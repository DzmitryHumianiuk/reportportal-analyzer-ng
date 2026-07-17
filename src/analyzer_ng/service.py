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
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg_pool import ConnectionPool

from analyzer_ng.amqp.client import AmqpConnection, ReplyPublisher
from analyzer_ng.amqp.consumer import Consumer
from analyzer_ng.amqp.dispatcher import Dispatcher, WorkerPool, build_routes
from analyzer_ng.config import AppConfig
from analyzer_ng.core.handlers import PipelineHandlers
from analyzer_ng.db.pool import check_pg, pool_in_use
from analyzer_ng.db.repositories.kb import PgKBStore
from analyzer_ng.db.repositories.llm_cache import PgLlmCacheStore
from analyzer_ng.db.repositories.llm_eval import PgLlmComparisonSource
from analyzer_ng.db.repositories.llm_events import PgLlmRoleStateStore
from analyzer_ng.llm.eval import LlmEvalJob
from analyzer_ng.llm.manager import LlmSidecar
from analyzer_ng.llm.wiring import build_extractor_feature_lookup, build_sidecar
from analyzer_ng.metrics import Metrics
from analyzer_ng.ml.reporting import MetricsDailyJob
from analyzer_ng.ml.retrain import REASON_NIGHTLY, NightlyRetrainTimer
from analyzer_ng.seeds.loader import SeedKB, load_seed_kb

logger = logging.getLogger(__name__)

# spec 03 cold-start condition: a project with < 50 label events is "cold" — the
# gate the cold-start rubric role (spec 04 §4.4) fires behind.
COLD_LABEL_EVENTS = 50


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

        # Real index + maintenance handlers (T2.2); analysis routes stay stubs
        # until T2.3. Bound to the store layer now if the pool already exists,
        # else in ``set_pg_pool`` (main.py opens the pool after bootstrap). The
        # routing table binds to this single instance, so a later ``bind`` swaps
        # in the real logic without touching the transport.
        self._sidecar: LlmSidecar | None = None
        self._handlers = PipelineHandlers()
        if pg_pool is not None:
            self._bind_handlers(pg_pool)
        self._publisher = ReplyPublisher(AmqpConnection(config, app_version), self._dlq)
        self._pool = WorkerPool(
            Dispatcher(build_routes(self._handlers)),
            self._publisher,
            workers=config.analyzer_ng_workers,
            queue_size=config.analyzer_ng_queue_size,
            max_retries=config.amqp_handler_max_retries,
            metrics=self._metrics,
            task_timeout=config.amqp_handler_task_timeout,
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
        # Nightly retrain timer (spec §6.5) — started once consumers are up so the
        # store layer is bound; it fires the retrainer at 02:00 UTC.
        self._retrain_timer: NightlyRetrainTimer | None = None

    def _next_seq(self) -> int:
        with self._seq_lock:
            return next(self._seq)

    def set_pg_pool(self, pool: ConnectionPool) -> None:
        """Attach the PostgreSQL pool once it is opened (after DB bootstrap)."""
        self._pg_pool = pool
        self._bind_handlers(pool)

    def _bind_handlers(self, pool: ConnectionPool) -> None:
        """Bind the store layer and, when ANALYZER_LLM_ENABLED, the LLM sidecar.

        The sidecar is constructed flag-gated (spec 04 §0): with the master switch
        off ``build_sidecar`` returns an inert facade (no client/queue/thread) and
        the engine gets no extractor lookup, so the analysis path is byte-identical
        to a build without the sidecar. When enabled, the engine enqueues async LLM
        jobs after each suggestion commit and folds cached extractor features in.
        """
        config = self._config
        sidecar = build_sidecar(
            config,
            pool,
            metrics=self._metrics,
            is_cold=self._make_is_cold(),
        )
        self._sidecar = sidecar
        extractor_features = (
            build_extractor_feature_lookup(PgLlmCacheStore(pool)) if sidecar.enabled else None
        )
        self._handlers.bind(
            pool,
            max_logs=config.analyzer_max_logs_per_item,
            sidecar=sidecar if sidecar.enabled else None,
            extractor_features=extractor_features,
            judge_tau=config.analyzer_llm_judge_tau,
        )

    def _make_is_cold(self):
        """A per-project cold check for the cold-start role (spec 04 §4.4)."""

        def is_cold(project_id: int) -> bool:
            label = self._handlers.label
            if label is None:
                return True
            try:
                return label.count_events_since(project_id=project_id) < COLD_LABEL_EVENTS
            except Exception:  # noqa: BLE001 — never fail an LLM job on the cold check
                logger.exception("cold-project check failed for project %s", project_id)
                return False

        return is_cold

    def load_seed_kb(self) -> SeedKB:
        """Startup step 6 (spec 01 §6): load & validate the seed failure-mode KB.

        Idempotent — validates the packaged catalog and binds the KBStore for
        lazy per-project copies. Safe to call again (re-load never duplicates DB
        rows; the catalog is package data). Requires the PG pool to be attached.
        """
        kb_store = PgKBStore(self._pg_pool) if self._pg_pool is not None else None
        self.seed_kb = load_seed_kb(kb_store)
        # Hand the loaded catalog to the analysis routes (T2.3): seed-mode priors
        # feed the cold decision function and lazy per-project copies.
        self._handlers.set_seed_kb(self.seed_kb)
        return self.seed_kb

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        """Start publisher, worker pool, and consumers; flip readiness true."""
        self._publisher.start()
        self._pool.start()
        for consumer in self._consumers:
            consumer.start()
        self._start_sidecar()
        self._start_retrain_timer()
        self._ready.set()
        logger.info(
            "analyzer-ng %s ready (emb=%s, gbm=%s)",
            self.version,
            self.emb_model_ver,
            self._handlers.gbm_version() or self.gbm_model_ver,
        )

    def _start_sidecar(self) -> None:
        """Probe Ollama (§1.3, non-blocking, degrades) and start the LLM worker.

        A no-op when the master switch is off. The probe is tenacity-retried inside
        the sidecar and never blocks service start — an absent Ollama is a normal,
        silent degradation.
        """
        sidecar = self._sidecar
        if sidecar is None or not sidecar.enabled:
            return
        try:
            sidecar.probe()
        except Exception:  # noqa: BLE001 — degrade, never crash startup
            logger.exception("LLM startup probe failed; continuing degraded")
        sidecar.start()

    def _start_retrain_timer(self) -> None:
        """Start the nightly retrain timer once the store layer is bound (spec §6.5).

        The 02:00 timer drives both the retrain (spec §6.5) and the ``metrics_daily``
        rollup for the day that just ended (spec §10.3).
        """
        retrainer = self._handlers.retrainer
        if retrainer is None:
            return  # store-less (unit) configuration — no learning loop
        stats = self._handlers.stats

        def _trigger() -> None:
            # Enqueue on the single-flight scheduler (non-blocking); the retrain runs
            # off this timer thread and coalesces with any feedback-driven request.
            self._handlers.request_retrain(REASON_NIGHTLY)
            if stats is not None:
                try:
                    yesterday = datetime.now(UTC).date() - timedelta(days=1)
                    MetricsDailyJob(stats, emb_model_ver=self.emb_model_ver).run(yesterday)
                except Exception:  # noqa: BLE001 — reporting must not kill the timer
                    logger.exception("nightly metrics_daily rollup failed")
            self._run_llm_eval()

        self._retrain_timer = NightlyRetrainTimer(_trigger)
        self._retrain_timer.start()

    def _run_llm_eval(self) -> None:
        """Nightly LLM paired comparison + per-project kill-switch (spec 04 §6.2).

        Runs only when the sidecar is on and a pool is bound; disables per-project
        underperforming roles and refreshes the admin ``/metrics`` gauge. Failures
        are swallowed — the eval must never kill the maintenance timer.
        """
        sidecar = self._sidecar
        if sidecar is None or not sidecar.enabled or self._pg_pool is None:
            return
        try:
            LlmEvalJob(
                PgLlmComparisonSource(self._pg_pool),
                PgLlmRoleStateStore(self._pg_pool),
                metrics=self._metrics,
            ).run()
        except Exception:  # noqa: BLE001 — the eval must not kill the timer
            logger.exception("nightly LLM eval failed")

    def shutdown(self, drain_timeout: float = 30.0) -> None:
        """Graceful shutdown (spec §6): stop consuming, drain workers, flush
        replies, close connections."""
        self._ready.clear()
        if self._retrain_timer is not None:
            self._retrain_timer.stop()
        if self._sidecar is not None:
            self._sidecar.stop()  # drain/stop the LLM worker + close the client
        self._handlers.shutdown()  # stop the background retrain scheduler
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

    def metrics_summary(self) -> dict | None:
        """Install-wide metrics_daily rollup for the health endpoint (spec §10.3)."""
        stats = self._handlers.stats
        if stats is None:
            return None
        try:
            since = datetime.now(UTC).date() - timedelta(days=7)
            return stats.metrics_summary(since)
        except Exception:  # noqa: BLE001 — the health endpoint must never raise
            logger.exception("metrics summary failed")
            return None
