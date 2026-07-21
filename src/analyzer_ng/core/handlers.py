"""Route handlers — Phase 1 stubs (spec 01 §4.4).

One method per routing key. Each returns a **spec-correct empty / no-op**
response of the right shape; Phase 2 replaces the bodies with the real pipeline
without touching the transport or the routing table. Deprecated keys log a WARN
that mirrors the legacy text and return the legacy no-op reply.

Business logic lives here; serialization and the routing-key table live in
``analyzer_ng.amqp.dispatcher`` so handler registration stays cleanly separated
from transport.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from psycopg_pool import ConnectionPool

from analyzer_ng.amqp.models import (
    AnalysisResult,
    BulkResponse,
    ClusterResult,
    DefectUpdate,
    DeleteLaunchesRequest,
    DeleteLogsRequest,
    DeleteTestItemsRequest,
    ItemUpdate,
    Launch,
    LaunchInfoForClustering,
    RemoveByDatesRequest,
    SearchLogInfo,
    SearchLogs,
    SuggestAnalysisResult,
    SuggestPattern,
    SuggestPatternLabel,  # noqa: F401  (re-exported for Phase 2 convenience)
    TestItemInfo,
    TrainInfo,
)
from analyzer_ng.core import observability as obs
from analyzer_ng.core.analysis import AnalysisEngine
from analyzer_ng.core.ingest import IndexPipeline
from analyzer_ng.db.repositories import (
    LabelEventIn,
    PgDrain3StateStore,
    PgKBStore,
    PgLabelStore,
    PgRetrievalStore,
    PgStatsStore,
)
from analyzer_ng.db.repositories.protocols import KBStore, LabelStore
from analyzer_ng.ml.artifacts import PgModelStore
from analyzer_ng.ml.gate import make_ship_gate
from analyzer_ng.ml.retrain import (
    REASON_EVENTS,
    REASON_ROUTE,
    Retrainer,
    RetrainScheduler,
)
from analyzer_ng.ml.serving import GbmPredictor
from analyzer_ng.seeds.loader import SeedKB

logger = logging.getLogger(__name__)


class StubHandlers:
    """Phase 1 no-op handlers.

    Every implemented key returns an empty-but-valid response; every deprecated
    key WARN-logs and returns the legacy reply. ``None`` means "publish no reply"
    (spec §3.4 / §4.4).
    """

    # -- Implemented keys (empty responses; Phase 2 fills them) ------------- #
    def index(self, launches: list[Launch]) -> BulkResponse:
        return BulkResponse(took=0, errors=False)

    def analyze(self, launches: list[Launch]) -> list[AnalysisResult]:
        return []

    def suggest(self, info: TestItemInfo) -> list[SuggestAnalysisResult]:
        return []

    def cluster(self, info: LaunchInfoForClustering) -> ClusterResult:
        return ClusterResult(project=info.project, launchId=info.launch.launchId, clusters=[])

    def search(self, request: SearchLogs) -> list[SearchLogInfo]:
        return []

    def delete(self, project: int) -> int:
        return 0

    def clean(self, request: DeleteLogsRequest) -> int:
        return 0

    def item_remove(self, request: DeleteTestItemsRequest) -> int:
        return 0

    def launch_remove(self, request: DeleteLaunchesRequest) -> int:
        return 0

    def remove_by_launch_start_time(self, request: RemoveByDatesRequest) -> int:
        return 0

    def remove_by_log_time(self, request: RemoveByDatesRequest) -> int:
        return 0

    def defect_update(self, request: DefectUpdate) -> list[int]:
        # Feedback sink is Phase 2; with no storage yet, every referenced item is
        # "not found" — the spec's reply is the list of not-updated ids.
        return [int(item_id) for item_id in request.itemsToUpdate]

    def train_models(self, info: TrainInfo) -> None:
        # No reply, ever (spec §4.4). Real GBM retrain lands in Phase 2.
        return None

    def suggest_patterns(self, project: int) -> SuggestPattern:
        return SuggestPattern()

    # -- No-op-with-WARN keys ---------------------------------------------- #
    def namespace_finder(self, launches: list[Launch]) -> None:
        logger.warning(
            "No-op 'namespace_finder' route called (obsolete: OpenSearch namespace "
            "statistics removed); acking without reply"
        )
        return None

    def index_suggest_info(self, items: Any) -> dict[str, Any]:
        # Deprecated: mirror legacy exactly — WARN and reply {} for ANY payload.
        # The body is parsed leniently (identity) upstream so a malformed shape can
        # never raise into the transport (no DLQ-without-reply); we defensively
        # stringify whatever arrived for the WARN (spec 01 §4.4).
        try:
            rendered = json.dumps(items, default=str)
        except (TypeError, ValueError):
            rendered = repr(items)
        logger.warning("Deprecated 'index_suggest_info' route called with: " + rendered)
        return {}

    def remove_suggest_info(self, value: int) -> int:
        logger.warning(f"Deprecated 'remove_suggest_info' route called with: {value}")
        return value

    def update_suggest_info(self, payload: Any) -> int:
        logger.warning(f"Deprecated 'update_suggest_info' route called with: {json.dumps(payload)}")
        return 1

    def remove_models(self, payload: Any) -> None:
        logger.warning(f"Deprecated 'remove_models' route called with {json.dumps(payload)}")
        return None

    def get_model_info(self, payload: Any) -> None:
        logger.warning(f"Deprecated 'get_model_info' route called with {json.dumps(payload)}")
        return None

    # -- Test utility keys (kept from legacy) ------------------------------ #
    def noop_sleep(self, seconds: Any) -> None:
        time.sleep(float(seconds))
        return None

    def noop_echo(self, payload: Any) -> Any:
        return payload

    def noop_fail(self, payload: Any) -> None:
        raise RuntimeError(f"Intentional failure for testing purposes: {payload}")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 date/datetime; naive values are treated as UTC."""
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class PipelineHandlers(StubHandlers):
    """Real index + maintenance handlers over the spec-02 stores (T2.2).

    Analysis routes (``analyze``/``suggest``/``cluster``/``search``/
    ``train_models``/``suggest_patterns``) intentionally inherit the
    :class:`StubHandlers` no-op bodies — they are T2.3's job. Every real method
    falls back to the stub when the stores are unbound, so the default
    (store-less) :class:`Dispatcher` used by the unit suite keeps its documented
    empty/no-op behavior.
    """

    def __init__(self) -> None:
        self._retrieval: PgRetrievalStore | None = None
        self._kb: KBStore | None = None
        self._label: LabelStore | None = None
        self._pipeline: IndexPipeline | None = None
        self._stats: PgStatsStore | None = None
        self._engine: AnalysisEngine | None = None
        self._seed_kb: SeedKB | None = None
        self._emb_tag = "none"
        self._model_store: PgModelStore | None = None
        self._predictor: GbmPredictor | None = None
        self._retrainer: Retrainer | None = None
        self._retrain_scheduler: RetrainScheduler | None = None
        # Optional LLM sidecar (spec 04) + its feature-time extractor lookup; wired
        # by the service in ``bind`` when ANALYZER_LLM_ENABLED. Absent ⇒ engine runs
        # byte-identical to a build without the sidecar.
        self._sidecar: object | None = None
        self._extractor_features: object | None = None
        self._judge_tau: float = 0.75
        # Operator-tunable decision/pipeline knobs (spec 01 §5.2). Defaults equal the
        # code constants so an unbound / defaulted engine is byte-identical.
        self._engine_tunables: dict[str, float | int] = {}

    def bind(
        self,
        pool: ConnectionPool,
        *,
        embedder: object | None = None,
        emb_model_ver: int = 0,
        emb_model_tag: str = "none",
        max_logs: int = 20,
        drain_max_lines: int = 40,
        seed_kb: SeedKB | None = None,
        sidecar: object | None = None,
        extractor_features: object | None = None,
        judge_tau: float = 0.75,
        engine_tunables: dict[str, float | int] | None = None,
        retrain_debounce_s: int | None = None,
    ) -> None:
        """Attach the store layer once the PostgreSQL pool is open (spec 01 §6)."""
        self._sidecar = sidecar
        self._extractor_features = extractor_features
        self._judge_tau = judge_tau
        self._engine_tunables = dict(engine_tunables or {})
        retrieval = PgRetrievalStore(pool)
        self._retrieval = retrieval
        kb = PgKBStore(pool)
        self._kb = kb
        label = PgLabelStore(pool)
        self._label = label
        stats = PgStatsStore(pool)
        self._stats = stats
        self._pipeline = IndexPipeline(
            retrieval,
            stats,
            PgDrain3StateStore(pool),
            embedder=embedder,
            emb_model_ver=emb_model_ver,
            max_logs=max_logs,
            drain_max_lines=drain_max_lines,
        )
        self._emb_tag = emb_model_tag
        if seed_kb is not None:
            self._seed_kb = seed_kb
        # Learning loop (T3.1): artifact store + atomic-swap GBM serving + retrainer.
        # The predictor lazily loads the active model on first predict; the retrainer
        # is driven by the train_models route, the defect_update counter, and the
        # service's nightly timer (spec §6.5).
        self._model_store = PgModelStore(pool)
        self._predictor = GbmPredictor(self._model_store)
        # The ship gate (T3.2) closes the loop: a retrained candidate replaces the
        # active model only if it holds up on the chronological eval slice (§10.2).
        retrainer_kwargs: dict[str, object] = {"gate": make_ship_gate(self._model_store)}
        if retrain_debounce_s is not None:
            retrainer_kwargs["min_interval"] = timedelta(seconds=retrain_debounce_s)
        self._retrainer = Retrainer(
            label,
            self._model_store,
            self._predictor,
            **retrainer_kwargs,  # type: ignore[arg-type]
        )
        # Single-flight background runner: triggers (defect_update counter, route,
        # nightly) enqueue here so the heavy fetch+train+ship never blocks an AMQP
        # worker thread (review follow-up Important #2). Idempotent: a second bind()
        # (e.g. set_pg_pool called again) must stop the previous scheduler thread
        # first so we never leak a second daemon runner.
        if self._retrain_scheduler is not None:
            self._retrain_scheduler.stop()
        self._retrain_scheduler = RetrainScheduler(self._retrainer)
        self._retrain_scheduler.start()
        self._build_engine(retrieval, kb, stats)

    def set_seed_kb(self, seed_kb: SeedKB) -> None:
        """Bind the loaded seed KB (spec 01 §6 step 6, after the pool is open)."""
        self._seed_kb = seed_kb
        if self._retrieval is not None and self._kb is not None and self._stats is not None:
            self._build_engine(self._retrieval, self._kb, self._stats)

    @property
    def retrainer(self) -> Retrainer | None:
        """The learning-loop retrainer (driven by the service's nightly timer)."""
        return self._retrainer

    def request_retrain(self, reason: str) -> None:
        """Non-blocking retrain request routed through the single-flight scheduler."""
        if self._retrain_scheduler is not None:
            self._retrain_scheduler.request(reason)

    def shutdown(self) -> None:
        """Stop the background retrain scheduler (called on service shutdown)."""
        if self._retrain_scheduler is not None:
            self._retrain_scheduler.stop()

    @property
    def stats(self) -> PgStatsStore | None:
        """The StatsStore (drives the nightly metrics_daily rollup, spec §10.3)."""
        return self._stats

    @property
    def retrieval(self) -> PgRetrievalStore | None:
        """The RetrievalStore (drives the nightly label_event orphan reaper)."""
        return self._retrieval

    @property
    def label(self) -> LabelStore | None:
        """The LabelStore (drives the cold-project check for the LLM sidecar)."""
        return self._label

    def gbm_version(self) -> str | None:
        """Version string of the currently served GBM (None when cold)."""
        return self._predictor.active_version() if self._predictor is not None else None

    def _build_engine(
        self, retrieval: PgRetrievalStore, kb: KBStore, stats: object
    ) -> None:
        assert self._pipeline is not None
        self._engine = AnalysisEngine(
            retrieval=retrieval,
            kb=kb,
            stats=stats,
            pipeline=self._pipeline,
            seed_kb=self._seed_kb,
            emb_model_tag=self._emb_tag,
            predictor=self._predictor,
            sidecar=self._sidecar,
            extractor_features=self._extractor_features,  # type: ignore[arg-type]
            judge_tau=self._judge_tau,
            **self._engine_tunables,  # type: ignore[arg-type]
        )

    # -- index ------------------------------------------------------------- #
    def index(self, launches: list[Launch]) -> BulkResponse:
        if self._pipeline is None:
            return super().index(launches)
        return self._pipeline.index_launches(launches)

    # -- analysis routes (T2.3) -------------------------------------------- #
    def analyze(self, launches: list[Launch]) -> list[AnalysisResult]:
        if self._engine is None:
            return super().analyze(launches)
        if launches:
            obs.set_project(int(launches[0].project))
        return self._engine.analyze(launches)

    def suggest(self, info: TestItemInfo) -> list[SuggestAnalysisResult]:
        if self._engine is None:
            return super().suggest(info)
        obs.set_project(int(info.project))
        return self._engine.suggest(info)

    def cluster(self, info: LaunchInfoForClustering) -> ClusterResult:
        if self._engine is None:
            return super().cluster(info)
        obs.set_project(int(info.project))
        return self._engine.cluster(info)

    def search(self, request: SearchLogs) -> list[SearchLogInfo]:
        if self._engine is None:
            return super().search(request)
        obs.set_project(int(request.projectId))
        return self._engine.search(request)

    # -- train_models (real retrain entrypoint, spec §6.5) ----------------- #
    def train_models(self, info: TrainInfo) -> None:
        """Trigger a retrain (debounced ≥ 1/hour). No reply, ever (spec §4.4).

        This is the real learning-loop entrypoint: an operator/RP-issued
        ``train_models`` message enqueues a retrain on the single-flight scheduler
        and returns immediately (no reply). Training reads only stored feature
        snapshots and ships atomically when it beats the active model via the eval
        gate; a cold install (< 50 events) is a logged no-op.
        """
        if self._retrain_scheduler is None:
            return super().train_models(info)
        logger.info("train_models: scheduling background retrain")
        self._retrain_scheduler.request(REASON_ROUTE)
        return None

    # -- deletions --------------------------------------------------------- #
    def delete(self, project: int) -> int:
        if self._retrieval is None:
            return super().delete(project)
        obs.set_project(int(project))
        return self._retrieval.delete_project(int(project))

    def clean(self, request: DeleteLogsRequest) -> int:
        if self._retrieval is None:
            return super().clean(request)
        obs.set_project(int(request.project))
        # analyzer-ng stores logs aggregated into failure_signature at item
        # granularity (spec 02) — there is no per-log row keyed by log id to
        # delete, so the legacy-shaped count is 0. (spec 02 wins over the log-id
        # phrasing in spec 01 §4.4.)
        logger.info(
            "clean: %d log id(s) for project %s — no per-log storage, nothing to delete",
            len(request.ids),
            request.project,
        )
        return 0

    def item_remove(self, request: DeleteTestItemsRequest) -> int:
        if self._retrieval is None:
            return super().item_remove(request)
        project = int(request.project)
        obs.set_project(project)
        return self._retrieval.delete_items(project, [int(i) for i in request.itemsToDelete])

    def launch_remove(self, request: DeleteLaunchesRequest) -> int:
        if self._retrieval is None:
            return super().launch_remove(request)
        project = int(request.project)
        obs.set_project(project)
        return self._retrieval.delete_launches(project, [int(i) for i in request.launch_ids])

    def remove_by_launch_start_time(self, request: RemoveByDatesRequest) -> int:
        return self._remove_by_dates(request, "start_time")

    def remove_by_log_time(self, request: RemoveByDatesRequest) -> int:
        return self._remove_by_dates(request, "log_time")

    def _remove_by_dates(
        self, request: RemoveByDatesRequest, field: Literal["start_time", "log_time"]
    ) -> int:
        if self._retrieval is None:
            return (
                super().remove_by_launch_start_time(request)
                if field == "start_time"
                else super().remove_by_log_time(request)
            )
        project = int(request.project)
        obs.set_project(project)
        start = _parse_iso(request.interval_start_date)
        end = _parse_iso(request.interval_end_date)
        return self._retrieval.delete_by_time_range(project, field, before=end, after=start)

    # -- defect_update (primary feedback source, spec 01 §4.5) ------------- #
    def defect_update(self, request: DefectUpdate) -> list[int]:
        if self._retrieval is None or self._label is None:
            return super().defect_update(request)
        project = int(request.project)
        obs.set_project(project)

        # 1. Normalize every entry to (item_id, issue_type.lower()).
        normalized: list[tuple[int, str]] = []
        for raw_id, value in request.itemsToUpdate.items():
            issue_type = (
                value.issueType if isinstance(value, ItemUpdate) else str(value)
            ).strip().lower()
            normalized.append((int(raw_id), issue_type))

        existing = self._retrieval.get_items_labels(project, [item_id for item_id, _ in normalized])
        not_updated: list[int] = []
        for item_id, issue_type in normalized:
            if item_id not in existing:
                not_updated.append(item_id)
                continue
            # 2. Append-only label_event + current-label overwrite + purity + outcomes.
            # These run as separate store transactions (not one atomic unit): the
            # label_event log is append-only and the current-label/purity/outcome
            # updates are individually idempotent, so a partial failure re-converges
            # on the next defect_update — full cross-store atomicity is not required
            # for this feedback signal.
            old_label = existing[item_id]
            self._retrieval.update_issue_type(project, item_id, issue_type, is_auto=False)
            self._label.append_event(
                LabelEventIn(
                    project_id=project,
                    item_id=item_id,
                    old_label=old_label,
                    new_label=issue_type,
                    source="rp_defect_update",
                )
            )
            if self._kb is not None:
                mode_id = self._kb.member_mode_id(project, item_id)
                if mode_id is not None:
                    self._kb.update_purity(project, mode_id)
            self._retrieval.record_feedback_outcome(project, item_id, issue_type)

        # 4. Bump the retrain counter (spec §6.5): a defect_update appended new
        # label_events, so signal the 100-new-events trigger. The actual train runs
        # on the single-flight background scheduler (debounced ≥ 1/hour) so this
        # feedback handler returns immediately without blocking an AMQP worker on a
        # full fetch+train+ship (review follow-up Important #2).
        if self._retrain_scheduler is not None:
            self._retrain_scheduler.request(REASON_EVENTS)

        # 5. Reply with the ids not found in analyzer storage (spec §4.5 step 3).
        return not_updated
