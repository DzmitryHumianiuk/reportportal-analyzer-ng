"""HTTP health/metrics shape tests (spec 01 §9.2).

Drives the FastAPI app with a fake :class:`HealthProvider` (no broker/DB) to pin
the response shapes and status codes of ``/``, ``/health``, and ``/metrics``.
"""

from __future__ import annotations

import warnings
from typing import Any

with warnings.catch_warnings():
    # FastAPI's TestClient re-exports Starlette's, which warns at import about its
    # httpx dependency — third-party and not actionable here.
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from analyzer_ng.api.http import create_app
from analyzer_ng.main import read_app_version
from analyzer_ng.metrics import Metrics


class FakeProvider:
    version = "9.9.9"
    emb_model_ver = None
    gbm_model_ver = None

    def __init__(self, *, ready: bool, pg: bool, amqp: bool, metrics: dict | None = None) -> None:
        self._ready = ready
        self._pg = pg
        self._amqp = amqp
        self._metrics = Metrics()
        self._summary = metrics

    def metrics_summary(self) -> dict | None:
        return self._summary

    def is_ready(self) -> bool:
        return self._ready

    def amqp_ok(self) -> bool:
        return self._amqp

    def pg_ok(self) -> bool:
        return self._pg

    def thread_statuses(self) -> list[dict[str, Any]]:
        return [{"name": "all", "status": "alive", "running_tasks": {"number": 0, "tasks": []}}]

    def render_metrics(self) -> tuple[bytes, str]:
        return self._metrics.render()


def test_root_healthy_when_pg_up() -> None:
    client = TestClient(create_app(FakeProvider(ready=True, pg=True, amqp=True)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
    assert resp.json()["threads"][0]["name"] == "all"


def test_root_503_when_pg_down() -> None:
    client = TestClient(create_app(FakeProvider(ready=True, pg=False, amqp=True)))
    resp = client.get("/")
    assert resp.status_code == 503
    assert resp.json() == {"status": "PostgreSQL is not healthy"}


def test_health_reports_ready_pg_amqp() -> None:
    client = TestClient(create_app(FakeProvider(ready=True, pg=True, amqp=True)))
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "live": True,
        "ready": True,
        "pg": True,
        "amqp": True,
        "emb_model_ver": None,
        "gbm_model_ver": None,
        "version": "9.9.9",
        "metrics": None,
    }


def test_health_exposes_metrics_daily_summary() -> None:
    # spec §10.3: the daily-metrics summary is surfaced on the health endpoint.
    summary = {"suggestions": 12, "accepted": 7, "auto_corrected": 1, "last_day": "2026-07-15"}
    provider = FakeProvider(ready=True, pg=True, amqp=True, metrics=summary)
    resp = TestClient(create_app(provider)).get("/health")
    assert resp.status_code == 200
    assert resp.json()["metrics"] == summary


def test_health_503_before_ready() -> None:
    client = TestClient(create_app(FakeProvider(ready=False, pg=True, amqp=False)))
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False


def test_metrics_exposes_expected_series() -> None:
    client = TestClient(create_app(FakeProvider(ready=True, pg=True, amqp=True)))
    resp = client.get("/metrics")
    assert resp.status_code == 200
    for series in (
        "analyzer_requests_total",
        "analyzer_request_seconds",
        "analyzer_queue_depth",
        "analyzer_suggest_shown_total",
        "analyzer_abstain_total",
        "analyzer_pg_pool_in_use",
    ):
        assert series in resp.text


def test_read_app_version_matches_version_file() -> None:
    assert read_app_version() == "0.1.0"
