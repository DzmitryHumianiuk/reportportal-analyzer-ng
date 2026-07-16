"""Per-task watchdog + structured-log context binding (spec 01 §8.2, §9.1).

These are the two REQUIRED carry-overs deferred from the T1.2 review to "real
pipeline": the worker pool must enforce ``AMQP_HANDLER_TASK_TIMEOUT`` and bind
``correlation_id``/``routing_key`` for the duration of each task.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from analyzer_ng.amqp.dispatcher import Dispatcher, ProcessingItem, WorkerPool, build_routes
from analyzer_ng.core import observability as obs
from analyzer_ng.core.cancellation import raise_if_cancelled
from analyzer_ng.core.handlers import StubHandlers


class FakePublisher:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str, str]] = []
        self.dead_letters: list[tuple[bytes, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def reply(self, reply_to: str, correlation_id: str, body: str) -> None:
        with self._lock:
            self.replies.append((reply_to, correlation_id, body))

    def dead_letter(self, body: bytes, headers: dict[str, Any]) -> None:
        with self._lock:
            self.dead_letters.append((body, headers))


def _wait(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


class _CapturingHandlers(StubHandlers):
    """Records the bound log context observed while a handler runs."""

    def __init__(self) -> None:
        self.captured: dict[str, object] = {}

    def noop_echo(self, payload: Any) -> Any:
        self.captured = dict(obs.current_log_fields())
        return payload


class _CooperativeHandlers(StubHandlers):
    """A handler that polls the cancel flag between stages, like the pipeline."""

    def noop_echo(self, payload: Any) -> Any:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            raise_if_cancelled()  # cooperative cancel point (§8.2)
            time.sleep(0.02)
        return payload


def test_watchdog_fails_task_that_overruns_the_budget() -> None:
    """A handler running past the budget is cancelled and treated as a failure."""
    pub = FakePublisher()
    pool = WorkerPool(
        Dispatcher(build_routes(_CooperativeHandlers())),
        pub,
        workers=1,
        retry_delays=[0.0, 0.0, 0.0],
        task_timeout=0.1,
    )
    pool.start()
    try:
        pool.submit(
            ProcessingItem(1, 1, "noop_echo", reply_to="rq", correlation_id="c", body="hi")
        )
        _wait(lambda: pub.dead_letters)
        _body, headers = pub.dead_letters[0]
        assert "TaskTimeout" in headers["x-error"]
        assert headers["x-retries"] == 0  # non-retryable: no re-runs of a 600 s task
        assert pub.replies == []  # RP sees a timeout (legacy behavior)
    finally:
        pool.shutdown(timeout=2)


def test_watchdog_disabled_when_timeout_non_positive() -> None:
    """A zero/negative budget disables the watchdog (task completes normally)."""
    pub = FakePublisher()
    pool = WorkerPool(
        Dispatcher(),
        pub,
        workers=1,
        retry_delays=[0.0],
        task_timeout=0.0,
    )
    pool.start()
    try:
        pool.submit(ProcessingItem(1, 1, "noop_echo", reply_to="rq", correlation_id="c", body="ok"))
        _wait(lambda: pub.replies)
        assert pub.replies == [("rq", "c", "ok")]
        assert pub.dead_letters == []
    finally:
        pool.shutdown(timeout=2)


def test_log_context_bound_during_handler_execution() -> None:
    """correlation_id + routing_key are visible to logs while the handler runs."""
    handlers = _CapturingHandlers()
    pub = FakePublisher()
    pool = WorkerPool(Dispatcher(build_routes(handlers)), pub, workers=1)
    pool.start()
    try:
        pool.submit(
            ProcessingItem(1, 1, "noop_echo", reply_to="rq", correlation_id="corr-42", body="x")
        )
        _wait(lambda: handlers.captured)
        assert handlers.captured["correlation_id"] == "corr-42"
        assert handlers.captured["routing_key"] == "noop_echo"
    finally:
        pool.shutdown(timeout=2)


def test_missing_correlation_id_is_replaced_with_a_uuid() -> None:
    handlers = _CapturingHandlers()
    pub = FakePublisher()
    pool = WorkerPool(Dispatcher(build_routes(handlers)), pub, workers=1)
    pool.start()
    try:
        pool.submit(ProcessingItem(1, 1, "noop_echo", reply_to="rq", correlation_id="", body="x"))
        _wait(lambda: handlers.captured)
        assert handlers.captured["correlation_id"]  # a generated uuid, never empty
    finally:
        pool.shutdown(timeout=2)
