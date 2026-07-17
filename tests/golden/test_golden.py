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
    # --- authentic-style fixtures adapted from legacy test_res/test_logs -----
    (
        "AppiumIosTest.tapContinue",
        [
            (
                "org.openqa.selenium.TimeoutException: Expected condition failed: waiting for "
                "presence of element located by: AppiumBy.iOSNsPredicate: label CONTAINS[c] "
                '"Continue" (tried for 10 second(s) with 500 milliseconds interval)\n'
                "\tat org.openqa.selenium.support.ui.FluentWait.until(FluentWait.java:228)\n"
                "\tat com.example.e2eui.element.DynamicElement.tap(DynamicElement.java:617)\n"
                "\tat com.example.e2eui.steps.ChangeDeviceSteps."
                "changeDevice(ChangeDeviceSteps.java:344)",
                40000,
            )
        ],
    ),
    (
        "JsCodeceptTest.resultVisible",
        [
            (
                "AssertionError: 'srl-item-title' is not visible: expected false to deeply "
                "equal true\n"
                "    at Object.handleEventually (node_modules/@pr/aura/lib/common.js:42:11)\n"
                "    at async ElementsHelper.seeVisibleElement "
                "(node_modules/@pr/aura/lib/elementsHelper.js:38:13)",
                40000,
            )
        ],
    ),
    (
        "DotnetVerifyTest.remindersStatus",
        [
            (
                "Verify that Response Codes are equal - Expected: 'BadRequest'; Actual: "
                "'InternalServerError'\n"
                "  Expected: BadRequest\n"
                "  But was:  InternalServerError\n"
                "   at Core.Common.VerifyThat.AreEqual(Object expected, Object actual) in "
                "D:\\src\\TestFramework\\Core\\Common\\VerifyThat.cs:line 168\n"
                "   at Tests.User.GetUserRemindersTests.TestGetRemindersNotLoggedInUser() in "
                "D:\\src\\TestFramework\\Tests\\User\\GetUserRemindersTests.cs:line 115",
                40000,
            )
        ],
    ),
    (
        "RpClusterTest.noAnalyzer",
        [
            (
                "com.epam.ta.reportportal.exception.ReportPortalException: Impossible interact "
                "with integration. There are no analyzer services are deployed.\n"
                "\tat com.epam.ta.reportportal.commons.validation.ErrorTypeBasedRuleValidator."
                "verify(ErrorTypeBasedRuleValidator.java:32)\n"
                "\tat com.epam.ta.reportportal.core.launch.cluster.UniqueErrorGenerator."
                "generate(UniqueErrorGenerator.java:60)",
                40000,
            )
        ],
    ),
    (
        "RestAssuredTest.statusCode500",
        [
            (
                "java.lang.AssertionError: 1 expectation failed.\n"
                "Expected status code <400> but was <500>.\n"
                "\tat java.base/jdk.internal.reflect.NativeConstructorAccessorImpl."
                "newInstance0(Native Method)\n"
                "\tat com.example.api.UserApiTest.shouldRejectBadRequest(UserApiTest.java:88)",
                40000,
            )
        ],
    ),
    (
        "RedisTest.connectionRefused",
        [
            (
                "redis.clients.jedis.exceptions.JedisConnectionException: Failed connecting to "
                "host redis-master:6379\n"
                "\tat redis.clients.jedis.Connection.connect(Connection.java:207)\n"
                "\tat com.example.cache.PriceCache.load(PriceCache.java:54)",
                40000,
            )
        ],
    ),
    (
        "TlsTest.handshake",
        [
            (
                "javax.net.ssl.SSLHandshakeException: PKIX path building failed: unable to find "
                "valid certification path to requested target\n"
                "\tat sun.security.ssl.Alert.createSSLException(Alert.java:131)\n"
                "\tat com.example.http.SecureClient.get(SecureClient.java:77)",
                40000,
            )
        ],
    ),
    (
        "PyFileNotFoundTest.loadFixture",
        [
            (
                "Traceback (most recent call last):\n"
                '  File "/app/tests/conftest.py", line 22, in load_fixture\n'
                "    with open(path) as fh:\n"
                "FileNotFoundError: [Errno 2] No such file or directory: "
                "'/app/tests/data/users.json'",
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
    "java_iae": (
        "2024-05-02 09:12:33 ERROR - java.lang.IllegalArgumentException: id must be positive\n"
        "\tat com.example.Repo.find(Repo.java:12)\n"
        "\tat com.example.Ctrl.get(Ctrl.java:30)"
    ),
    "java_caused_by": (
        "com.example.ServiceException: wrapper\n"
        "Caused by: java.sql.SQLException: connection reset\n"
        "\tat com.example.Db.query(Db.java:9)"
    ),
    "dotnet_nunit": (
        "Verify Response Codes are equal - Expected: 'BadRequest'; Actual: 'InternalServerError'\n"
        "  Expected: BadRequest\n"
        "  But was:  InternalServerError\n"
        "   at Core.Common.VerifyThat.AreEqual(Object expected, Object actual) in "
        "D:\\src\\Common\\VerifyThat.cs:line 168"
    ),
    "js_assert": (
        "AssertionError: expected false to deeply equal true\n"
        "    at Object.handleEventually (node_modules/@pr/aura/lib/common.js:42:11)"
    ),
    "appium_timeout": (
        "org.openqa.selenium.TimeoutException: Expected condition failed: waiting for element\n"
        "\tat org.openqa.selenium.support.ui.FluentWait.until(FluentWait.java:228)\n"
        "\tat com.example.e2eui.element.DynamicElement.tap(DynamicElement.java:617)"
    ),
    "markdown_mode": (
        "!!!MARKDOWN_MODE!!!\nElement should be visible\n```\nAssertionError: not visible\n```"
    ),
    "conn_refused": (
        "java.net.ConnectException: Connection refused (Connection refused)\n"
        "\tat java.base/java.net.Socket.connect(Socket.java:633)"
    ),
    "dns_unknown_host": (
        "java.net.UnknownHostException: api.internal.svc: Name or service not known"
    ),
    "oom_heap": (
        "java.lang.OutOfMemoryError: Java heap space\n"
        "\tat com.example.report.Builder.build(Builder.java:200)"
    ),
    "stack_overflow": "java.lang.StackOverflowError\n\tat com.example.Rec.walk(Rec.java:5)",
    "db_pool": (
        "org.springframework.jdbc.CannotGetJdbcConnectionException: HikariPool-1 - Connection "
        "is not available, request timed out after 30000ms"
    ),
    "kafka_broker": (
        "org.apache.kafka.common.errors.TimeoutException: Topic orders not present in metadata "
        "after 60000 ms"
    ),
    "redis_conn": (
        "redis.clients.jedis.exceptions.JedisConnectionException: Failed connecting to host "
        "redis-master:6379"
    ),
    "tls_cert": (
        "javax.net.ssl.SSLHandshakeException: PKIX path building failed: unable to find valid "
        "certification path to requested target"
    ),
    "py_keyerror": (
        "Traceback (most recent call last):\n"
        '  File "/srv/app/handler.py", line 55, in dispatch\n'
        "KeyError: 'user_id'"
    ),
    "py_filenotfound": (
        "Traceback (most recent call last):\n"
        '  File "/app/tests/conftest.py", line 22, in load_fixture\n'
        "FileNotFoundError: [Errno 2] No such file or directory: '/app/tests/data/users.json'"
    ),
    "npe_typeerror_js": (
        "TypeError: Cannot read properties of undefined (reading 'id')\n"
        "    at Cart.total (/app/src/cart.js:17:9)"
    ),
    "stale_element": (
        "org.openqa.selenium.StaleElementReferenceException: stale element reference: element "
        "is not attached to the page document\n"
        "\tat com.example.pages.LoginPage.submit(LoginPage.java:55)"
    ),
    "assertion_expected_actual": (
        "org.junit.ComparisonFailure: expected:<[ACTIVE]> but was:<[DISABLED]>\n"
        "\tat com.example.StatusTest.shouldBeActive(StatusTest.java:41)"
    ),
    "docker_testcontainers": (
        "org.testcontainers.containers.ContainerLaunchException: Could not find a valid Docker "
        "environment. Please see logs and check configuration"
    ),
    "disk_full": (
        "java.io.IOException: No space left on device\n\tat com.example.Log.write(Log.java:3)"
    ),
    "permission_denied": (
        "PermissionError: [Errno 13] Permission denied: '/var/lib/app/cache/index.lock'"
    ),
    "webdriver_screenshot": (
        "org.openqa.selenium.NoSuchElementException: no such element: Unable to locate element\n"
        "Webdriver screenshot captured: screenshot-123.png\n"
        "\tat com.example.pages.Home.open(Home.java:20)"
    ),
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
