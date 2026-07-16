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
from typing import Any

from analyzer_ng.amqp.models import (
    AnalysisResult,
    BulkResponse,
    ClusterResult,
    DefectUpdate,
    DeleteLaunchesRequest,
    DeleteLogsRequest,
    DeleteTestItemsRequest,
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
