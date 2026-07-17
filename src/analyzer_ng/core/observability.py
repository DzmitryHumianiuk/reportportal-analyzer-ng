"""Structured-logging context (spec 01 §9.1).

The mandatory per-message log fields — ``correlation_id`` (message correlation id
or a generated uuid), ``routing_key``, ``project``, and ``duration_ms`` — are
propagated through :mod:`contextvars` so any log record emitted while handling a
message carries them without threading them through every call.

The worker pool binds ``correlation_id``/``routing_key`` around each task
(:func:`bind_request`); handlers set ``project`` once it is known
(:func:`set_project`); ``duration_ms`` is attached to the completion log line via
``logging`` ``extra=`` (it is not a contextvar — it exists only on completion
records, spec §9.1). The JSON formatter (``main._JsonFormatter``) reads
:func:`current_log_fields` plus ``record.duration_ms``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
routing_key_var: ContextVar[str | None] = ContextVar("routing_key", default=None)
project_var: ContextVar[int | None] = ContextVar("project", default=None)


@contextmanager
def bind_request(correlation_id: str | None, routing_key: str | None) -> Iterator[str]:
    """Bind the per-message log context for the duration of one task.

    A missing/empty ``correlation_id`` is replaced with a generated uuid (spec
    §9.1: "message correlation_id or generated uuid"). ``project`` is reset to
    ``None`` on entry and restored on exit, so a handler that sets it via
    :func:`set_project` never leaks into the next task on the same worker thread.
    Yields the effective correlation id.
    """
    effective = correlation_id or str(uuid.uuid4())
    cid_token = correlation_id_var.set(effective)
    rk_token = routing_key_var.set(routing_key)
    pid_token = project_var.set(None)
    try:
        yield effective
    finally:
        correlation_id_var.reset(cid_token)
        routing_key_var.reset(rk_token)
        project_var.reset(pid_token)


def set_project(project: int | None) -> None:
    """Record the project id for subsequent log lines in this task's context."""
    project_var.set(project)


def current_log_fields() -> dict[str, object]:
    """The bound structured-log fields present in the current context (spec §9.1)."""
    fields: dict[str, object] = {}
    cid = correlation_id_var.get()
    if cid:
        fields["correlation_id"] = cid
    rk = routing_key_var.get()
    if rk:
        fields["routing_key"] = rk
    pid = project_var.get()
    if pid is not None:
        fields["project"] = pid
    return fields
