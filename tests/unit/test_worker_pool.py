"""Worker-pool policy tests (spec 01 §8.1–§8.2).

Drives :class:`WorkerPool` with an in-memory fake publisher (no broker) to verify
reply routing, the retry-then-dead-letter policy, non-retryable short-circuits,
and priority ordering.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from analyzer_ng.amqp.dispatcher import Dispatcher, ProcessingItem, WorkerPool


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


def _pool(publisher: FakePublisher, **kwargs: Any) -> WorkerPool:
    pool = WorkerPool(
        Dispatcher(),
        publisher,
        workers=kwargs.pop("workers", 1),
        max_retries=kwargs.pop("max_retries", 3),
        retry_delays=kwargs.pop("retry_delays", [0.0, 0.0, 0.0]),
        **kwargs,
    )
    pool.start()
    return pool


def _wait(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


def test_successful_reply_is_published() -> None:
    pub = FakePublisher()
    pool = _pool(pub)
    try:
        pool.submit(
            ProcessingItem(1, 1, "noop_echo", reply_to="rq", correlation_id="c1", body="hi")
        )  # noqa: E501
        _wait(lambda: pub.replies)
        assert pub.replies == [("rq", "c1", "hi")]
    finally:
        pool.shutdown(timeout=2)


def test_inline_mode_processes_on_caller_thread_without_workers() -> None:
    # DEBUG_MODE (spec 01 §5.1): inline mode starts no worker threads and processes
    # each item synchronously on the submitting thread; the reply is already
    # published by the time submit() returns.
    pub = FakePublisher()
    pool = WorkerPool(Dispatcher(), pub, inline=True)
    pool.start()  # no-op: no worker threads spawned
    caller = threading.get_ident()
    seen: list[int] = []

    class _Spy(Dispatcher):
        def process(self, routing_key: str, body: Any) -> Any:
            seen.append(threading.get_ident())
            return super().process(routing_key, body)

    pool._dispatcher = _Spy()  # type: ignore[attr-defined]
    pool.submit(ProcessingItem(1, 1, "noop_echo", reply_to="rq", correlation_id="c", body="hi"))
    assert pub.replies == [("rq", "c", "hi")]  # done synchronously, no _wait needed
    assert seen == [caller]  # ran on the caller thread, not a worker


def test_no_reply_when_reply_to_absent() -> None:
    pub = FakePublisher()
    pool = _pool(pub)
    try:
        pool.submit(ProcessingItem(1, 1, "noop_echo", reply_to=None, correlation_id="", body="hi"))
        time.sleep(0.2)
        assert pub.replies == []
    finally:
        pool.shutdown(timeout=2)


def test_train_models_no_reply_even_with_reply_to() -> None:
    pub = FakePublisher()
    pool = _pool(pub)
    try:
        pool.submit(
            ProcessingItem(
                1,
                1,
                "train_models",
                reply_to="rq",
                correlation_id="c",
                body={"model_type": 2, "project": 1},
            )
        )
        time.sleep(0.2)
        assert pub.replies == []
        assert pub.dead_letters == []
    finally:
        pool.shutdown(timeout=2)


def test_handler_failure_retries_then_dead_letters() -> None:
    pub = FakePublisher()
    pool = _pool(pub, max_retries=3, retry_delays=[0.0, 0.0, 0.0])
    try:
        pool.submit(
            ProcessingItem(1, 1, "noop_fail", reply_to="rq", correlation_id="c", body={"x": 1})
        )  # noqa: E501
        _wait(lambda: pub.dead_letters)
        body, headers = pub.dead_letters[0]
        assert headers["x-routing-key"] == "noop_fail"
        assert headers["x-retries"] == 3
        assert pub.replies == []
    finally:
        pool.shutdown(timeout=2)


def test_validation_error_is_non_retryable_and_dead_lettered() -> None:
    pub = FakePublisher()
    pool = _pool(pub, retry_delays=[10.0, 10.0, 10.0])  # long delays prove no retry happened
    try:
        bad = ProcessingItem(1, 1, "index", reply_to="rq", correlation_id="c", body=[{"bad": 1}])
        pool.submit(bad)
        _wait(lambda: pub.dead_letters, timeout=2)
        assert pub.dead_letters[0][1]["x-retries"] == 0
    finally:
        pool.shutdown(timeout=2)


def test_unknown_key_is_acked_without_dead_letter() -> None:
    pub = FakePublisher()
    pool = _pool(pub)
    try:
        unknown = ProcessingItem(1, 1, "does_not_exist", reply_to="rq", correlation_id="c", body={})
        pool.submit(unknown)
        time.sleep(0.2)
        assert pub.dead_letters == []
        assert pub.replies == []
    finally:
        pool.shutdown(timeout=2)


def test_priority_ordering_processes_lower_priority_first() -> None:
    pub = FakePublisher()
    # Single worker; submit high-priority (later) then low-priority (earlier).
    pool = WorkerPool(Dispatcher(), pub, workers=1, retry_delays=[0.0])
    # Pre-load the queue before starting workers so ordering is deterministic.
    pool.submit(
        ProcessingItem(200, 2, "noop_echo", reply_to="rq", correlation_id="late", body="late")
    )  # noqa: E501
    pool.submit(
        ProcessingItem(100, 1, "noop_echo", reply_to="rq", correlation_id="early", body="early")
    )  # noqa: E501
    pool.start()
    try:
        _wait(lambda: len(pub.replies) == 2)
        assert [r[1] for r in pub.replies] == ["early", "late"]
    finally:
        pool.shutdown(timeout=2)
