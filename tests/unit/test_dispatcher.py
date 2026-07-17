"""Routing-table disposition tests (spec 01 §4.3–§4.4).

Exercises :class:`Dispatcher` in-process (no broker) to pin every key's
adapter/serializer wiring and no-op behavior against the spec table.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from analyzer_ng.amqp.dispatcher import Dispatcher, UnknownRoutingKey
from analyzer_ng.amqp.models import AnalysisResult, BulkResponse, SuggestPattern

LAUNCH = {"launchId": 1, "project": 2, "testItems": []}


@pytest.fixture
def dispatcher() -> Dispatcher:
    return Dispatcher()


def test_index_returns_bulk_response_json(dispatcher: Dispatcher) -> None:
    reply = dispatcher.process("index", [LAUNCH])
    assert reply is not None
    BulkResponse.model_validate_json(reply)  # validates against §4.2


def test_analyze_returns_json_array(dispatcher: Dispatcher) -> None:
    reply = dispatcher.process("analyze", [LAUNCH])
    assert json.loads(reply) == []


def test_suggest_returns_json_array(dispatcher: Dispatcher) -> None:
    body = {"launchId": 1, "project": 2, "logs": []}
    assert json.loads(dispatcher.process("suggest", body)) == []


def test_cluster_returns_cluster_result(dispatcher: Dispatcher) -> None:
    body = {"launch": LAUNCH, "project": 2, "numberOfLogLines": 5}
    reply = json.loads(dispatcher.process("cluster", body))
    assert reply == {"project": 2, "launchId": 1, "clusters": []}


def test_search_returns_json_array(dispatcher: Dispatcher) -> None:
    body = {
        "launchId": 1,
        "launchName": "L",
        "itemId": 2,
        "projectId": 3,
        "filteredLaunchIds": [1],
        "logMessages": ["m"],
        "logLines": 5,
    }
    assert json.loads(dispatcher.process("search", body)) == []


def test_scalar_delete_returns_stringified_count(dispatcher: Dispatcher) -> None:
    assert dispatcher.process("delete", 7) == "0"


def test_clean_returns_stringified_count(dispatcher: Dispatcher) -> None:
    assert dispatcher.process("clean", {"ids": [1, 2], "project": 3}) == "0"


def test_defect_update_returns_json_int_list_of_unknown_ids(dispatcher: Dispatcher) -> None:
    body = {
        "project": 1,
        "itemsToUpdate": {"123": "pb001", "456": {"issueType": "ab001"}},
    }
    reply = json.loads(dispatcher.process("defect_update", body))
    assert sorted(reply) == [123, 456]


def test_train_models_never_replies(dispatcher: Dispatcher) -> None:
    body = {"model_type": 2, "project": 1}
    assert dispatcher.process("train_models", body) is None


def test_suggest_patterns_returns_empty_pattern(dispatcher: Dispatcher) -> None:
    reply = dispatcher.process("suggest_patterns", 5)
    SuggestPattern.model_validate_json(reply)
    assert json.loads(reply) == {"suggestionsWithLabels": [], "suggestionsWithoutLabels": []}


def test_namespace_finder_noop_no_reply(
    dispatcher: Dispatcher, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        assert dispatcher.process("namespace_finder", [LAUNCH]) is None
    assert any("namespace_finder" in r.message for r in caplog.records)


def test_index_suggest_info_deprecated_replies_empty_object(
    dispatcher: Dispatcher, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        reply = dispatcher.process("index_suggest_info", [])
    assert reply == "{}"
    assert any(
        "Deprecated 'index_suggest_info' route called with:" in r.message for r in caplog.records
    )


def test_index_suggest_info_replies_empty_object_for_malformed_payload(
    dispatcher: Dispatcher, caplog: pytest.LogCaptureFixture
) -> None:
    # A malformed payload must NOT raise (no ValidationError -> DLQ-without-reply);
    # the deprecated route always replies {} (spec 01 §4.4).
    for body in ({"not": "a list"}, [{"garbage": 1}], "totally wrong", 12345):
        with caplog.at_level("WARNING"):
            assert dispatcher.process("index_suggest_info", body) == "{}"


def test_remove_suggest_info_echoes_int(dispatcher: Dispatcher) -> None:
    assert dispatcher.process("remove_suggest_info", 42) == "42"


def test_update_suggest_info_replies_one(dispatcher: Dispatcher) -> None:
    assert dispatcher.process("update_suggest_info", {"any": "thing"}) == "1"


def test_remove_models_and_get_model_info_no_reply(dispatcher: Dispatcher) -> None:
    assert dispatcher.process("remove_models", {"x": 1}) is None
    assert dispatcher.process("get_model_info", {"x": 1}) is None


def test_noop_echo_round_trips(dispatcher: Dispatcher) -> None:
    assert dispatcher.process("noop_echo", "hello") == "hello"
    assert dispatcher.process("noop_echo", 5) == "5"


def test_noop_fail_raises(dispatcher: Dispatcher) -> None:
    with pytest.raises(RuntimeError):
        dispatcher.process("noop_fail", {"x": 1})


def test_unknown_routing_key_raises(dispatcher: Dispatcher) -> None:
    with pytest.raises(UnknownRoutingKey):
        dispatcher.process("does_not_exist", {})


def test_validation_error_on_bad_payload(dispatcher: Dispatcher) -> None:
    with pytest.raises(ValidationError):
        dispatcher.process("index", [{"not": "a launch"}])


def test_analyze_serializes_nonempty_results() -> None:
    from analyzer_ng.amqp.dispatcher import serialize_model_list

    results = [AnalysisResult(testItem=1, issueType="pb001", relevantItem=2)]
    assert json.loads(serialize_model_list(results)) == [
        {"testItem": 1, "issueType": "pb001", "relevantItem": 2}
    ]
