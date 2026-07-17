"""Health/metrics HTTP surface (spec 01 §9.2).

FastAPI app with three endpoints:

* ``GET /`` — legacy-compatible health JSON (200 healthy / 503 with a non-healthy
  status string when PostgreSQL is down), including per-consumer thread status and
  running tasks.
* ``GET /health`` — liveness/readiness plus ``pg``/``amqp`` booleans and model
  versions; 503 until the service is ready.
* ``GET /metrics`` — Prometheus exposition of the ``analyzer_*`` series.

The app is transport-agnostic: it reads everything from an injected
:class:`HealthProvider`, so the running service supplies live state and tests can
supply a fake.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response


class HealthProvider(Protocol):
    """Read-only view of service state the HTTP layer renders."""

    version: str
    emb_model_ver: str | None
    gbm_model_ver: str | None

    def is_ready(self) -> bool: ...
    def amqp_ok(self) -> bool: ...
    def pg_ok(self) -> bool: ...
    def thread_statuses(self) -> list[dict[str, Any]]: ...
    def render_metrics(self) -> tuple[bytes, str]: ...
    def metrics_summary(self) -> dict | None: ...


def create_app(provider: HealthProvider) -> FastAPI:
    """Build the FastAPI app bound to ``provider``."""
    app = FastAPI(title="analyzer-ng", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def root() -> Response:
        if not provider.pg_ok():
            # Legacy shape used the key "status" with "OpenSearch is not healthy";
            # analyzer-ng keeps the key, new text (spec §9.2).
            return JSONResponse(status_code=503, content={"status": "PostgreSQL is not healthy"})
        return JSONResponse(
            status_code=200,
            content={"status": "healthy", "threads": provider.thread_statuses()},
        )

    @app.get("/health")
    def health() -> Response:
        ready = provider.is_ready()
        # metrics_summary is optional on older providers/fakes — degrade gracefully.
        summary_fn = getattr(provider, "metrics_summary", None)
        body = {
            "live": True,
            "ready": ready,
            "pg": provider.pg_ok(),
            "amqp": provider.amqp_ok(),
            "emb_model_ver": provider.emb_model_ver,
            "gbm_model_ver": provider.gbm_model_ver,
            "version": provider.version,
            "metrics": summary_fn() if callable(summary_fn) else None,
        }
        return JSONResponse(status_code=200 if ready else 503, content=body)

    @app.get("/metrics")
    def metrics() -> Response:
        body, content_type = provider.render_metrics()
        return Response(content=body, media_type=content_type)

    return app


class HttpServer:
    """Runs the FastAPI app under uvicorn in a background thread (spec §1.1)."""

    def __init__(self, provider: HealthProvider, port: int, host: str = "0.0.0.0") -> None:
        # Imported lazily so importing the app package doesn't require uvicorn.
        import uvicorn

        self._app = create_app(provider)
        config = uvicorn.Config(
            self._app, host=host, port=port, log_level="warning", access_log=False
        )
        self._server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.run, name="http-server", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
