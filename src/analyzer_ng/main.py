"""Service entrypoint and startup sequence (spec 01 §6).

Order: configure logging -> load & validate config + read ``VERSION`` -> start the
HTTP server (ready=false, liveness available during startup) -> PostgreSQL
bootstrap + migrations -> open the connection pool -> start AMQP consumers, worker
pool, and reply publisher -> readiness flips true. SIGTERM/SIGINT triggers a
graceful shutdown; a second signal exits immediately (130).

Seed-KB load (§6 step 6) validates the packaged failure-mode catalog and binds
the KBStore for lazy per-project copies. Model warmup (§6 step 5) lands with the
ML task; ``emb``/``gbm`` versions are reported as ``null`` until then.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from types import FrameType

from analyzer_ng.api.http import HttpServer
from analyzer_ng.config import AppConfig, load_config
from analyzer_ng.core import observability as obs
from analyzer_ng.db.pool import open_pool
from analyzer_ng.db.startup import bootstrap_and_migrate_or_exit
from analyzer_ng.metrics import Metrics
from analyzer_ng.service import AnalyzerService

logger = logging.getLogger(__name__)

# VERSION lives at the repo root in dev and at the container WORKDIR in the image
# (§7.1 ``COPY VERSION ./``). The installed package is under site-packages, so the
# repo-relative path only resolves in a source checkout — probe both.
_VERSION_CANDIDATES = (
    Path.cwd() / "VERSION",
    Path(__file__).resolve().parents[2] / "VERSION",
)


class _JsonFormatter(logging.Formatter):
    """Minimal JSON-lines formatter (spec §9.1)."""

    def __init__(self, app_version: str) -> None:
        super().__init__()
        self._app_version = app_version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "app_version": self._app_version,
        }
        # §9.1 mandatory fields: correlation_id / routing_key / project (contextvar-
        # propagated) and duration_ms (on completion lines, carried via extra=).
        payload.update(obs.current_log_fields())
        duration_ms = getattr(record, "duration_ms", None)
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def read_app_version() -> str:
    """Read the single-line semver from the ``VERSION`` file (spec §6 step 1)."""
    for candidate in _VERSION_CANDIDATES:
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0"


def setup_logging(level: str, app_version: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter(app_version))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def _install_signal_handlers(shutdown_event: threading.Event) -> None:
    signalled = {"count": 0}

    def _handler(signum: int, _frame: FrameType | None) -> None:
        signalled["count"] += 1
        if signalled["count"] >= 2:
            logger.warning("Second signal (%s) received; exiting immediately", signum)
            raise SystemExit(130)
        logger.info("Signal %s received; starting graceful shutdown", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def run(config: AppConfig, app_version: str) -> int:
    """Execute the startup sequence and block until shutdown. Returns exit code."""
    shutdown_event = threading.Event()
    _install_signal_handlers(shutdown_event)

    metrics = Metrics()
    service = AnalyzerService(config, app_version, metrics=metrics)

    # Step 2: HTTP up immediately (ready=false until consumers start).
    http = HttpServer(service, port=config.analyzer_http_port)
    http.start()
    logger.info("HTTP server listening on :%d", config.analyzer_http_port)

    # Steps 3–4: PostgreSQL bootstrap + migrations, then open the pool.
    applied = bootstrap_and_migrate_or_exit(
        config.pg_dsn_effective,
        schema=config.analyzer_pg_schema,
        create_db=config.analyzer_pg_create_db,
    )
    logger.info("Migrations applied this start: %d", len(applied))
    pool = open_pool(config)
    service.set_pg_pool(pool)

    # Step 6: seed KB load (idempotent) — validate the packaged catalog and bind
    # the KBStore for lazy per-project copies (spec 01 §6 / spec 03 §9).
    service.load_seed_kb()

    # Steps 7–9: AMQP consumers + worker pool + reply publisher; readiness true.
    service.start()

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    finally:
        logger.info("Shutting down analyzer-ng")
        service.shutdown()
        http.shutdown()
        pool.close()
    return 0


def main() -> None:
    """Console-script entrypoint."""
    config = load_config()
    app_version = read_app_version()
    setup_logging(config.logging_level, app_version)
    logger.info("Starting analyzer-ng %s", app_version)
    raise SystemExit(run(config, app_version))


if __name__ == "__main__":
    main()
