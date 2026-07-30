"""Negative cache for deterministic role failures (issue #7).

Project 7 has template sets whose whole log is a bare assertion message with no
exception identifier, so the extractor's grounding check rejects both seeds every
time. These tests pin the deal: that certain failure is remembered for a short
window (no LLM call on a repeat), a transport failure never is, and a remembered
failure stays a failure everywhere downstream.
"""

from __future__ import annotations

import json

from _llm_fakes import FakeCacheStore, FakeEventStore, MockOllama

from analyzer_ng.core.features import FeatureContext, extract_features
from analyzer_ng.llm.breaker import CircuitBreaker
from analyzer_ng.llm.engine import LlmEngine, is_negative, negative_payload
from analyzer_ng.llm.roles import ExplainerRole, ExtractorRole
from analyzer_ng.llm.roles.base import sha256_hex
from analyzer_ng.llm.roles.extractor import extractor_template_hash
from analyzer_ng.llm.wiring import build_extractor_feature_lookup

MODEL = "qwen3:4b-q4_K_M"


def _engine(mock: MockOllama, cache: FakeCacheStore, events: FakeEventStore) -> LlmEngine:
    return LlmEngine(
        client=mock.client(model=MODEL),
        breaker=CircuitBreaker(),
        cache_store=cache,
        event_store=events,
        model=MODEL,
    )


def _cache_key(role, inp: dict) -> str:
    return sha256_hex(f"{MODEL}|{role.name}|{sha256_hex(role.content_key(inp))}")


def _extractor_input() -> dict:
    # The measured shape: two lines of merged assertion text, no class name anywhere.
    return {
        "fact_block": {"error_templates": ["expected <*> to deeply equal <*>"]},
        "log_excerpt": "AssertionError: expected false to deeply equal true\nat step 4",
        "exception_fp": 4242,
        "template_ids": [11, 12],
    }


# Schema-valid, but root_exception appears nowhere in the log → post_validate rejects
# it as a hallucination. Deterministic: the same input fails the same way on any seed.
_UNGROUNDED = json.dumps(
    {
        "root_exception": "com.acme.NeverSeenException",
        "wrapper_chain": [],
        "failing_layer": "test_code",
        "error_class": "assertion",
        "components": [],
    }
)


def _explainer_input() -> dict:
    return {
        "mode_title": "Connection refused",
        "mode_label": "si001",
        "mode_support": 42,
        "mode_purity": 0.9,
        "match_signals": "cosine 0.91",
        "fact_block": {"exception_chain": ["java.net.ConnectException"]},
        "log_excerpt": "java.net.ConnectException: Connection refused",
        "mode_id": 7,
        "error_hash": 123456,
    }


_UNGROUNDED_EXPLANATION = json.dumps(
    {"explanation": "x", "quoted_log_lines": ["fabricated line"], "quoted_fact_values": []}
)


def test_validation_fail_is_cached_and_the_repeat_costs_nothing() -> None:
    role, inp = ExtractorRole(), _extractor_input()
    cache, events = FakeCacheStore(), FakeEventStore()
    mock = MockOllama([_UNGROUNDED, _UNGROUNDED])
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)

    assert result.outcome == "validation_fail"
    assert result.output is None
    assert len(mock.chat_bodies) == 2  # retry-once, then remember the failure
    row = cache.rows[(1, _cache_key(role, inp))]
    assert is_negative(row["output"])
    assert row["output"]["outcome"] == "validation_fail"
    # Not an empty-but-present extraction: nothing downstream can read a field.
    assert "failing_layer" not in row["output"]
    assert "error_class" not in row["output"]
    # Never served as a template-set extraction at feature time (§4.2).
    assert row["template_hash"] is None

    # The repeat inside the window: a hit, and zero Ollama calls.
    repeat_mock = MockOllama([])
    repeat = _engine(repeat_mock, cache, events).run(role, project_id=1, item_id=11, inp=inp)
    assert repeat.outcome == "validation_fail"
    assert repeat.output is None
    assert repeat.cache_hit is True
    assert repeat_mock.chat_bodies == []


def test_negative_hit_is_recorded_honestly_and_differs_from_a_positive_hit() -> None:
    role, inp = ExtractorRole(), _extractor_input()
    cache, events = FakeCacheStore(), FakeEventStore()
    cache.put(1, _cache_key(role, inp), role.name, MODEL, negative_payload("validation_fail"))
    _engine(MockOllama([]), cache, events).run(role, project_id=1, item_id=10, inp=inp)

    event = events.events[-1]
    assert event["outcome"] == "validation_fail"  # never reported as ok
    assert event["cache_hit"] is True  # and distinguishable from a fresh failure
    assert event["output"] is None


def test_timeout_is_never_cached() -> None:
    role, inp = ExtractorRole(), _extractor_input()
    cache, events = FakeCacheStore(), FakeEventStore()
    mock = MockOllama([{"exception": "timeout"}])
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)
    assert result.outcome == "timeout"
    assert cache.rows == {}  # retrying transport failures is the point of the breaker


def test_breaker_open_is_never_cached() -> None:
    role, inp = ExtractorRole(), _extractor_input()
    cache, events = FakeCacheStore(), FakeEventStore()
    mock = MockOllama([{"exception": "timeout"}] * 3)
    breaker = CircuitBreaker()
    engine = LlmEngine(
        client=mock.client(model=MODEL),
        breaker=breaker,
        cache_store=cache,
        event_store=events,
        model=MODEL,
    )
    for _ in range(3):
        engine.run(role, project_id=1, item_id=10, inp=inp)
    result = engine.run(role, project_id=1, item_id=10, inp=inp)
    assert result.outcome == "breaker_open"
    assert cache.rows == {}


def test_negative_ttl_is_shorter_than_the_positive_one() -> None:
    role = ExtractorRole()
    assert role.negative_ttl_days == 7
    assert role.ttl_days == 90
    assert role.negative_ttl_days < role.ttl_days


def test_negative_entry_expires_on_the_short_ttl_while_a_success_survives() -> None:
    role, inp = ExtractorRole(), _extractor_input()
    key = _cache_key(role, inp)

    # Eight days old: past the 7-day negative window, so the role runs again.
    cache, events = FakeCacheStore(), FakeEventStore()
    cache.put(1, key, role.name, MODEL, negative_payload("validation_fail"))
    cache.rows[(1, key)]["age_days"] = 8
    mock = MockOllama([_UNGROUNDED, _UNGROUNDED])
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)
    assert result.cache_hit is False
    assert len(mock.chat_bodies) == 2

    # Same age, a successful entry: still fresh, because success keeps 90 days.
    good = {
        "root_exception": None,
        "wrapper_chain": [],
        "failing_layer": "test_code",
        "error_class": "assertion",
        "components": [],
    }
    cache2, events2 = FakeCacheStore(), FakeEventStore()
    cache2.put(1, key, role.name, MODEL, good)
    cache2.rows[(1, key)]["age_days"] = 8
    mock2 = MockOllama([])
    result2 = _engine(mock2, cache2, events2).run(role, project_id=1, item_id=10, inp=inp)
    assert result2.outcome == "ok"
    assert result2.cache_hit is True
    assert mock2.chat_bodies == []


def test_negative_entry_inside_the_window_is_a_hit() -> None:
    role, inp = ExtractorRole(), _extractor_input()
    key = _cache_key(role, inp)
    cache, events = FakeCacheStore(), FakeEventStore()
    cache.put(1, key, role.name, MODEL, negative_payload("validation_fail"))
    cache.rows[(1, key)]["age_days"] = 3
    mock = MockOllama([])
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)
    assert result.cache_hit is True
    assert result.outcome == "validation_fail"
    assert mock.chat_bodies == []


def test_roles_without_the_knob_do_not_cache_failures() -> None:
    # The engine keys off Role.negative_ttl_days, not the role's name: the
    # explainer has not opted in, so its validation_fail stays uncached.
    role, inp = ExplainerRole(), _explainer_input()
    assert role.negative_ttl_days is None
    cache, events = FakeCacheStore(), FakeEventStore()
    mock = MockOllama([_UNGROUNDED_EXPLANATION, _UNGROUNDED_EXPLANATION])
    result = _engine(mock, cache, events).run(role, project_id=1, item_id=10, inp=inp)
    assert result.outcome == "validation_fail"
    assert cache.rows == {}


class _FakeTemplateCache:
    """Feature-time lookup store, keyed like ``get_extractor_by_template``."""

    def __init__(self) -> None:
        self.rows: dict[tuple[int, int], dict] = {}

    def get_extractor_by_template(self, project_id: int, template_hash: int, ttl_days: int):
        return self.rows.get((project_id, template_hash))


def test_negative_entry_leaves_the_gbm_columns_at_the_unknown_sentinel() -> None:
    cache = _FakeTemplateCache()
    thash = extractor_template_hash(4242, [11, 12])
    # Even if a negative row reached this index, it must read as "no extractor
    # output" — never as an empty-but-present extraction.
    cache.rows[(1, thash)] = negative_payload("validation_fail")
    lookup = build_extractor_feature_lookup(cache)  # type: ignore[arg-type]
    assert lookup(1, 4242, [11, 12]) is None

    layer, error_class = ("unknown", "unknown")  # what the caller uses on a miss
    values = extract_features(FeatureContext(llm_failing_layer=layer, llm_error_class=error_class))
    assert values["llm_failing_layer_unknown"] == 1.0
    assert values["llm_error_class_unknown"] == 1.0
    onehots = [k for k in values if k.startswith(("llm_failing_layer_", "llm_error_class_"))]
    assert len(onehots) == 19
    assert sum(values[k] for k in onehots) == 2.0  # one active level per group
