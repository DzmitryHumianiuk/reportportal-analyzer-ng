"""Engine flow tests: cache, retry-once-then-drop, tenancy, schema-or-dropped."""

from __future__ import annotations

import json

from _llm_fakes import FakeCacheStore, FakeEventStore, MockOllama

from analyzer_ng.llm.breaker import CircuitBreaker
from analyzer_ng.llm.engine import LlmEngine
from analyzer_ng.llm.roles import ExplainerRole, JudgeRole
from analyzer_ng.llm.roles.base import sha256_hex

MODEL = "qwen3:4b-q4_K_M"


def _engine(mock: MockOllama, cache: FakeCacheStore, events: FakeEventStore) -> LlmEngine:
    return LlmEngine(
        client=mock.client(model=MODEL),
        breaker=CircuitBreaker(),
        cache_store=cache,
        event_store=events,
        model=MODEL,
    )


def _explainer_input() -> dict:
    return {
        "mode_title": "Connection refused",
        "mode_label": "si001",
        "mode_support": 42,
        "mode_purity": 0.9,
        "match_signals": "cosine 0.91",
        "fact_block": {"exception_chain": ["java.net.ConnectException"]},
        "log_excerpt": "java.net.ConnectException: Connection refused\nat com.acme.Client.call",
        "mode_id": 7,
        "error_hash": 123456,
    }


def _cache_key(role, inp: dict) -> str:
    return sha256_hex(f"{MODEL}|{role.name}|{sha256_hex(role.content_key(inp))}")


_GOOD_EXPLAINER = json.dumps(
    {
        "explanation": "matched",
        "quoted_log_lines": ["java.net.ConnectException: Connection refused"],
        "quoted_fact_values": [],
    }
)


def test_ok_path_stores_cache_and_event() -> None:
    role, inp = ExplainerRole(), _explainer_input()
    mock = MockOllama([_GOOD_EXPLAINER])
    cache, events = FakeCacheStore(), FakeEventStore()
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)
    assert result.outcome == "ok"
    assert len(mock.chat_bodies) == 1
    assert (1, _cache_key(role, inp)) in cache.rows
    assert events.events[-1]["outcome"] == "ok"
    assert events.events[-1]["cache_hit"] is False


def test_cache_hit_skips_ollama() -> None:
    role, inp = ExplainerRole(), _explainer_input()
    cache, events = FakeCacheStore(), FakeEventStore()
    cache.rows[(1, _cache_key(role, inp))] = {
        "output": {"explanation": "cached", "quoted_lines": []}
    }
    mock = MockOllama([])  # zero Ollama calls expected on a cache hit
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)
    assert result.cache_hit is True
    assert result.output == {"explanation": "cached", "quoted_lines": []}
    assert mock.chat_bodies == []  # zero Ollama calls
    assert events.events[-1]["cache_hit"] is True


def test_per_project_cache_isolation() -> None:
    role, inp = ExplainerRole(), _explainer_input()
    cache, events = FakeCacheStore(), FakeEventStore()
    cache.rows[(1, _cache_key(role, inp))] = {"output": {"explanation": "A", "quoted_lines": []}}
    # Project 2 must MISS project 1's key and actually call Ollama.
    mock = MockOllama([_GOOD_EXPLAINER])
    result = _engine(mock, cache, events).run(role, project_id=2, item_id=10, inp=inp)
    assert result.cache_hit is False
    assert len(mock.chat_bodies) == 1
    assert (2, _cache_key(role, inp)) in cache.rows


def test_schema_fail_retries_once_then_drops() -> None:
    role, inp = ExplainerRole(), _explainer_input()
    # Valid HTTP envelope, but the assistant *content* is not valid JSON → schema
    # failure (a malformed HTTP envelope would instead be a transport 'timeout').
    mock = MockOllama(["not json content", "still not json"])
    cache, events = FakeCacheStore(), FakeEventStore()
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)
    assert result.outcome == "schema_fail"
    assert result.output is None
    assert len(mock.chat_bodies) == 2  # retry-once: exactly two calls
    assert cache.rows == {}  # never store partial output
    assert events.events[-1]["outcome"] == "schema_fail"


def test_validation_fail_then_success_on_retry() -> None:
    role, inp = ExplainerRole(), _explainer_input()
    bad = json.dumps(
        {"explanation": "x", "quoted_log_lines": ["fabricated line"], "quoted_fact_values": []}
    )
    mock = MockOllama([bad, _GOOD_EXPLAINER])
    cache, events = FakeCacheStore(), FakeEventStore()
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)
    assert result.outcome == "ok"
    assert len(mock.chat_bodies) == 2


def test_breaker_open_short_circuits() -> None:
    role, inp = ExplainerRole(), _explainer_input()
    mock = MockOllama([{"status": 500}, {"status": 500}, {"status": 500}])
    cache, events = FakeCacheStore(), FakeEventStore()
    breaker = CircuitBreaker()
    engine = LlmEngine(
        client=mock.client(model=MODEL),
        breaker=breaker,
        cache_store=cache,
        event_store=events,
        model=MODEL,
    )
    # Three transport failures across runs open the breaker (retry does not apply
    # to transport failures — each run makes exactly one call).
    for _ in range(3):
        engine.run(role, project_id=1, item_id=10, inp=inp)
    assert len(mock.chat_bodies) == 3
    result = engine.run(role, project_id=1, item_id=10, inp=inp)
    assert result.outcome == "breaker_open"
    assert len(mock.chat_bodies) == 3  # no further Ollama calls while open


# ---- Judge: always schema-valid or dropped (acceptance §7) ---- #
def _judge_input() -> dict:
    cands = [
        {
            "id": 100 + i,
            "suggestion_id": 200 + i,
            "label": "si",
            "similarity": 0.6,
            "exc_chain": "X",
            "templates": "t",
            "frames": "f",
        }
        for i in range(3)
    ]
    return {
        "fact_block": {"exception_chain": ["java.net.ConnectException"]},
        "query_excerpt": "Connection refused",
        "candidates": cands,
        "query_error_hash": 999,
    }


def test_judge_malformed_and_out_of_enum_are_dropped() -> None:
    role = JudgeRole()
    fuzz = [
        ["}{ malformed", "}{ malformed"],  # content not valid JSON
        ['{"choice":"candidate_9","reason":"r"}'] * 2,  # out-of-enum
        ['{"choice":"candidate_1","reason":"r","x":1}'] * 2,  # extra field
    ]
    for script in fuzz:
        mock = MockOllama(list(script))
        cache, events = FakeCacheStore(), FakeEventStore()
        result = _engine(mock, cache, events).run(role, project_id=1, item_id=5, inp=_judge_input())
        assert result.output is None
        assert result.outcome in ("schema_fail", "validation_fail")
        assert len(mock.chat_bodies) == 2  # retry-once verified
        assert cache.rows == {}  # zero mutations
