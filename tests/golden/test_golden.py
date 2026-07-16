"""Golden-file tests for preprocessing + signatures (spec 03 §11).

Fixed fixture logs map to exact cleaned outputs, signature documents,
``exception_fp`` and ``error_hash``. The expected values are committed under
``tests/golden/*.json``. Regenerate intentionally with::

    ANALYZER_UPDATE_GOLDEN=1 pytest tests/golden/test_golden.py

Determinism (byte-identical across runs) is asserted directly by building each
signature twice with an independent Drain instance.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from analyzer_ng.ml.drain import DrainManager
from analyzer_ng.preprocessing.pipeline import LogInput, build_item_signature, clean_log

_GOLDEN_DIR = Path(__file__).parent
_UPDATE = os.environ.get("ANALYZER_UPDATE_GOLDEN") == "1"

# --- fixtures ------------------------------------------------------------
# (name, [(message, level), ...]) covering java / python / js / selenium /
# assertions / http / timeouts / mixed, incl. multi-log items.
FIXTURES: list[tuple[str, list[tuple[str, int]]]] = [
    (
        "JavaNpeTest.shouldHandleNull",
        [
            (
                "2024-05-01 10:00:00,123 ERROR c.e.Svc - Request "
                "550e8400-e29b-41d4-a716-446655440000 failed\n"
                "java.lang.NullPointerException: value was null\n"
                "\tat com.example.Service.handle(Service.java:42)\n"
                "\tat com.example.Worker.run(Worker.java:88)",
                40000,
            )
        ],
    ),
    (
        "JavaCausedByTest.nested",
        [
            (
                "com.example.ServiceException: wrapper failure\n"
                "Caused by: java.lang.IllegalStateException: bad state\n"
                "\tat com.example.Core.process(Core.java:11)\n"
                "\tat com.example.Api.call(Api.java:7)",
                40000,
            )
        ],
    ),
    (
        "PythonValueErrorTest.test_parse",
        [
            (
                "Traceback (most recent call last):\n"
                '  File "/app/svc/parser.py", line 88, in parse\n'
                "    return int(value)\n"
                "ValueError: invalid literal for int() with base 10: 'abc'",
                40000,
            )
        ],
    ),
    (
        "SeleniumStaleTest.clickSubmit",
        [
            (
                "org.openqa.selenium.StaleElementReferenceException: stale element\n"
                "\tat com.example.pages.LoginPage.submit(LoginPage.java:55)\n"
                "\tat org.openqa.selenium.remote.RemoteWebElement.click(RemoteWebElement.java:1)",
                40000,
            )
        ],
    ),
    (
        "JsAssertTest.shouldEqual",
        [
            (
                "AssertionError [ERR_ASSERTION]: expected 200 but was 500\n"
                "    at Object.<anonymous> (/app/test/api.spec.js:12:5)",
                40000,
            )
        ],
    ),
    (
        "HttpTest.serverError",
        [("Response status code: 503 Service Unavailable from gateway", 40000)],
    ),
    (
        "TimeoutTest.slowResponse",
        [("Test timed out after 30000 ms waiting for element to appear", 40000)],
    ),
    (
        "DbConnTest.pool",
        [
            (
                "org.postgresql.util.PSQLException: connection pool exhausted\n"
                "\tat com.example.db.Pool.acquire(Pool.java:120)",
                40000,
            )
        ],
    ),
    (
        "MixedMultiLogTest.flow",
        [
            ("INFO warming up caches for tenant east", 20000),  # dropped (below ERROR)
            ("java.lang.IllegalArgumentException: bad arg foo", 40000),
            (
                "java.lang.IllegalArgumentException: bad arg foo\n"
                "\tat com.example.Validator.check(Validator.java:9)",
                40000,
            ),
        ],
    ),
    (
        "KafkaTest.brokerDown",
        [("org.apache.kafka.common.errors.TimeoutException: broker not available", 40000)],
    ),
    (
        "EmptyItemTest.noLogs",
        [("   ", 40000)],
    ),
    (
        "OomTest.heap",
        [
            (
                "java.lang.OutOfMemoryError: Java heap space\n"
                "\tat com.example.report.Builder.build(Builder.java:200)",
                40000,
            )
        ],
    ),
]

# Raw single logs for the preprocessing golden (cleaned views + features).
PREPROC_FIXTURES: dict[str, str] = {
    "java_npe": (
        "2024-05-01 10:00:00,123 ERROR c.e.Svc - failure at "
        "https://api.example.com/v1/orders\n"
        "java.lang.NullPointerException: null\n"
        "\tat com.example.Service.handle(Service.java:42)"
    ),
    "python_trace": (
        "Traceback (most recent call last):\n"
        '  File "/app/svc/parser.py", line 88, in parse\n'
        "ValueError: bad input"
    ),
    "http_code": "Response status code: 404 Not Found for /api/users/123",
    "webdriver_noise": (
        "org.openqa.selenium.TimeoutException: wait failed\n"
        "Build info: version: '4.1.0', revision: 'abc123'\n"
        "\tat com.example.Page.wait(Page.java:5)"
    ),
    "uuid_hex": ("Session 550e8400-e29b-41d4-a716-446655440000 with token 0xdeadbeef1234 expired"),
}


def _build_signature(name: str, logs: list[tuple[str, int]]) -> dict:
    result = build_item_signature(
        name, [LogInput(m, log_level=lv) for m, lv in logs], DrainManager()
    )
    return {
        "signature_text": result.signature_text,
        "exception_fp": result.exception_fp,
        "error_hash": result.error_hash,
        "exc_classes": result.exc_classes,
        "frames": result.frames,
        "template_hashes": result.template_hashes,
        "status_codes": result.status_codes,
        "is_assertion": result.is_assertion,
        "has_stacktrace": result.has_stacktrace,
    }


def _build_preproc(raw: str) -> dict:
    c = clean_log(raw)
    return {
        "unified": c.unified,
        "msg": c.msg,
        "stack": c.stack,
        "exceptions": c.exceptions,
        "status_codes": c.status_codes,
        "urls": c.urls,
        "paths": c.paths,
        "has_stacktrace": c.has_stacktrace,
    }


def _load_or_write(path: Path, computed: dict) -> dict:
    if _UPDATE or not path.exists():
        path.write_text(json.dumps(computed, indent=2, ensure_ascii=False) + "\n")
    return json.loads(path.read_text())


def test_golden_signatures():
    computed = {name: _build_signature(name, logs) for name, logs in FIXTURES}
    expected = _load_or_write(_GOLDEN_DIR / "expected_signatures.json", computed)
    assert computed == expected


def test_golden_preprocessing():
    computed = {k: _build_preproc(v) for k, v in PREPROC_FIXTURES.items()}
    expected = _load_or_write(_GOLDEN_DIR / "expected_preprocessing.json", computed)
    assert computed == expected


def test_signatures_are_deterministic_across_runs():
    # Byte-identical across two independent builds (fresh Drain instances).
    for name, logs in FIXTURES:
        first = _build_signature(name, logs)
        second = _build_signature(name, logs)
        assert first == second, name
