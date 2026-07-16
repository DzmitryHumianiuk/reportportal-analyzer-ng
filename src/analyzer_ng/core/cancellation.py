"""Per-task cooperative cancellation / watchdog (spec 01 §8.2).

A task running longer than ``AMQP_HANDLER_TASK_TIMEOUT`` (default 600 s) must be
cancelled and treated as a failure. Threads cannot be force-killed in CPython, so
cancellation is **cooperative**: :func:`task_watchdog` arms a :class:`threading.Timer`
that sets a per-task cancel flag (a contextvar-propagated :class:`threading.Event`),
and the pipeline calls :func:`raise_if_cancelled` between stages. The worker pool
additionally checks the flag after the handler returns, so even a handler that
never polls is failed if it overran (spec §8.2 "treated as a failure").
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_cancel_var: ContextVar[threading.Event | None] = ContextVar("cancel_event", default=None)


class TaskTimeout(Exception):
    """Raised when a task exceeds ``AMQP_HANDLER_TASK_TIMEOUT`` (spec §8.2)."""


@contextmanager
def task_watchdog(timeout: float | None) -> Iterator[threading.Event | None]:
    """Arm a cooperative watchdog for ``timeout`` seconds (no-op if falsy/<=0).

    Yields the cancel :class:`~threading.Event` (or ``None`` when disabled) so the
    caller can check ``event.is_set()`` after running the handler.
    """
    if not timeout or timeout <= 0:
        yield None
        return
    event = threading.Event()
    timer = threading.Timer(timeout, event.set)
    timer.daemon = True
    timer.start()
    token = _cancel_var.set(event)
    try:
        yield event
    finally:
        timer.cancel()
        _cancel_var.reset(token)


def raise_if_cancelled() -> None:
    """Raise :class:`TaskTimeout` if the current task's watchdog has fired."""
    event = _cancel_var.get()
    if event is not None and event.is_set():
        raise TaskTimeout("task exceeded AMQP_HANDLER_TASK_TIMEOUT")
