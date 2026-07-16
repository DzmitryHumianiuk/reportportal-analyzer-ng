"""AnalyzerService — composition root for the AMQP transport + shell (spec 01 §6).

Wires the pieces built by the ``amqp``/``api``/``db`` packages into one runnable
service and implements the :class:`~analyzer_ng.api.http.HealthProvider` the HTTP
layer renders:

* one :class:`~analyzer_ng.amqp.client.ReplyPublisher` (own connection);
* the shared :class:`~analyzer_ng.amqp.dispatcher.WorkerPool`;
* two :class:`~analyzer_ng.amqp.consumer.Consumer` threads — ``all`` (everything
  except ``train_models``) and ``train`` (only ``train_models``);
* health/metrics wiring over an optional PostgreSQL pool.

``main.py`` owns process concerns (config, DB bootstrap, signals, exit codes);
this object owns the running transport so it can be started/stopped in tests.
"""

from __future__ import annotations

import itertools
import logging
import threading
from typing import Any

from psycopg_pool import ConnectionPool

from analyzer_ng.amqp.client import AmqpConnection, ReplyPublisher
from analyzer_ng.amqp.consumer import Consumer
from analyzer_ng.amqp.dispatcher import Dispatcher, WorkerPool
from analyzer_ng.config import AppConfig
from analyzer_ng.db.pool import check_pg, pool_in_use
from analyzer_ng.db.repositories.kb import PgKBStore
from analyzer_ng.metrics import Metrics
from analyzer_ng.seeds.loader import SeedKB, load_seed_kb

logger = logging.getLogger(__name__)


class AnalyzerService:
    """Owns the AMQP consumers, worker pool, and reply publisher."""

    def __init__(
        self,
        config: AppConfig,
        app_version: str,
        *,
        pg_pool: ConnectionPool | None = None,
        metrics: Metrics | None = None,
        emb_model_ver: str | None = None,
        gbm_model_ver: str | None = None,
    ) -> None:
        self._config = config
        self.version = app_version
        self.emb_model_ver = emb_model_ver
        self.gbm_model_ver = gbm_model_ver
        self._pg_pool = pg_pool
        self._metrics = metrics or Metrics()
        self.seed_kb: SeedKB | None = None

        prefix = config.analyzer_ng_queue_prefix
        self._all_queue = f"{prefix}all"
        self._train_queue = f"{prefix}train"
        self._dlq = f"{prefix}dlq"

        self._seq = itertools.count(1)
        self._seq_lock = threading.Lock()

        self._publisher = ReplyPublisher(AmqpConnection(config, app_version), self._dlq)
        self._pool = WorkerPool(
            Dispatcher(),
            self._publisher,
            workers=config.analyzer_ng_workers,
            queue_size=config.analyzer_ng_queue_size,
            max_retries=config.amqp_handler_max_retries,
            metrics=self._metrics,
        )
        self._consumers = [
            Consumer(
                "all",
                self._all_queue,
                AmqpConnection(config, app_version),
                self._pool,
                self._publisher,
                route_filter=lambda rk: rk != "train_models",
                next_seq=self._next_seq,
                prefetch=config.analyzer_ng_prefetch,
            ),
            Consumer(
                "train",
                self._train_queue,
                AmqpConnection(config, app_version),
                self._pool,
                self._publisher,
                route_filter=lambda rk: rk == "train_models",
                next_seq=self._next_seq,
                prefetch=config.analyzer_ng_prefetch,
            ),
        ]
        self._ready = threading.Event()

    def _next_seq(self) -> int:
        with self._seq_lock:
            return next(self._seq)

    def set_pg_pool(self, pool: ConnectionPool) -> None:
        """Attach the PostgreSQL pool once it is opened (after DB bootstrap)."""
        self._pg_pool = pool

    def load_seed_kb(self) -> SeedKB:
        """Startup step 6 (spec 01 §6): load & validate the seed failure-mode KB.

        Idempotent — validates the packaged catalog and binds the KBStore for
        lazy per-project copies. Safe to call again (re-load never duplicates DB
        rows; the catalog is package data). Requires the PG pool to be attached.
        """
        kb_store = PgKBStore(self._pg_pool) if self._pg_pool is not None else None
        self.seed_kb = load_seed_kb(kb_store)
        return self.seed_kb

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        """Start publisher, worker pool, and consumers; flip readiness true."""
        self._publisher.start()
        self._pool.start()
        for consumer in self._consumers:
            consumer.start()
        self._ready.set()
        logger.info(
            "analyzer-ng %s ready (emb=%s, gbm=%s)",
            self.version,
            self.emb_model_ver,
            self.gbm_model_ver,
        )

    def shutdown(self, drain_timeout: float = 30.0) -> None:
        """Graceful shutdown (spec §6): stop consuming, drain workers, flush
        replies, close connections."""
        self._ready.clear()
        for consumer in self._consumers:
            consumer.stop(timeout=drain_timeout)
        self._pool.shutdown(timeout=drain_timeout)
        self._publisher.shutdown(timeout=10.0)

    # -- HealthProvider ---------------------------------------------------- #
    def is_ready(self) -> bool:
        return self._ready.is_set()

    def amqp_ok(self) -> bool:
        return all(consumer.alive for consumer in self._consumers)

    def pg_ok(self) -> bool:
        return check_pg(self._pg_pool)

    def thread_statuses(self) -> list[dict[str, Any]]:
        tasks = [
            {
                "routing_key": item.routing_key,
                "correlation_id": item.correlation_id,
                "send_time": item.send_time,
            }
            for item in self._pool.running_tasks()
        ]
        statuses: list[dict[str, Any]] = [
            {
                "name": consumer.name,
                "status": "alive" if consumer.alive else "dead",
                "running_tasks": {"number": 0, "tasks": []},
            }
            for consumer in self._consumers
        ]
        statuses.append(
            {
                "name": "workers",
                "status": "alive",
                "running_tasks": {"number": len(tasks), "tasks": tasks},
            }
        )
        return statuses

    def render_metrics(self) -> tuple[bytes, str]:
        self._metrics.queue_depth.set(self._pool.queue_depth)
        self._metrics.pg_pool_in_use.set(pool_in_use(self._pg_pool))
        return self._metrics.render()
