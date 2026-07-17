"""Wire-model fidelity tests (spec 01 §4.2).

Every request/response field name and default is copied verbatim from spec 01's
tables (verified against legacy ``app/commons/model/launch_objects.py``). UI and
RP-backend compatibility depend on exact field names, so these tests pin the
schema: a rename or a changed default is a wire-format regression.
"""

from __future__ import annotations

from analyzer_ng.amqp import models


def test_error_logging_level_constant() -> None:
    assert models.ERROR_LOGGING_LEVEL == 40000


def test_analyzer_conf_defaults() -> None:
    conf = models.AnalyzerConf()
    assert conf.analyzerMode == "ALL"
    assert conf.minShouldMatch == 80
    assert conf.numberOfLogLines == -1
    assert conf.isAutoAnalyzerEnabled is True
    assert conf.indexingRunning is True
    assert conf.allMessagesShouldMatch is False
    assert conf.searchLogsMinShouldMatch == 95
    assert conf.uniqueErrorsMinShouldMatch == 95
    assert conf.numberOfLogsToIndex == 20
    assert conf.minimumLogLevel == models.ERROR_LOGGING_LEVEL
    assert conf.similarityThresholdToDrop == 0.95
    assert conf.searchScoreMode == "avg"


def test_log_defaults_and_timestamp7() -> None:
    log = models.Log(logId=1, message="boom")
    assert log.logLevel == 0
    assert log.clusterId == 0
    assert log.clusterMessage == ""
    assert isinstance(log.logTime, tuple)
    assert len(log.logTime) == 7


def test_test_item_fields() -> None:
    item = models.TestItem(testItemId=5, isAutoAnalyzed=False)
    assert item.uniqueId == ""
    assert item.issueType == ""
    assert item.originalIssueType == ""
    assert item.testCaseHash == 0
    assert item.endTime is None
    assert item.lastModified is None
    assert item.description is None
    assert item.linksToBts == []
    assert item.logs == []


def test_launch_fields() -> None:
    launch = models.Launch(launchId=1, project=2)
    assert launch.launchName == ""
    assert launch.launchNumber == 0
    assert launch.previousLaunchId == 0
    assert isinstance(launch.analyzerConfig, models.AnalyzerConf)
    assert launch.testItems == []
    assert launch.clusters == {}


def test_test_item_info_fields() -> None:
    info = models.TestItemInfo(launchId=1, project=2)
    assert info.testItemId == 0
    assert info.clusterId == 0
    assert info.logs == []
    assert isinstance(info.analyzerConfig, models.AnalyzerConf)


def test_launch_info_for_clustering() -> None:
    launch = models.Launch(launchId=1, project=2)
    linfo = models.LaunchInfoForClustering(launch=launch, project=2, numberOfLogLines=5)
    assert linfo.forUpdate is False
    assert linfo.cleanNumbers is False


def test_search_logs_fields() -> None:
    search = models.SearchLogs(
        launchId=1,
        launchName="L",
        itemId=2,
        projectId=3,
        filteredLaunchIds=[1, 2],
        logMessages=["m"],
        logLines=5,
    )
    assert isinstance(search.analyzerConfig, models.AnalyzerConf)


def test_defect_update_accepts_str_and_itemupdate() -> None:
    payload = {
        "project": 1,
        "itemsToUpdate": {
            "123": "pb001",
            "456": {
                "issueType": "ab001",
                "issueComment": "x",
                "timestamp": [2026, 7, 14, 12, 0, 0, 0],
            },
        },
    }
    upd = models.DefectUpdate(**payload)
    # Union-keyed dict keeps the JSON string keys (legacy parity); handlers
    # normalize them to ints. String and ItemUpdate values both round-trip.
    assert upd.itemsToUpdate["123"] == "pb001"
    assert isinstance(upd.itemsToUpdate["456"], models.ItemUpdate)
    assert upd.itemsToUpdate["456"].issueType == "ab001"


def test_delete_launches_snake_case_wire_field() -> None:
    req = models.DeleteLaunchesRequest(project=1, launch_ids=[1, 2])
    assert req.launch_ids == [1, 2]


def test_remove_by_dates_snake_case_wire_fields() -> None:
    req = models.RemoveByDatesRequest(
        project=1, interval_start_date="2026-01-01", interval_end_date="2026-02-01"
    )
    assert req.interval_start_date == "2026-01-01"


def test_train_info_model_type_enum() -> None:
    info = models.TrainInfo(model_type=models.ModelType.suggestion, project=7)
    assert info.model_type is models.ModelType.suggestion
    assert info.additional_projects is None
    assert info.gathered_metric_total == 0


def test_train_info_accepts_wire_int_model_type() -> None:
    # The chart-era RP publishes ``train_models`` with ``model_type`` as an INT enum
    # (1=defect_type, 2=suggestion, 3=auto_analysis) — the wire model must accept it
    # so the message is handled, not silently DLQ'd (live-fix wire note).
    for wire_int, expected in (
        (1, models.ModelType.defect_type),
        (2, models.ModelType.suggestion),
        (3, models.ModelType.auto_analysis),
    ):
        info = models.TrainInfo.model_validate({"model_type": wire_int, "project": 7})
        assert info.model_type is expected


def test_analysis_result_fields() -> None:
    res = models.AnalysisResult(testItem=1, issueType="pb001", relevantItem=2)
    assert res.model_dump() == {"testItem": 1, "issueType": "pb001", "relevantItem": 2}


def test_suggest_analysis_result_has_all_ui_fields() -> None:
    required = {
        "project",
        "testItem",
        "testItemLogId",
        "launchId",
        "launchName",
        "launchNumber",
        "issueType",
        "relevantItem",
        "relevantLogId",
        "isMergedLog",
        "matchScore",
        "resultPosition",
        "esScore",
        "esPosition",
        "modelFeatureNames",
        "modelFeatureValues",
        "modelInfo",
        "usedLogLines",
        "minShouldMatch",
        "processedTime",
        "userChoice",
        "methodName",
        "clusterId",
    }
    assert set(models.SuggestAnalysisResult.model_fields) == required


def test_bulk_response_defaults() -> None:
    resp = models.BulkResponse(took=3, errors=False)
    assert resp.items == []
    assert resp.logResults == []
    assert resp.status == 0


def test_cluster_result_and_info() -> None:
    ci = models.ClusterInfo(clusterId=1, clusterMessage="m", logIds=[1], itemIds=[2])
    cr = models.ClusterResult(project=1, launchId=2, clusters=[ci])
    assert cr.clusters[0].clusterMessage == "m"


def test_suggest_pattern_defaults() -> None:
    sp = models.SuggestPattern()
    assert sp.suggestionsWithLabels == []
    assert sp.suggestionsWithoutLabels == []
