"""Log-filtering tests (spec 03 §1.1, acceptance §11 Preprocessing/Filtering)."""

from __future__ import annotations

from analyzer_ng.preprocessing.pipeline import (
    ERROR_LOGGING_LEVEL,
    LogInput,
    clean_log,
    filter_item_logs,
)

DISTINCT = [
    "NullPointerException in payment processor during checkout",
    "Timeout connecting to inventory database replica",
    "Invalid signature on incoming webhook payload",
    "Kafka broker not available for order events topic",
    "Disk quota exceeded while writing audit log",
    "Permission denied opening TLS certificate store",
    "Deadlock detected updating user balance rows",
    "Rate limit exceeded calling external shipping API",
    "Configuration property missing for redis cluster",
    "ClassNotFound loading custom serializer plugin",
    "OutOfMemoryError building large report export",
    "Connection reset by peer on metrics endpoint",
    "StaleElementReferenceException clicking submit button",
    "AssertionError expected active but was disabled",
    "SSLHandshakeException verifying upstream host",
]


def test_filter_drops_below_error_dedupes_and_caps():
    # 10 INFO logs (dropped) + 15 distinct ERROR logs + 5 exact duplicates of
    # the first five -> 20 ERROR logs, 5 near-duplicates. Expect the 15 unique.
    info = [LogInput(f"info noise line {i}", log_level=20000) for i in range(10)]
    errors = [LogInput(m, log_level=ERROR_LOGGING_LEVEL) for m in DISTINCT]
    dups = [LogInput(m, log_level=ERROR_LOGGING_LEVEL) for m in DISTINCT[:5]]

    kept = filter_item_logs(info + errors + dups)

    assert len(kept) == 15
    assert set(kept) == set(DISTINCT)


def test_filter_empty_messages_dropped():
    logs = [LogInput("   ", log_level=40000), LogInput("real error here", log_level=40000)]
    assert filter_item_logs(logs) == ["real error here"]


def test_cap_keeps_last_n():
    logs = [LogInput(f"distinct error kind {i} alpha beta", log_level=40000) for i in range(25)]
    kept = filter_item_logs(logs, max_logs=20)
    assert len(kept) == 20
    # "Last 20" — the tail of the surviving list.
    assert kept[-1] == "distinct error kind 24 alpha beta"


def test_no_error_logs_returns_empty():
    logs = [LogInput("info", log_level=20000), LogInput("debug", log_level=10000)]
    assert filter_item_logs(logs) == []


def test_clean_log_extracts_features():
    raw = (
        "2024-01-01 10:00:00 ERROR - java.lang.IllegalStateException: bad at "
        "https://api.example.com/x\n"
        "\tat com.example.Foo.bar(Foo.java:12)\n"
        "\tat com.example.Baz.qux(Baz.java:34)"
    )
    cleaned = clean_log(raw)
    assert cleaned.has_stacktrace
    assert any("IllegalStateException" in e for e in cleaned.exceptions)
    assert cleaned.urls == ["https://api.example.com/x"]
