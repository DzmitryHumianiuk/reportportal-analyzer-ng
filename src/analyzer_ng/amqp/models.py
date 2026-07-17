"""AMQP wire models (spec 01 §4.2).

Every field name and default is copied **verbatim** from spec 01's tables, which
were in turn verified against the legacy ``app/commons/model/launch_objects.py``
and ``app/commons/model/ml.py``. The ReportPortal backend and UI depend on these
exact names — do not rename any wire-visible field. Phase 2 fills the handlers;
this module only pins the schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import Enum, auto

from pydantic import BaseModel, ConfigDict, Field

# ERROR log level threshold (RP convention: only ERROR+ logs are analyzed).
ERROR_LOGGING_LEVEL: int = 40000

# RP sends time as a 7-tuple [Y, M, D, h, m, s, weekday].
timestamp7 = tuple[int, int, int, int, int, int, int]


def timestamp_factory() -> timestamp7:
    """Default factory for ``timestamp7`` fields: the current UTC time tuple.

    UTC (not local time) so timestamps are stable and comparable regardless of the
    container's TZ — a naive local tuple would drift the wire value by the offset.
    """
    now = datetime.now(UTC).timetuple()
    return now[0], now[1], now[2], now[3], now[4], now[5], now[6]


class ModelType(Enum):
    """ML model kinds (legacy ``app/commons/model/ml.py``)."""

    defect_type = auto()
    suggestion = auto()
    auto_analysis = auto()


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class AnalyzerConf(BaseModel):
    """Per-launch analyzer configuration."""

    analyzerMode: str = "ALL"
    minShouldMatch: int = 80
    numberOfLogLines: int = -1
    isAutoAnalyzerEnabled: bool = True
    indexingRunning: bool = True
    allMessagesShouldMatch: bool = False
    searchLogsMinShouldMatch: int = 95
    uniqueErrorsMinShouldMatch: int = 95
    numberOfLogsToIndex: int = 20
    minimumLogLevel: int = ERROR_LOGGING_LEVEL
    similarityThresholdToDrop: float = 0.95
    searchScoreMode: str = "avg"


class Log(BaseModel):
    """A single log line attached to a test item."""

    logId: int
    logLevel: int = 0
    logTime: timestamp7 = Field(default_factory=timestamp_factory)
    message: str
    clusterId: int = 0
    clusterMessage: str = ""


class TestItem(BaseModel):
    """A failed test item with its logs."""

    testItemId: int
    isAutoAnalyzed: bool
    uniqueId: str = ""
    issueType: str = ""
    issueDescription: str = ""
    originalIssueType: str = ""
    startTime: timestamp7 = Field(default_factory=timestamp_factory)
    endTime: list[int] | None = None
    lastModified: list[int] | None = None
    testCaseHash: int = 0
    testItemName: str = ""
    description: str | None = None
    linksToBts: list[str] = []
    logs: list[Log] = []


class Launch(BaseModel):
    """A launch carrying test items (``index`` / ``analyze`` / ``namespace_finder``)."""

    launchId: int
    project: int
    launchName: str = ""
    launchNumber: int = 0
    previousLaunchId: int = 0
    launchStartTime: timestamp7 = Field(default_factory=timestamp_factory)
    analyzerConfig: AnalyzerConf = AnalyzerConf()
    testItems: list[TestItem] = []
    clusters: dict = {}


class TestItemInfo(BaseModel):
    """``suggest`` request payload."""

    testItemId: int = 0
    uniqueId: str = ""
    testCaseHash: int = 0
    clusterId: int = 0
    launchId: int
    launchName: str = ""
    launchNumber: int = 0
    previousLaunchId: int = 0
    testItemName: str = ""
    project: int
    analyzerConfig: AnalyzerConf = AnalyzerConf()
    logs: list[Log] = []


class LaunchInfoForClustering(BaseModel):
    """``cluster`` request payload."""

    launch: Launch
    project: int
    forUpdate: bool = False
    numberOfLogLines: int
    cleanNumbers: bool = False


class SearchLogs(BaseModel):
    """``search`` request payload."""

    launchId: int
    launchName: str
    itemId: int
    projectId: int
    filteredLaunchIds: list[int]
    logMessages: list[str]
    analyzerConfig: AnalyzerConf = AnalyzerConf()
    logLines: int


class ItemUpdate(BaseModel):
    """A single label change inside a ``defect_update`` payload (new backends)."""

    timestamp: timestamp7 = Field(default_factory=timestamp_factory)
    issueType: str
    issueComment: str = ""


class DefectUpdate(BaseModel):
    """``defect_update`` request payload — the primary human-feedback signal.

    Values are either a bare issue-type locator string (old backends) or an
    :class:`ItemUpdate` object (new backends); accept both.
    """

    project: int | str
    itemsToUpdate: dict[int | str, str | ItemUpdate]


class DeleteLogsRequest(BaseModel):
    """``clean`` request payload."""

    ids: list[int]
    project: int


class DeleteTestItemsRequest(BaseModel):
    """``item_remove`` request payload."""

    project: int | str
    itemsToDelete: list[int | str]


class DeleteLaunchesRequest(BaseModel):
    """``launch_remove`` request payload.

    ``launch_ids`` is snake_case **on the wire** — keep it.
    """

    project: int | str
    launch_ids: list[int | str]


class RemoveByDatesRequest(BaseModel):
    """``remove_by_launch_start_time`` / ``remove_by_log_time`` request payload.

    ``interval_start_date`` / ``interval_end_date`` are snake_case on the wire.
    """

    project: int | str
    interval_start_date: str
    interval_end_date: str


class TrainInfo(BaseModel):
    """``train_models`` request payload."""

    # ``model_type`` collides with pydantic's protected ``model_`` namespace;
    # the field name is wire-visible legacy, so disable the guard rather than
    # rename it.
    model_config = ConfigDict(protected_namespaces=())

    model_type: ModelType
    project: int
    additional_projects: Iterable[int] | None = None
    gathered_metric_total: int = 0


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class AnalysisResult(BaseModel):
    """``analyze`` reply element."""

    testItem: int
    issueType: str
    relevantItem: int


class SearchLogInfo(BaseModel):
    """``search`` reply element."""

    logId: int
    testItemId: int
    matchScore: float


class ClusterInfo(BaseModel):
    """One cluster inside a :class:`ClusterResult`."""

    clusterId: int
    clusterMessage: str
    logIds: list[int]
    itemIds: list[int]


class ClusterResult(BaseModel):
    """``cluster`` reply."""

    project: int
    launchId: int
    clusters: list[ClusterInfo]


class SuggestAnalysisResult(BaseModel):
    """``suggest`` reply element — all fields are required by the RP UI."""

    project: int
    testItem: int
    testItemLogId: int
    launchId: int
    launchName: str
    launchNumber: int
    issueType: str
    relevantItem: int
    relevantLogId: int
    isMergedLog: bool = False
    matchScore: float
    resultPosition: int
    esScore: float | None = 0.0
    esPosition: int | None = None
    modelFeatureNames: str | None = None
    modelFeatureValues: str | None = None
    modelInfo: str | None = None
    usedLogLines: int
    minShouldMatch: int
    processedTime: float
    userChoice: int = 0
    methodName: str
    clusterId: int = 0


class LogExceptionResult(BaseModel):
    """Per-log extracted exception names (RP "unique errors")."""

    logId: int
    foundExceptions: list[str] = []


class BulkResponse(BaseModel):
    """``index`` reply."""

    took: int
    errors: bool
    items: list[str] = []
    logResults: list[LogExceptionResult] = []
    status: int = 0


class SuggestPatternLabel(BaseModel):
    """A single suggested pattern (with or without an associated label)."""

    pattern: str
    totalCount: int
    percentTestItemsWithLabel: float = 0.0
    label: str = ""


class SuggestPattern(BaseModel):
    """``suggest_patterns`` reply."""

    suggestionsWithLabels: list[SuggestPatternLabel] = []
    suggestionsWithoutLabels: list[SuggestPatternLabel] = []
