"""Structured-log field propagation (spec 01 §9.1)."""

from __future__ import annotations

import json
import logging

import pytest

from analyzer_ng.core import observability as obs
from analyzer_ng.main import _JsonFormatter


@pytest.fixture(autouse=True)
def _clean_context() -> None:
    """Reset the ambient log context.

    In production every task runs inside ``bind_request`` (worker pool), which
    resets the contextvars on exit; tests that call handlers directly can leave
    ``project`` set, so start each test from a clean slate.
    """
    obs.correlation_id_var.set(None)
    obs.routing_key_var.set(None)
    obs.project_var.set(None)


def test_current_log_fields_empty_outside_a_request() -> None:
    assert obs.current_log_fields() == {}


def test_bind_request_sets_correlation_and_routing_key() -> None:
    with obs.bind_request("corr-1", "index"):
        obs.set_project(123)
        fields = obs.current_log_fields()
        assert fields == {"correlation_id": "corr-1", "routing_key": "index", "project": 123}
    # Context is cleared on exit (no leakage to the next task).
    assert obs.current_log_fields() == {}


def test_json_formatter_includes_context_and_duration() -> None:
    formatter = _JsonFormatter("9.9.9")
    record = logging.LogRecord(
        name="analyzer_ng.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="handled '%s'",
        args=("index",),
        exc_info=None,
    )
    record.duration_ms = 42  # attached via extra= on completion lines
    with obs.bind_request("corr-7", "index"):
        obs.set_project(5)
        payload = json.loads(formatter.format(record))
    assert payload["correlation_id"] == "corr-7"
    assert payload["routing_key"] == "index"
    assert payload["project"] == 5
    assert payload["duration_ms"] == 42
    assert payload["app_version"] == "9.9.9"
    assert payload["msg"] == "handled 'index'"


def test_json_formatter_omits_absent_fields() -> None:
    formatter = _JsonFormatter("1.0.0")
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname=__file__, lineno=1, msg="hi", args=(), exc_info=None
    )
    payload = json.loads(formatter.format(record))
    assert "correlation_id" not in payload
    assert "duration_ms" not in payload
