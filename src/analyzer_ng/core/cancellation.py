"""Per-task cooperative cancellation / watchdog (spec 01 §8.2).

A task running longer than ``AMQP_HANDLER_TASK_TIMEOUT`` (default 600 s) must be
cancelled and treated as a failure. §8.2 specifies two enforcement mechanisms:

* a **cooperative cancel flag** checked between pipeline stages — threads cannot
  be force-killed in CPython, so :func:`task_watchdog` arms a
  :class:`threading.Timer` that sets a contextvar-propagated
  :class:`threading.Event`, and the pipeline calls :func:`raise_if_cancelled`
  between stages (the worker also fails a handler that overran without polling);
* a **PostgreSQL ``statement_timeout`` set to the remaining budget** on the store
  connections used by handler work, so a blocked SQL statement is interrupted by
  the server rather than leaking the worker thread past the deadline. The store
  layer reads :func:`statement_timeout_ms` when it checks out a connection;
  migrations/startup run outside any watchdog and therefore stay exempt.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_cancel_var: ContextVar[threading.Event | None] = ContextVar("cancel_event", default=None)
_deadline_var: ContextVar[float | None] = ContextVar("cancel_deadline", default=None)


class TaskTimeout(Exception):
    """Raised when a task exceeds ``AMQP_HANDLER_TASK_TIMEOUT`` (spec §8.2)."""


@contextmanager
def task_watchdog(timeout: float | None) -> Iterator[threading.Event | None]:
    """Arm a cooperative watchdog for ``timeout`` seconds (no-op if falsy/<=0).

    Records the monotonic deadline so store connections can derive the remaining
    budget for ``statement_timeout``. Yields the cancel :class:`~threading.Event`
    (or ``None`` when disabled) so the caller can check ``event.is_set()`` after
    running the handler.
    """
    if not timeout or timeout <= 0:
        yield None
        return
    event = threading.Event()
    timer = threading.Timer(timeout, event.set)
    timer.daemon = True
    timer.start()
    cancel_token = _cancel_var.set(event)
    deadline_token = _deadline_var.set(time.monotonic() + timeout)
    try:
        yield event
    finally:
        timer.cancel()
        _cancel_var.reset(cancel_token)
        _deadline_var.reset(deadline_token)


def raise_if_cancelled() -> None:
    """Raise :class:`TaskTimeout` if the current task's watchdog has fired."""
    event = _cancel_var.get()
    if event is not None and event.is_set():
        raise TaskTimeout("task exceeded AMQP_HANDLER_TASK_TIMEOUT")


def remaining_budget() -> float | None:
    """Seconds left before the active watchdog fires, or ``None`` if none is armed."""
    deadline = _deadline_var.get()
    if deadline is None:
        return None
    return deadline - time.monotonic()


def statement_timeout_ms() -> int | None:
    """PG ``statement_timeout`` (ms) for the remaining budget, or ``None`` if unbounded.

    Clamped to at least 1 ms so an already-exhausted budget cancels SQL immediately
    rather than disabling the timeout (``0`` = unlimited in PostgreSQL).
    """
    remaining = remaining_budget()
    if remaining is None:
        return None
    return max(1, int(remaining * 1000))
