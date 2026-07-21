"""Masked-truncation screen tests (spec 04 §3.0 step 6b).

Constrained decoding hides a num_predict cut: the grammar closes the open JSON
string and emits the trailing schema fields, so a mid-sentence fragment validates
and flows to the UI (live: coldstart reason for item 3688, "...the exception is
a"). These cover the heuristic, the finish_reason gate, the retry-with-doubled-
budget path, and the honest trailing marker.
"""

from __future__ import annotations

import json

import pytest
from _llm_fakes import FakeCacheStore, FakeEventStore, MockOllama

from analyzer_ng.llm.breaker import CircuitBreaker
from analyzer_ng.llm.engine import (
    LlmEngine,
    _ends_complete,
    _looks_truncated,
    _mark_truncated,
    _truncated_fields,
)
from analyzer_ng.llm.roles import ColdStartRole
from analyzer_ng.llm.roles.base import sha256_hex

MODEL = "qwen3:4b-q4_K_M"


# --------------------------------------------------------------------------- #
# _ends_complete: does the text look like a finished sentence?
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "The product output was wrong.",
        "Is this a product bug?",
        "It failed!",
        "It trailed off…",  # horizontal ellipsis
        'The log shows "Connection refused."',  # terminal before a closing quote
        "root frame in test code.)",  # terminal before a closing paren
        "エラーです。",  # CJK: ends with 。
        "   ",  # blank is not a mid-sentence cut
        "",
        '""',  # only closers — nothing to judge
    ],
)
def test_ends_complete_true(text: str) -> None:
    assert _ends_complete(text)


@pytest.mark.parametrize(
    "text",
    [
        "However, since the root frame is in the test code and the exception is a",
        "The failing frame is in the test",
        'It matched because of "Connection refused',  # cut inside an open quote
        "assertion fired and the value was 42",
    ],
)
def test_ends_complete_false(text: str) -> None:
    assert not _ends_complete(text)


# --------------------------------------------------------------------------- #
# _looks_truncated: only a "length" stop makes an unfinished field a masked cut.
# --------------------------------------------------------------------------- #
def test_looks_truncated_requires_length_stop() -> None:
    cut = "the exception is a"
    # The server hit the token cap AND the text ends mid-sentence → masked cut.
    assert _looks_truncated(cut, "length")
    # Same text, but the model stopped on its own → trust it, don't flag.
    assert not _looks_truncated(cut, "stop")
    # finish_reason not surfaced → conservative, don't flag on punctuation alone.
    assert not _looks_truncated(cut, None)


def test_looks_truncated_complete_text_at_length_is_not_flagged() -> None:
    # Model happened to finish the sentence exactly as the cap was reached.
    assert not _looks_truncated("The product output was wrong.", "length")


def test_truncated_fields_scopes_to_declared_free_text() -> None:
    role = ColdStartRole()
    output = {
        "label": "pb",
        "confidence": "high",
        "rubric_rule_matched": "R1",
        "reason": "the exception is a",
    }
    assert _truncated_fields(role, output, "length") == ["reason"]
    assert _truncated_fields(role, output, "stop") == []


def test_mark_truncated_appends_marker_and_flag() -> None:
    marked = _mark_truncated({"reason": "the exception is a"}, ["reason"])
    assert marked["reason"] == "the exception is a …"
    assert marked["truncated"] is True
    # The marker makes the field read as complete (honest, not silent).
    assert _ends_complete(marked["reason"])


# --------------------------------------------------------------------------- #
# Engine integration.
# --------------------------------------------------------------------------- #
def _coldstart_input() -> dict:
    return {
        "fact_block": {"exception_chain": ["java.lang.AssertionError"]},
        "log_excerpt": "java.lang.AssertionError: expected true\nat com.acme.FooTest.check",
        "error_hash": 3688,
    }


def _cache_key(role, inp: dict) -> str:
    return sha256_hex(f"{MODEL}|{role.name}|{sha256_hex(role.content_key(inp))}")


def _engine(mock: MockOllama, cache: FakeCacheStore, events: FakeEventStore) -> LlmEngine:
    return LlmEngine(
        client=mock.client(model=MODEL),
        breaker=CircuitBreaker(),
        cache_store=cache,
        event_store=events,
        model=MODEL,
    )


# A schema-valid, rubric-agreeing (R1 -> pb) coldstart body cut mid-reason.
_CUT = {
    "content": json.dumps(
        {
            "label": "pb",
            "confidence": "high",
            "rubric_rule_matched": "R1",
            "reason": "The AssertionError fired in the test. However, the exception is a",
        }
    ),
    "finish_reason": "length",
}
_COMPLETE = {
    "content": json.dumps(
        {
            "label": "pb",
            "confidence": "high",
            "rubric_rule_matched": "R1",
            "reason": "The AssertionError fired in the test, so the product output is wrong.",
        }
    ),
    "finish_reason": "stop",
}


def test_masked_cut_retries_with_doubled_budget_and_heals() -> None:
    role, inp = ColdStartRole(), _coldstart_input()
    mock = MockOllama([_CUT, _COMPLETE])
    cache, events = FakeCacheStore(), FakeEventStore()
    result = _engine(mock, cache, events).run(role, project_id=7, item_id=3688, inp=inp)

    assert result.outcome == "ok"
    assert len(mock.chat_bodies) == 2  # first cut, one retry
    # The retry doubled the token budget.
    assert mock.chat_bodies[0]["options"]["num_predict"] == role.num_predict
    assert mock.chat_bodies[1]["options"]["num_predict"] == role.num_predict * 2
    # The healed (complete) reason is what got persisted — no marker, no flag.
    assert result.output["reason"].endswith("wrong.")
    assert "truncated" not in result.output
    assert cache.rows[(7, _cache_key(role, inp))]["output"]["reason"].endswith("wrong.")


def test_persistent_cut_is_marked_not_silently_stored() -> None:
    role, inp = ColdStartRole(), _coldstart_input()
    # Both the first call and the doubled-budget retry come back cut.
    mock = MockOllama([_CUT, _CUT])
    cache, events = FakeCacheStore(), FakeEventStore()
    result = _engine(mock, cache, events).run(role, project_id=7, item_id=3688, inp=inp)

    assert result.outcome == "ok"  # still a usable, if clipped, answer
    assert len(mock.chat_bodies) == 2
    assert result.output["reason"].endswith("the exception is a …")
    assert result.output["truncated"] is True
    # The event and cache carry the honest, marked text (never the silent cut).
    stored = cache.rows[(7, _cache_key(role, inp))]["output"]
    assert stored["truncated"] is True
    assert events.events[-1]["outcome"] == "ok"
    assert events.events[-1]["output"]["truncated"] is True


def test_complete_reason_at_length_is_not_retried() -> None:
    role, inp = ColdStartRole(), _coldstart_input()
    # finish_reason "length" but the sentence is finished — no masked cut.
    at_cap = {**_COMPLETE, "finish_reason": "length"}
    mock = MockOllama([at_cap])
    cache, events = FakeCacheStore(), FakeEventStore()
    result = _engine(mock, cache, events).run(role, project_id=7, item_id=3688, inp=inp)

    assert result.outcome == "ok"
    assert len(mock.chat_bodies) == 1  # no needless retry
    assert "truncated" not in result.output


def test_retry_transport_failure_falls_back_to_marked_original() -> None:
    role, inp = ColdStartRole(), _coldstart_input()
    # First call cut; the doubled-budget retry dies at the transport → mark original.
    mock = MockOllama([_CUT, {"exception": "timeout"}])
    cache, events = FakeCacheStore(), FakeEventStore()
    result = _engine(mock, cache, events).run(role, project_id=7, item_id=3688, inp=inp)

    assert result.outcome == "ok"
    assert result.output["truncated"] is True
    assert result.output["reason"].endswith("the exception is a …")


# --------------------------------------------------------------------------- #
# finish_reason threading through the client.
# --------------------------------------------------------------------------- #
def test_finish_reason_threaded_native() -> None:
    mock = MockOllama([{"content": '{"x":"1"}', "finish_reason": "length"}])
    resp = mock.client().chat(system="s", user="u", schema={"type": "object"}, num_predict=10)
    assert resp.finish_reason == "length"


def test_finish_reason_threaded_openai() -> None:
    mock = MockOllama([{"content": '{"x":"1"}', "finish_reason": "length"}])
    resp = mock.client(api="openai").chat(
        system="s", user="u", schema={"type": "object"}, num_predict=10
    )
    assert resp.finish_reason == "length"


# --------------------------------------------------------------------------- #
# Budget audit: the right-sized num_predict values are documented in the roles.
# --------------------------------------------------------------------------- #
def test_role_budgets_right_sized() -> None:
    from analyzer_ng.llm.roles import ExplainerRole, JudgeRole
    from analyzer_ng.llm.roles.explainer import AbstainExplainerRole

    assert ColdStartRole().num_predict == 384
    assert ExplainerRole().num_predict == 512
    assert AbstainExplainerRole().num_predict == 384
    assert JudgeRole().num_predict == 320
    # Declared free-text (narrative) fields — the masked-truncation surface.
    assert ColdStartRole().free_text_fields == ("reason",)
    assert ExplainerRole().free_text_fields == ("explanation",)
    assert AbstainExplainerRole().free_text_fields == ("explanation",)
    assert JudgeRole().free_text_fields == ("reason",)
