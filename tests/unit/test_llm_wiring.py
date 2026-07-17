"""Sidecar wiring proofs (spec 04 §7): byte-identity, async non-blocking, tenancy.

These exercise the *real* wiring the T4.1 review asked to make load-bearing:
- LLM-off / cache-miss feature vectors are byte-identical to a build without the
  sidecar (the extractor sentinel is a constant);
- the extractor feature lookup is per-project at feature time (§4.2/§5.4);
- enqueue never waits on LLM processing (the suggest path stays on its budget);
- the concrete SuggestionOps applier honours the §4 row-update invariants
  (explainer fills explanation+llm_used; cold-start inserts < τ_auto with
  llm_used, surfaced as methodName='llm_coldstart').
"""

from __future__ import annotations

import threading
import time
from typing import Any

from analyzer_ng.core.decision import TAU_AUTO
from analyzer_ng.core.features import FeatureContext, extract_features, to_vector
from analyzer_ng.llm.apply import COLDSTART_METHOD_NAME
from analyzer_ng.llm.engine import RoleResult
from analyzer_ng.llm.queue import LLMJob, LlmQueue
from analyzer_ng.llm.wiring import (
    PgLlmApplier,
    PgLlmFactLoader,
    build_extractor_feature_lookup,
    build_sidecar,
)


def _config(**overrides: object):
    from analyzer_ng.config import AppConfig

    base = dict(
        amqp_url="amqp://guest:guest@localhost/",
        analyzer_pg_dsn="postgresql://u:p@localhost/analyzer",
    )
    base.update(overrides)
    return AppConfig(**base)  # type: ignore[arg-type]


# ---- Extractor feature lookup: tenancy + hit/miss (§4.2/§5.4) ---- #
class _FakeCache:
    def __init__(self) -> None:
        self.rows: dict[tuple[int, int], dict] = {}

    def get_extractor_by_template(self, project_id, template_hash, ttl_days):
        return self.rows.get((project_id, template_hash))


def test_extractor_lookup_is_per_project() -> None:
    from analyzer_ng.llm.roles.extractor import extractor_template_hash

    cache = _FakeCache()
    thash = extractor_template_hash(1234, [7, 8, 9])
    cache.rows[(1, thash)] = {"failing_layer": "infrastructure", "error_class": "http_5xx"}
    lookup = build_extractor_feature_lookup(cache)  # type: ignore[arg-type]

    # Project 1 (the owner) hits; project 2 (same template set) must miss (§5.4).
    assert lookup(1, 1234, [7, 8, 9]) == ("infrastructure", "http_5xx")
    assert lookup(2, 1234, [7, 8, 9]) is None


def test_feature_vector_byte_identical_off_vs_miss() -> None:
    # A build with the sidecar off (no lookup) and one whose lookup misses both
    # produce the sentinel `unknown` → identical 41-float vectors.
    off = to_vector(extract_features(FeatureContext(has_stacktrace=True, exception_count=2)))
    miss = to_vector(
        extract_features(
            FeatureContext(
                has_stacktrace=True,
                exception_count=2,
                llm_failing_layer="unknown",
                llm_error_class="unknown",
            )
        )
    )
    assert off == miss


# ---- build_sidecar: LLM-off is inert (§0) ---- #
def test_build_sidecar_disabled_is_inert() -> None:
    sidecar = build_sidecar(_config(analyzer_llm_enabled=False), pool=None)
    assert sidecar.enabled is False
    # Enqueue for every role is a no-op — no queue, no thread, no store touch.
    for role in ("explainer", "extractor", "judge", "coldstart"):
        sidecar.enqueue(role, 1, 10, {})
    assert sidecar.probe() is False


# ---- Async: enqueue never waits on processing (§7 latency) ---- #
def test_enqueue_never_blocks_on_slow_processing() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_process(job: LLMJob) -> None:
        started.set()
        release.wait(5.0)  # simulate a 30s-class Ollama call

    queue = LlmQueue(slow_process, maxsize=10)
    queue.start()
    try:
        queue.enqueue(LLMJob("explainer", 1, 1, {}))
        assert started.wait(2.0)  # worker is now stuck inside slow_process
        t0 = time.monotonic()
        queue.enqueue(LLMJob("explainer", 1, 2, {}))  # the suggest-path call
        assert time.monotonic() - t0 < 0.1  # returned immediately despite worker busy
    finally:
        release.set()
        queue.stop()


# ---- Concrete SuggestionOps applier (§4 row updates) ---- #
class _FakeOps:
    def __init__(self) -> None:
        self.explanations: list[tuple] = []
        self.judge: list[tuple] = []
        self.coldstart: list[dict] = []

    def set_explanation(self, project_id, suggestion_id, explanation):
        self.explanations.append((project_id, suggestion_id, explanation))

    def annotate_judge(self, project_id, item_id, *, first_suggestion_id, features_patch):
        self.judge.append((project_id, item_id, first_suggestion_id, features_patch))

    def insert_coldstart(self, **kw: Any) -> int:
        self.coldstart.append(kw)
        return 999


def _applier(ops: _FakeOps) -> PgLlmApplier:
    app = PgLlmApplier.__new__(PgLlmApplier)
    app._ops = ops  # type: ignore[attr-defined]
    app._model_tag = "qwen3:4b-q4_K_M"  # type: ignore[attr-defined]
    return app


def test_applier_explainer_sets_explanation() -> None:
    ops = _FakeOps()
    result = RoleResult("ok", {"explanation": "because X", "quoted_lines": []}, False, "p")
    _applier(ops)("explainer", 1, 10, {"suggestion_id": 55}, result)
    assert ops.explanations == [(1, 55, "because X")]


def test_applier_coldstart_locator_from_label_and_band() -> None:
    ops = _FakeOps()
    out = {"label": "si", "confidence": "high", "rubric_rule_matched": "R6", "reason": "conn"}
    _applier(ops)("coldstart", 1, 10, {"launch_id": 42}, RoleResult("ok", out, False, "p"))
    row = ops.coldstart[0]
    # §4.4: predicted_label = default locator of the rubric's base label.
    assert row["predicted_label"] == "si001"
    # Always inside the suggest band (< τ_auto), never auto-applies.
    assert row["confidence"] < TAU_AUTO
    assert row["model_ver"].startswith("rubric+")
    assert row["features"]["coldstart"]["rule"] == "R6"


def test_coldstart_method_name_constant() -> None:
    # The RP surfacing marker for cold-start AI suggestions (§4.4).
    assert COLDSTART_METHOD_NAME == "llm_coldstart"


# ---- Fact loader gating (§4.1 abstain skip, §4.4 cold gate) ---- #
class _FakeFacts:
    def __init__(self, sig: dict | None, sug: dict | None) -> None:
        self._sig = sig
        self._sug = sug

    def load_signature(self, project_id, item_id):
        return self._sig

    def latest_suggestion(self, project_id, item_id):
        return self._sug


_SIG = {
    "exception_fp": 1,
    "error_hash": 2,
    "top_frames": ["a.b.C"],
    "template_ids": [1, 2],
    "exc_text": "java.net.ConnectException",
    "msg_text": "Connection refused",
    "frames_text": "at a.b.C",
    "status_codes": [503],
    "launch_id": 7,
}


def test_fact_loader_explainer_skips_abstain() -> None:
    loader = PgLlmFactLoader(_FakeFacts(_SIG, {"predicted_label": "ti", "suggestion_id": 1}))
    assert loader("explainer", 1, 10, {}) is None  # nothing shown to explain


def test_fact_loader_coldstart_gated_on_cold_project() -> None:
    facts = _FakeFacts(_SIG, None)
    warm = PgLlmFactLoader(facts, is_cold=lambda pid: False)
    cold = PgLlmFactLoader(facts, is_cold=lambda pid: True)
    assert warm("coldstart", 1, 10, {}) is None  # warm project → skip (§4.4)
    got = cold("coldstart", 1, 10, {"launch_id": 7})
    assert got is not None and got["launch_id"] == 7


def test_fact_loader_missing_signature_returns_none() -> None:
    loader = PgLlmFactLoader(_FakeFacts(None, None))
    assert loader("extractor", 1, 10, {}) is None
