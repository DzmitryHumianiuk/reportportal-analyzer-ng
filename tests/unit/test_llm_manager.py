"""LlmSidecar facade tests: LLM-off byte-identical, gating, probe, health."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from _llm_fakes import FakeCacheStore, FakeEventStore, FakeRoleStateStore, MockOllama

from analyzer_ng.config import AppConfig
from analyzer_ng.llm.client import OllamaClient
from analyzer_ng.llm.manager import LlmSidecar

_GOOD_EXPLAINER = json.dumps({"explanation": "matched", "quoted_lines": ["Connection refused"]})


def _config(**overrides: object) -> AppConfig:
    base = dict(
        amqp_url="amqp://guest:guest@localhost/",
        analyzer_pg_dsn="postgresql://u:p@localhost/analyzer",
    )
    base.update(overrides)
    return AppConfig(**base)  # type: ignore[arg-type]


def _stores() -> tuple[FakeCacheStore, FakeEventStore, FakeRoleStateStore]:
    return FakeCacheStore(), FakeEventStore(), FakeRoleStateStore()


def _canary_client() -> OllamaClient:
    """A client that fails the test on any HTTP hit (spec §7 canary)."""

    def boom(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"LLM-off build must not hit Ollama: {request.url}")

    return OllamaClient("http://ollama:11434", "m", transport=httpx.MockTransport(boom))


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


# ---- LLM-off byte-identical (heart of acceptance) ---- #
def test_disabled_is_inert_and_never_hits_ollama() -> None:
    cache, events, state = _stores()
    sidecar = LlmSidecar(
        _config(analyzer_llm_enabled=False),
        cache_store=cache,
        event_store=events,
        role_state_store=state,
        client=_canary_client(),  # would raise if ever used
    )
    assert sidecar.enabled is False
    # Enqueue for every role — all no-ops, no queue, no thread, no events.
    for role in ("explainer", "extractor", "judge", "coldstart"):
        sidecar.enqueue(role, 1, 10, _explainer_input())
    assert events.events == []
    assert cache.rows == {}
    assert sidecar.probe() is False
    assert sidecar.health() == {
        "enabled": False,
        "available": False,
        "model": "qwen3:4b-q4_K_M",
        "reason": "disabled",
        "breaker_state": None,  # no breaker constructed while disabled
    }


def test_health_surfaces_breaker_state_and_makes_no_network_call() -> None:
    # tech-debt #4: health() is a cheap in-process read — the breaker's live state
    # is surfaced without any Ollama call (canary client raises on any HTTP hit).
    cache, events, state = _stores()
    sidecar = LlmSidecar(
        _config(analyzer_llm_enabled=True),
        cache_store=cache,
        event_store=events,
        role_state_store=state,
        client=_canary_client(),  # would raise if health() touched the network
    )
    health = sidecar.health()
    assert health["enabled"] is True
    assert health["breaker_state"] == "closed"  # fresh breaker starts closed
    # Trip the breaker (3 consecutive transport failures) — health reflects it live.
    for _ in range(3):
        sidecar._breaker.record_failure()  # type: ignore[union-attr]
    assert sidecar.health()["breaker_state"] == "open"


# ---- Probe ---- #
def test_probe_available_when_model_present() -> None:
    cache, events, state = _stores()
    mock = MockOllama(model_present=True, chat_script=["{}"])  # warmup call
    sidecar = LlmSidecar(
        _config(analyzer_llm_enabled=True),
        cache_store=cache,
        event_store=events,
        role_state_store=state,
        client=mock.client(),
    )
    assert sidecar.probe() is True
    assert sidecar.health()["available"] is True
    assert sidecar.health()["reason"] is None


def test_probe_model_missing_warns_exact_pull_and_no_download(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache, events, state = _stores()
    mock = MockOllama(model_present=False)
    sidecar = LlmSidecar(
        _config(analyzer_llm_enabled=True, analyzer_llm_model="qwen3:4b-q4_K_M"),
        cache_store=cache,
        event_store=events,
        role_state_store=state,
        client=mock.client(),
    )
    with caplog.at_level(logging.WARNING):
        assert sidecar.probe() is False
    assert "ollama pull qwen3:4b-q4_K_M" in caplog.text
    assert sidecar.health()["reason"] == "model_missing"
    assert all("/api/pull" not in r.url.path for r in mock.requests)  # no download


# ---- Gating ---- #
def test_role_flag_off_skips_enqueue() -> None:
    cache, events, state = _stores()
    mock = MockOllama(chat_script=["{}"])
    sidecar = LlmSidecar(
        _config(analyzer_llm_enabled=True, analyzer_llm_judge=False),
        cache_store=cache,
        event_store=events,
        role_state_store=state,
        client=mock.client(),
    )
    sidecar._available = True
    sidecar.enqueue("judge", 1, 10, _explainer_input())
    assert sidecar._queue.drain_once() is False  # nothing enqueued


def test_per_project_role_state_disable_skips_processing() -> None:
    cache, events, state = _stores()
    state.set_state(1, "explainer", enabled=False)
    mock = MockOllama(chat_script=[_GOOD_EXPLAINER])
    sidecar = LlmSidecar(
        _config(analyzer_llm_enabled=True),
        cache_store=cache,
        event_store=events,
        role_state_store=state,
        client=mock.client(),
    )
    sidecar._available = True
    sidecar.enqueue("explainer", 1, 10, _explainer_input())
    sidecar._queue.drain_once()
    assert events.events == []  # role disabled for project 1 → no call
    assert mock.chat_bodies == []


def test_enabled_runs_role_and_calls_applier() -> None:
    cache, events, state = _stores()
    mock = MockOllama(chat_script=[_GOOD_EXPLAINER])
    applied: list[tuple] = []
    sidecar = LlmSidecar(
        _config(analyzer_llm_enabled=True),
        cache_store=cache,
        event_store=events,
        role_state_store=state,
        client=mock.client(),
        applier=lambda role, pid, iid, inp, res: applied.append((role, pid, iid, res.outcome)),
    )
    sidecar._available = True
    sidecar.enqueue("explainer", 1, 10, _explainer_input())
    sidecar._queue.drain_once()
    assert events.events[-1]["outcome"] == "ok"
    assert applied == [("explainer", 1, 10, "ok")]


def test_unavailable_processing_is_skipped() -> None:
    cache, events, state = _stores()
    mock = MockOllama(chat_script=[_GOOD_EXPLAINER])
    sidecar = LlmSidecar(
        _config(analyzer_llm_enabled=True),
        cache_store=cache,
        event_store=events,
        role_state_store=state,
        client=mock.client(),
    )
    sidecar._available = False  # Ollama not (yet) available
    sidecar.enqueue("explainer", 1, 10, _explainer_input())
    sidecar._queue.drain_once()
    assert events.events == []
    assert mock.chat_bodies == []
