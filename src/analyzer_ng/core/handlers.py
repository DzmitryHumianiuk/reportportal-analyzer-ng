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
from datetime import UTC, datetime
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
from analyzer_ng.core.ingest import IndexPipeline
from analyzer_ng.db.repositories import (
    LabelEventIn,
    PgDrain3StateStore,
    PgKBStore,
    PgLabelStore,
    PgRetrievalStore,
    PgStatsStore,
)
from analyzer_ng.db.repositories.protocols import KBStore, LabelStore, RetrievalStore

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

    def index_suggest_info(self, items: list[SuggestAnalysisResult]) -> dict[str, Any]:
        json_str = json.dumps([item.model_dump() for item in items])
        logger.warning("Deprecated 'index_suggest_info' route called with: " + json_str)
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
        self._retrieval: RetrievalStore | None = None
        self._kb: KBStore | None = None
        self._label: LabelStore | None = None
        self._pipeline: IndexPipeline | None = None

    def bind(
        self,
        pool: ConnectionPool,
        *,
        embedder: object | None = None,
        emb_model_ver: int = 0,
        max_logs: int = 20,
    ) -> None:
        """Attach the store layer once the PostgreSQL pool is open (spec 01 §6)."""
        retrieval = PgRetrievalStore(pool)
        self._retrieval = retrieval
        self._kb = PgKBStore(pool)
        self._label = PgLabelStore(pool)
        self._pipeline = IndexPipeline(
            retrieval,
            PgStatsStore(pool),
            PgDrain3StateStore(pool),
            embedder=embedder,
            emb_model_ver=emb_model_ver,
            max_logs=max_logs,
        )

    # -- index ------------------------------------------------------------- #
    def index(self, launches: list[Launch]) -> BulkResponse:
        if self._pipeline is None:
            return super().index(launches)
        return self._pipeline.index_launches(launches)

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

        # 3. Reply with the ids not found in analyzer storage (spec §4.5 step 3).
        return not_updated
