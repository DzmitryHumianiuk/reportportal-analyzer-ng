"""Routing-key table, ProcessingItem, and the worker pool (spec 01 §4, §8).

The :class:`Dispatcher` owns the routing-key table (adapter -> handler ->
serializer per key, §4.3/§4.4) and turns a ``(routing_key, body)`` pair into an
optional reply string. The :class:`WorkerPool` drains a bounded priority queue of
:class:`ProcessingItem`, runs the dispatcher with the retry/DLQ policy of §8.2,
and hands replies/dead-letters to the publisher.

Handlers themselves live in :mod:`analyzer_ng.core.handlers`, so business logic
stays separate from this transport glue.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Empty, PriorityQueue
from typing import Any, Protocol

from pydantic import ValidationError

from analyzer_ng.amqp.models import (
    DefectUpdate,
    DeleteLaunchesRequest,
    DeleteLogsRequest,
    DeleteTestItemsRequest,
    Launch,
    LaunchInfoForClustering,
    RemoveByDatesRequest,
    SearchLogs,
    TestItemInfo,
    TrainInfo,
)
from analyzer_ng.core import observability as obs
from analyzer_ng.core.cancellation import TaskTimeout, task_watchdog
from analyzer_ng.core.handlers import StubHandlers

logger = logging.getLogger(__name__)


class UnknownRoutingKey(Exception):
    """Raised when a routing key has no entry in the routing table (§4.4)."""

    def __init__(self, routing_key: str) -> None:
        super().__init__(f"Unknown routing key '{routing_key}'")
        self.routing_key = routing_key


# --------------------------------------------------------------------------- #
# ProcessingItem — the unit of work on the priority queue.
# --------------------------------------------------------------------------- #
@dataclass(order=True)
class ProcessingItem:
    """A parsed message queued for a worker.

    Ordering is ``(priority, number)`` — priority is ``timestamp_in_ms`` (lower =
    older = first, §8.1); ``number`` is the monotonic arrival sequence and breaks
    ties deterministically. All other fields are excluded from comparison.
    """

    priority: int
    number: int
    routing_key: str = field(compare=False)
    reply_to: str | None = field(compare=False, default=None)
    correlation_id: str = field(compare=False, default="")
    body: Any = field(compare=False, default=None)
    retries: int = field(compare=False, default=0)
    send_time: float | None = field(compare=False, default=None)


# --------------------------------------------------------------------------- #
# Serializers (spec §4.3).
# --------------------------------------------------------------------------- #
def serialize_model_list(results: list[Any]) -> str:
    """List-of-model replies (``analyze``/``search``/``suggest``)."""
    return json.dumps([model.model_dump() for model in results])


def serialize_model(model: Any) -> str:
    """Single-model replies (``index``/``cluster``/``suggest_patterns``)."""
    return model.model_dump_json()


def serialize_scalar_str(value: Any) -> str:
    """Plain scalar replies — stringified count / echoed int."""
    return str(value)


def serialize_json(value: Any) -> str:
    """Raw-JSON replies (``defect_update`` id list; deprecated ``{}`` / ``1``)."""
    return json.dumps(value)


# --------------------------------------------------------------------------- #
# Request adapters (raw JSON -> typed request). Raise ValidationError on bad shape.
# --------------------------------------------------------------------------- #
def _adapt_launches(body: Any) -> list[Launch]:
    return [Launch(**launch) for launch in body]


def _identity(body: Any) -> Any:
    return body


@dataclass(frozen=True)
class RouteSpec:
    """One routing-table entry: how to adapt, handle, and serialize a key."""

    adapt: Callable[[Any], Any]
    handler: Callable[[Any], Any]
    serializer: Callable[[Any], str] | None


class Handlers(Protocol):
    """Structural type for the handler set the routing table binds to."""

    def index(self, launches: list[Launch]) -> Any: ...
    def analyze(self, launches: list[Launch]) -> Any: ...
    def suggest(self, info: TestItemInfo) -> Any: ...
    def cluster(self, info: LaunchInfoForClustering) -> Any: ...
    def search(self, request: SearchLogs) -> Any: ...
    def delete(self, project: int) -> Any: ...
    def clean(self, request: DeleteLogsRequest) -> Any: ...
    def item_remove(self, request: DeleteTestItemsRequest) -> Any: ...
    def launch_remove(self, request: DeleteLaunchesRequest) -> Any: ...
    def remove_by_launch_start_time(self, request: RemoveByDatesRequest) -> Any: ...
    def remove_by_log_time(self, request: RemoveByDatesRequest) -> Any: ...
    def defect_update(self, request: DefectUpdate) -> Any: ...
    def train_models(self, info: TrainInfo) -> Any: ...
    def suggest_patterns(self, project: int) -> Any: ...
    def namespace_finder(self, launches: list[Launch]) -> Any: ...
    def index_suggest_info(self, items: Any) -> Any: ...
    def remove_suggest_info(self, value: int) -> Any: ...
    def update_suggest_info(self, payload: Any) -> Any: ...
    def remove_models(self, payload: Any) -> Any: ...
    def get_model_info(self, payload: Any) -> Any: ...
    def noop_sleep(self, seconds: Any) -> Any: ...
    def noop_echo(self, payload: Any) -> Any: ...
    def noop_fail(self, payload: Any) -> Any: ...


def build_routes(handlers: Handlers) -> dict[str, RouteSpec]:
    """Build the routing-key table (spec §4.4), binding handlers to adapters and
    serializers. ``serializer=None`` means "never reply" (§3.4)."""
    return {
        "index": RouteSpec(_adapt_launches, handlers.index, serialize_model),
        "analyze": RouteSpec(_adapt_launches, handlers.analyze, serialize_model_list),
        "suggest": RouteSpec(lambda b: TestItemInfo(**b), handlers.suggest, serialize_model_list),
        "cluster": RouteSpec(
            lambda b: LaunchInfoForClustering(**b), handlers.cluster, serialize_model
        ),
        "search": RouteSpec(lambda b: SearchLogs(**b), handlers.search, serialize_model_list),
        "delete": RouteSpec(int, handlers.delete, serialize_scalar_str),
        "clean": RouteSpec(lambda b: DeleteLogsRequest(**b), handlers.clean, serialize_scalar_str),
        "item_remove": RouteSpec(
            lambda b: DeleteTestItemsRequest(**b), handlers.item_remove, serialize_scalar_str
        ),
        "launch_remove": RouteSpec(
            lambda b: DeleteLaunchesRequest(**b), handlers.launch_remove, serialize_scalar_str
        ),
        "remove_by_launch_start_time": RouteSpec(
            lambda b: RemoveByDatesRequest(**b),
            handlers.remove_by_launch_start_time,
            serialize_scalar_str,
        ),
        "remove_by_log_time": RouteSpec(
            lambda b: RemoveByDatesRequest(**b),
            handlers.remove_by_log_time,
            serialize_scalar_str,
        ),
        "defect_update": RouteSpec(
            lambda b: DefectUpdate(**b), handlers.defect_update, serialize_json
        ),
        "train_models": RouteSpec(lambda b: TrainInfo(**b), handlers.train_models, None),
        "suggest_patterns": RouteSpec(int, handlers.suggest_patterns, serialize_model),
        "namespace_finder": RouteSpec(_adapt_launches, handlers.namespace_finder, None),
        # Deprecated: must ALWAYS reply {} (spec 01 §4.4). Parse leniently (identity)
        # so a malformed payload can never raise ValidationError → DLQ-without-reply;
        # the handler ignores the shape and returns {} unconditionally.
        "index_suggest_info": RouteSpec(_identity, handlers.index_suggest_info, serialize_json),
        "remove_suggest_info": RouteSpec(int, handlers.remove_suggest_info, serialize_scalar_str),
        "update_suggest_info": RouteSpec(_identity, handlers.update_suggest_info, serialize_json),
        "remove_models": RouteSpec(_identity, handlers.remove_models, None),
        "get_model_info": RouteSpec(_identity, handlers.get_model_info, None),
        "noop_sleep": RouteSpec(_identity, handlers.noop_sleep, None),
        "noop_echo": RouteSpec(_identity, handlers.noop_echo, serialize_scalar_str),
        "noop_fail": RouteSpec(_identity, handlers.noop_fail, None),
    }


class Dispatcher:
    """Validates, handles, and serializes a single message (pure, thread-safe)."""

    def __init__(self, routes: dict[str, RouteSpec] | None = None) -> None:
        self.routes = routes if routes is not None else build_routes(StubHandlers())

    def process(self, routing_key: str, body: Any) -> str | None:
        """Run adapt -> handler -> serialize for ``routing_key``.

        Returns the reply string, or ``None`` when the route never replies or the
        handler produced ``None`` (spec §3.4). Raises :class:`UnknownRoutingKey`
        for unregistered keys and ``pydantic.ValidationError`` for bad payloads —
        both non-retryable (§8.2).
        """
        spec = self.routes.get(routing_key)
        if spec is None:
            raise UnknownRoutingKey(routing_key)
        request = spec.adapt(body)
        response = spec.handler(request)
        if response is None or spec.serializer is None:
            return None
        return spec.serializer(response)


# --------------------------------------------------------------------------- #
# Worker pool (spec §8.1–§8.2).
# --------------------------------------------------------------------------- #
class ReplySink(Protocol):
    """What the worker pool needs from the publisher."""

    def reply(self, reply_to: str, correlation_id: str, body: str) -> None: ...
    def dead_letter(self, body: bytes, headers: dict[str, Any]) -> None: ...


class MetricsSink(Protocol):
    """What the worker pool needs from the metrics registry."""

    def observe_request(self, routing_key: str, outcome: str, seconds: float) -> None: ...


class _NullMetrics:
    def observe_request(self, routing_key: str, outcome: str, seconds: float) -> None:
        return None


# Errors that must never be retried (§8.2): a bad payload or an unknown key will
# fail identically on every attempt; a watchdog timeout must not re-run the
# expensive task three more times.
_NON_RETRYABLE = (ValidationError, UnknownRoutingKey, TaskTimeout)


class WorkerPool:
    """A fixed set of worker threads draining a bounded priority queue.

    This is analyzer-ng's ``ThreadPoolExecutor`` equivalent (spec §1.1): N daemon
    workers so the bounded :class:`~queue.PriorityQueue` can order tasks by
    ``(timestamp_in_ms, arrival_seq)`` — an ordering a bare executor cannot
    provide. Backpressure is the consumer blocking on ``submit`` when the queue is
    full (§8.1).
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        publisher: ReplySink,
        *,
        workers: int = 2,
        queue_size: int = 100,
        max_retries: int = 3,
        retry_delays: list[float] | None = None,
        metrics: MetricsSink | None = None,
        task_timeout: float = 600.0,
    ) -> None:
        self._dispatcher = dispatcher
        self._publisher = publisher
        self._workers = max(1, workers)
        self._queue: PriorityQueue[ProcessingItem] = PriorityQueue(maxsize=queue_size)
        self._max_retries = max_retries
        # Per-task watchdog budget (§8.2); <=0 disables it.
        self._task_timeout = task_timeout
        # 1 s / 2 s / 4 s by default (§8.2); overridable so tests stay fast.
        self._retry_delays = retry_delays if retry_delays is not None else [1.0, 2.0, 4.0]
        self._metrics: MetricsSink = metrics or _NullMetrics()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._running_lock = threading.Lock()
        self._running: dict[int, ProcessingItem] = {}

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        for i in range(self._workers):
            thread = threading.Thread(target=self._run, name=f"worker-{i}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def submit(self, item: ProcessingItem) -> None:
        """Enqueue an item, blocking while the queue is full (backpressure)."""
        self._queue.put(item, block=True)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def running_tasks(self) -> list[ProcessingItem]:
        with self._running_lock:
            return list(self._running.values())

    def shutdown(self, timeout: float = 30.0) -> None:
        """Stop accepting work and wait up to ``timeout`` for in-flight tasks.

        Idle workers block only on a short ``get`` timeout, so setting the stop
        flag is enough to drain them — no sentinel (a ``None`` sentinel cannot be
        ordered in a :class:`~queue.PriorityQueue`).
        """
        self._stop.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

    # -- worker loop ------------------------------------------------------- #
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except Empty:
                continue
            worker_id = threading.get_ident()
            with self._running_lock:
                item.send_time = time.time()
                self._running[worker_id] = item
            try:
                self._process(item)
            finally:
                with self._running_lock:
                    self._running.pop(worker_id, None)

    def _process(self, item: ProcessingItem) -> None:
        # Bind the §9.1 log context (correlation_id + routing_key) for this task.
        with obs.bind_request(item.correlation_id, item.routing_key):
            self._process_bound(item)

    def _process_bound(self, item: ProcessingItem) -> None:
        started = time.monotonic()
        attempt = 0
        while True:
            try:
                with task_watchdog(self._task_timeout) as cancel_event:
                    reply = self._dispatcher.process(item.routing_key, item.body)
                    # A handler that overran without polling is still a failure (§8.2).
                    if cancel_event is not None and cancel_event.is_set():
                        raise TaskTimeout("task exceeded AMQP_HANDLER_TASK_TIMEOUT")
            except UnknownRoutingKey as exc:
                # Benign: ack + WARN + no reply, no dead-letter (§4.4).
                logger.warning("Unknown routing key '%s', ignoring", exc.routing_key)
                self._metrics.observe_request(
                    item.routing_key, "unknown", time.monotonic() - started
                )
                return
            except _NON_RETRYABLE as exc:
                self._fail(item, exc, started, retried=attempt)
                return
            except Exception as exc:  # noqa: BLE001 — retry policy owns the decision
                if attempt >= self._max_retries:
                    self._fail(item, exc, started, retried=attempt)
                    return
                delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
                logger.warning(
                    "Handler for '%s' failed (attempt %d/%d): %s; retrying in %.0fs",
                    item.routing_key,
                    attempt + 1,
                    self._max_retries,
                    exc,
                    delay,
                )
                attempt += 1
                item.retries = attempt
                if self._stop.wait(delay):
                    return  # shutting down mid-retry
                continue

            self._publish_reply(item, reply)
            duration_ms = int((time.monotonic() - started) * 1000)
            self._metrics.observe_request(item.routing_key, "success", time.monotonic() - started)
            # §9.1 completion line — carries duration_ms plus the bound context fields.
            logger.info(
                "handled '%s'", item.routing_key, extra={"duration_ms": duration_ms}
            )
            return

    def _publish_reply(self, item: ProcessingItem, reply: str | None) -> None:
        if reply is None or not item.reply_to:
            return
        try:
            self._publisher.reply(item.reply_to, item.correlation_id, reply)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to enqueue reply for '%s': %s", item.routing_key, exc)

    def _fail(self, item: ProcessingItem, exc: Exception, started: float, *, retried: int) -> None:
        logger.error(
            "Task '%s' failed after %d retries (correlation_id=%s): %s",
            item.routing_key,
            retried,
            item.correlation_id,
            exc,
            exc_info=exc,
            extra={"duration_ms": int((time.monotonic() - started) * 1000)},
        )
        self._metrics.observe_request(item.routing_key, "failed", time.monotonic() - started)
        self._dead_letter(item, exc)

    def _dead_letter(self, item: ProcessingItem, exc: Exception) -> None:
        headers = {
            "x-error": f"{type(exc).__name__}: {exc}",
            "x-routing-key": item.routing_key,
            "x-retries": item.retries,
        }
        try:
            body = json.dumps(item.body).encode("utf-8")
            self._publisher.dead_letter(body, headers)
        except Exception as dlq_exc:  # noqa: BLE001
            logger.exception("Failed to dead-letter '%s': %s", item.routing_key, dlq_exc)
