"""Index pipeline orchestration (spec 01 §4.4 ``index``; spec 03 §1-§3).

Turns an ``index`` request (a ``list[Launch]``) into persisted rows:

    filter ERROR+/near-dup/cap  ->  preprocess + Drain3 templates  ->  signature
      ->  (optional) embed  ->  upsert test_item + failure_signature
      ->  incremental test_history_stats

Drain3 is single-writer per project via the spec-02 CAS contract: state is loaded,
the batch is mined, and the snapshot is saved with an optimistic-concurrency check;
a lost race reloads, re-mines, and retries (bounded). Embedding is optional — when
no embedder is bound the index soft-degrades to lexical-only (``emb=NULL``,
``emb_model_ver=0``), as CONTEXT's risk note requires.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from analyzer_ng.amqp.models import (
    ERROR_LOGGING_LEVEL,
    BulkResponse,
    Launch,
    LogExceptionResult,
    TestItem,
)
from analyzer_ng.core.cancellation import raise_if_cancelled
from analyzer_ng.db.repositories.models import SignatureIn, TestItemIn
from analyzer_ng.db.repositories.protocols import Drain3StateStore, RetrievalStore, StatsStore
from analyzer_ng.ml.drain import DrainManager, load_manager, save_manager
from analyzer_ng.ml.hashing import to_signed64
from analyzer_ng.ml.signature import SignatureResult
from analyzer_ng.preprocessing import pipeline as pp

logger = logging.getLogger(__name__)

DRAIN_CAS_RETRIES = 3


@dataclass(frozen=True)
class ItemAnalysis:
    """An in-memory analysis of one item (signature + embedding, unpersisted).

    Produced by :meth:`IndexPipeline.build_item_analyses` for the analyze/suggest/
    cluster/search read paths — it computes signatures/embeddings without mutating
    persisted Drain3 state (analyze must not create templates).
    """

    launch: Launch
    item: TestItem
    signature: SignatureResult
    emb: list[float] | None
    emb_model_ver: int
    log_count: int
    start_time: datetime | None
    clean_msg: str  # unmasked cleaned primary text (seed matching + clusterMessage)


def _ts7_to_datetime(ts: object) -> datetime | None:
    """Convert an RP ``timestamp7`` ([Y,M,D,h,m,s,weekday]) to a UTC datetime."""
    if not isinstance(ts, (list, tuple)):
        return None
    try:
        parts = [int(x) for x in ts[:6]]
    except (TypeError, ValueError):
        return None
    if len(parts) < 6:
        return None
    try:
        return datetime(parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], tzinfo=UTC)
    except ValueError:
        return None


def _template_id(hash_hex: str) -> int:
    """Signed-bigint template id for a signature hex template hash (spec 02 §2)."""
    return to_signed64(int(hash_hex, 16))


class IndexPipeline:
    """Stateless-per-call ingest orchestrator over the spec-02 stores."""

    def __init__(
        self,
        retrieval: RetrievalStore,
        stats: StatsStore,
        drain_store: Drain3StateStore,
        *,
        embedder: object | None = None,
        emb_model_ver: int = 0,
        max_logs: int = 20,
        cas_retries: int = DRAIN_CAS_RETRIES,
    ) -> None:
        self._retrieval = retrieval
        self._stats = stats
        self._drain_store = drain_store
        self._embedder = embedder
        self._emb_model_ver = emb_model_ver
        self._max_logs = max_logs
        self._cas_retries = cas_retries

    # ------------------------------------------------------------------ #
    def index_launches(self, launches: list[Launch]) -> BulkResponse:
        started = time.monotonic()
        by_project: dict[int, list[tuple[Launch, TestItem]]] = defaultdict(list)
        for launch in launches:
            for item in launch.testItems:
                by_project[launch.project].append((launch, item))

        log_results: list[LogExceptionResult] = []
        errors = False
        for project_id, entries in by_project.items():
            raise_if_cancelled()
            try:
                self._index_project(project_id, entries, log_results)
            except Exception:  # noqa: BLE001 — one project's failure sets errors, others proceed
                logger.exception("index failed for project %s", project_id)
                errors = True

        took_ms = int((time.monotonic() - started) * 1000)
        return BulkResponse(
            took=took_ms, errors=errors, items=[], logResults=log_results, status=0
        )

    # ------------------------------------------------------------------ #
    def _index_project(
        self,
        project_id: int,
        entries: list[tuple[Launch, TestItem]],
        log_results: list[LogExceptionResult],
    ) -> None:
        items, sigs, stats_bumps = self._mine_and_build(project_id, entries)

        # spec 02 §2.10: test_item + failure_signature + test_history_stats commit in
        # ONE transaction, so a mid-write failure leaves nothing persisted for the
        # project (never items-without-signatures/stats). Drain3 state was already
        # saved above — its template mirror is idempotent/additive, so a rolled-back
        # DB write is re-converged by the next index of the same launch.
        with self._retrieval.transaction() as conn:
            self._retrieval.upsert_items(items, conn=conn)
            self._retrieval.upsert_signatures(sigs, conn=conn)
            for test_case_hash, ts in stats_bumps:
                # §2.10 records each index as a failure observation (incremental
                # upsert), so window_runs accumulates on a re-index of the same
                # launch. This is a deliberate reconciliation of §2.10 against
                # §8.4's "no-op rewrite" wording in favor of the more specific
                # §2.10 stats contract (the hot retrieval tables stay idempotent).
                self._stats.bump_test_history(project_id, test_case_hash, True, ts, conn=conn)

        # foundExceptions per ERROR+ log for the BulkResponse (Drain-independent).
        for _launch, item in entries:
            for log in item.logs:
                if log.logLevel >= ERROR_LOGGING_LEVEL and log.message.strip():
                    cleaned = pp.clean_log(log.message)
                    log_results.append(
                        LogExceptionResult(
                            logId=log.logId, foundExceptions=list(cleaned.exceptions)
                        )
                    )

    def _mine_and_build(
        self, project_id: int, entries: list[tuple[Launch, TestItem]]
    ) -> tuple[list[TestItemIn], list[SignatureIn], list[tuple[int, datetime]]]:
        """Drain-mine + build signatures for a project under the CAS contract.

        Re-runs on a lost CAS race (a fresh miner each attempt, so template hashes
        reflect the reloaded state), up to ``cas_retries`` times.
        """
        last_error: Exception | None = None
        for attempt in range(self._cas_retries):
            manager, version = load_manager(
                self._drain_store,
                project_id,
                template_loader=self._load_template_texts,
            )
            items: list[TestItemIn] = []
            sigs: list[SignatureIn] = []
            stats_bumps: list[tuple[int, datetime]] = []
            for launch, item in entries:
                raise_if_cancelled()
                item_in, sig_in, ts = self._process_item(manager, launch, item)
                items.append(item_in)
                sigs.append(sig_in)
                if item_in.test_case_hash is not None:
                    stats_bumps.append((item_in.test_case_hash, ts))

            hash_to_pattern = {
                row["template_id"]: row["pattern"] for row in manager.cluster_mirror_rows()
            }
            for sig in sigs:
                sig.tmpl_text = " ".join(
                    p for p in (hash_to_pattern.get(t, "") for t in sig.template_ids) if p
                )

            if save_manager(self._drain_store, project_id, manager, version):
                return items, sigs, stats_bumps
            last_error = RuntimeError("Drain3 CAS conflict")
            logger.warning(
                "Drain3 CAS conflict for project %s (attempt %d/%d); reloading and retrying",
                project_id,
                attempt + 1,
                self._cas_retries,
            )
        raise RuntimeError(
            f"Drain3 state persistence failed for project {project_id} "
            f"after {self._cas_retries} attempts"
        ) from last_error

    def _process_item(
        self, manager: DrainManager, launch: Launch, item: TestItem
    ) -> tuple[TestItemIn, SignatureIn, datetime]:
        logs = [pp.LogInput(message=log.message, log_level=log.logLevel) for log in item.logs]
        kept = pp.filter_item_logs(logs, max_logs=self._max_logs)
        result = pp.build_item_signature(item.testItemName, logs, manager, in_app_prefixes=None)

        issue_type = (item.issueType or "").strip().lower() or None
        test_case_hash = item.testCaseHash or None
        start_time = _ts7_to_datetime(item.startTime)
        log_times = [t for t in (_ts7_to_datetime(log.logTime) for log in item.logs) if t]
        log_time_max = max(log_times) if log_times else None

        emb: list[float] | None = None
        emb_ver = 0
        if self._embedder is not None and result.signature_text:
            emb = self._embedder.embed(result.signature_text).tolist()  # type: ignore[attr-defined]
            emb_ver = self._emb_model_ver

        item_in = TestItemIn(
            item_id=item.testItemId,
            project_id=launch.project,
            launch_id=launch.launchId,
            launch_name=launch.launchName,
            launch_number=launch.launchNumber,
            test_case_hash=test_case_hash,
            unique_id=item.uniqueId or None,
            item_name=item.testItemName,
            start_time=start_time,
            is_auto_analyzed=item.isAutoAnalyzed,
            issue_type=issue_type,
            log_count=len(kept),
            log_time_max=log_time_max,
        )
        sig_in = SignatureIn(
            project_id=launch.project,
            item_id=item.testItemId,
            exception_fp=result.exception_fp,
            error_hash=result.error_hash,
            top_frames=result.frames,
            template_ids=[_template_id(h) for h in result.template_hashes],
            exc_text=" ".join(result.exc_classes),
            msg_text=result.msg_text,
            frames_text=" ".join(result.frames),
            tmpl_text="",  # filled from the miner's live patterns in _mine_and_build
            status_codes=list(result.status_codes),
            emb=emb,
            emb_model_ver=emb_ver,
        )
        ts = log_time_max or start_time or datetime.now(UTC)
        return item_in, sig_in, ts

    def _load_template_texts(self, project_id: int) -> list[str]:
        loader = getattr(self._drain_store, "load_template_texts", None)
        return list(loader(project_id)) if loader is not None else []

    # ------------------------------------------------------------------ #
    # Read-path signature building (analyze/suggest/cluster/search, T2.3)
    # ------------------------------------------------------------------ #
    def build_item_analyses(
        self, project_id: int, entries: list[tuple[Launch, TestItem]]
    ) -> list[ItemAnalysis]:
        """Build signatures + embeddings for a batch **without persisting**.

        Loads the project's Drain3 state read-only (never ``save_manager``), so the
        analyze/suggest paths mine template *hashes* deterministically (content
        hashes, so they align with indexed signatures) without creating new
        persisted templates.
        """
        manager, _version = load_manager(
            self._drain_store, project_id, template_loader=self._load_template_texts
        )
        out: list[ItemAnalysis] = []
        for launch, item in entries:
            raise_if_cancelled()
            out.append(self._build_one(manager, launch, item))
        return out

    def _build_one(self, manager: DrainManager, launch: Launch, item: TestItem) -> ItemAnalysis:
        logs = [pp.LogInput(message=log.message, log_level=log.logLevel) for log in item.logs]
        kept = pp.filter_item_logs(logs, max_logs=self._max_logs)
        result = pp.build_item_signature(item.testItemName, logs, manager, in_app_prefixes=None)
        clean_msg = "\n".join(pp.clean_log(m).msg for m in kept if m.strip())

        emb: list[float] | None = None
        emb_ver = 0
        if self._embedder is not None and result.signature_text:
            emb = self._embedder.embed(result.signature_text).tolist()  # type: ignore[attr-defined]
            emb_ver = self._emb_model_ver
        return ItemAnalysis(
            launch=launch,
            item=item,
            signature=result,
            emb=emb,
            emb_model_ver=emb_ver,
            log_count=len(kept),
            start_time=_ts7_to_datetime(item.startTime),
            clean_msg=clean_msg,
        )

    def template_id(self, hash_hex: str) -> int:
        """Public accessor for the stable signed-bigint template id (spec 02 §2)."""
        return _template_id(hash_hex)
