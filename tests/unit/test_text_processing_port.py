"""Unit tests for the ported text-processing primitives (spec 03 §1.2).

Cases marked "legacy" are adapted verbatim from the legacy
``test/unit/utils/test_text_processing.py`` inline (fixture-file-free)
parametrizations, so ported behaviour is pinned to the original analyzer.
"""

from __future__ import annotations

import pytest

from analyzer_ng.preprocessing import text_processing as tp


# --- legacy inline parametrizations --------------------------------------
@pytest.mark.parametrize(
    "message, expected",
    [
        ("\t \r\n ", "\n "),
        ("\r\n", "\n"),
        ("\n", "\n"),
        ("   \n", "\n"),
        (" \r\n", "\n"),
    ],
)
def test_unify_line_endings(message, expected):
    assert tp.unify_line_endings(message) == expected


@pytest.mark.parametrize(
    "message, expected",
    [
        ("\t \r\n ", " \r\n"),
        ("\r\n", "\r\n"),
        ("\n", "\n"),
        ("   \n", "\n"),
        (" \r\n", " \r\n"),
        ("   ", " "),
        ("  　", " "),
        ("a  　b", "a b"),
        ("   \n   ", "\n"),
    ],
)
def test_unify_spaces(message, expected):
    assert tp.unify_spaces(message) == expected


@pytest.mark.parametrize(
    "url, expected_url",
    [
        (
            "amqp://user:password@10.68.56.88:5672/analyzer?heartbeat=30",
            "amqp://10.68.56.88:5672/analyzer?heartbeat=30",
        ),
        ("amqps://rpuser:fkkf0+4pUn@192.68.56.88:5672", "amqps://192.68.56.88:5672"),
        ("https://test123:aa-bb_cc@msgbroker.example.com/", "https://msgbroker.example.com/"),
        ("https://test123:aa%20bb%40cc@msgbroker.example.com/", "https://msgbroker.example.com/"),
    ],
)
def test_remove_credentials_from_url(url, expected_url):
    assert tp.remove_credentials_from_url(url) == expected_url


@pytest.mark.parametrize(
    "test_texts, expected_result",
    [
        (["a", "a", "a"], [2]),
        (["a", "b", "a"], [1, 2]),
        (["a", "b", "b"], [0, 2]),
        (["a", "b", "a", "b"], [2, 3]),
        (["a", "b", "a", "b", "a"], [3, 4]),
        (["a", "b", "a", "b", "a", "c"], [3, 4, 5]),
    ],
)
def test_find_last_unique_texts(test_texts, expected_result):
    assert tp.find_last_unique_texts(0.95, test_texts) == expected_result


@pytest.mark.parametrize(
    "base_text, other_texts, expected_scores",
    [
        (
            "org.openqa.selenium.TimeoutException: ErrorCodec.decode",
            ["org.openqa.selenium.TimeoutException: ErrorCodec.decode"],
            [(1.0, False)],
        ),
        (
            "org.openqa.selenium.TimeoutException",
            ["java.lang.NullPointerException"],
            [(0.11, False)],
        ),
        (
            "org.openqa.selenium.TimeoutException: ErrorCodec.decode",
            ["org.openqa.selenium.TimeoutException: Different error"],
            [(0.71, False)],
        ),
        ("", [""], [(0.0, True)]),
        ("some text", [""], [(0.0, False)]),
        (
            "org.openqa.selenium.TimeoutException",
            [
                "org.openqa.selenium.TimeoutException",
                "java.lang.NullPointerException",
                "org.openqa.selenium.WebDriverException",
            ],
            [(1.0, False), (0.11, False), (0.60, False)],
        ),
    ],
)
def test_calculate_text_similarity_basic_cases(base_text, other_texts, expected_scores):
    actual = tp.calculate_text_similarity(base_text, other_texts)
    assert len(actual) == len(expected_scores)
    for a, e in zip(actual, expected_scores, strict=True):
        assert a.similarity == pytest.approx(e[0], abs=0.01)
        assert a.both_empty == e[1]


@pytest.mark.parametrize(
    "text, expected_contains",
    [
        (
            "org.openqa.selenium.TimeoutException: ErrorCodec.decode(ErrorCodec.java:167)",
            ["timeout", "exception", "error", "codec", "decode"],
        ),
        (
            'AppiumBy.iOSNsPredicate: label CONTAINS[c] "Continue"',
            ["appium", "predicate", "label", "contains", "continue"],
        ),
        ("CamelCaseExample_with_snake_case", ["camel", "case", "example", "snake"]),
        ("XMLHttpRequest.send()", ["xml", "http", "request", "send"]),
        (
            "build_info: version: '4.33.0', revision: '2c6aaad03a'",
            ["build", "info", "version", "revision", "4", "33", "0"],
        ),
    ],
)
def test_preprocess_text_for_similarity(text, expected_contains):
    processed = " ".join(tp.preprocess_text_for_similarity(text))
    assert processed == processed.lower()
    tokens = set(processed.split())
    for word in expected_contains:
        assert word in tokens
    assert not any(ch in processed for ch in ".,;:!?()[]{}\"'")


def test_calculate_text_similarity_no_other_texts():
    assert tp.calculate_text_similarity("base text", []) == []


# --- targeted behaviour checks for the fingerprint-critical functions ----
def test_get_found_exceptions_java_and_python():
    assert tp.get_found_exceptions("caused by java.lang.NullPointerException at foo") == [
        "java.lang.NullPointerException"
    ]
    found = tp.get_found_exceptions("Traceback ... ValueError bad AssertionError: nope")
    assert "AssertionError" in found


def test_remove_numbers_tags_and_strips():
    assert tp.remove_numbers("code 404 line 12") == f"code {tp.NUMBER_TAG} line {tp.NUMBER_TAG}"


def test_status_codes_extraction():
    assert tp.get_unique_potential_status_codes("Response status code: 503\nstatus code: 503") == [
        "503"
    ]


def test_status_codes_assertion_and_response_idioms():
    # 2026-07-18 extension: assertion/HTTP-response idioms the legacy patterns miss.
    xunit = "Assert.Equal() Failure: Values differ\nExpected: 201\nActual:   503"
    assert tp.get_unique_potential_status_codes(xunit) == ["201", "503"]
    # a server-500 of the same assertion produces a *different* status set — this is
    # the discriminant that keeps a pb/500 from over-matching an si/503 (same hash).
    assert tp.get_unique_potential_status_codes("Expected: 201\nActual:   500") == ["201", "500"]
    assert tp.get_potential_status_codes("StatusCode=503") == ["503"]
    assert tp.get_potential_status_codes("StatusCode: 404") == ["404"]
    assert tp.get_potential_status_codes("HTTP/1.1 503 Service Unavailable") == ["503"]
    assert tp.get_potential_status_codes("GET /account/profile -> 500 in 58 ms") == ["500"]


def test_status_codes_no_false_positives_on_bare_numbers():
    # ports, line numbers, counts, ids and 2-digit values must NOT become status codes.
    for text in (
        "at Foo.bar(Foo.java:96)",
        "listening on port 8080",
        "retryAfter:30",
        "user U-70455",
        "Expected: 42 items but found 3",
        "value 12345 exceeded the ceiling",
        "Expected: Completed\nActual:   RejectedFeatureDisabled",
    ):
        assert tp.get_potential_status_codes(text) == [], text


def test_extract_urls_and_paths():
    text = "connect to https://api.example.com/v1 failed at /opt/app/main.py"
    urls = tp.extract_urls(text)
    assert urls == ["https://api.example.com/v1"]
    paths = tp.extract_paths(tp.remove_urls(text, urls))
    assert "/opt/app/main.py" in paths


def test_is_line_from_stacktrace_java_and_python():
    assert tp.is_line_from_stacktrace("\tat com.example.Foo.bar(Foo.java:42)")
    assert tp.is_line_from_stacktrace('  File "/app/foo.py", line 10, in bar')
    assert not tp.is_line_from_stacktrace("Just a plain error message")


def test_detect_log_description_and_stacktrace_splits_java():
    log = (
        "java.lang.RuntimeException: boom\n"
        "\tat com.example.Foo.bar(Foo.java:42)\n"
        "\tat com.example.Baz.qux(Baz.java:7)"
    )
    msg, stack = tp.detect_log_description_and_stacktrace(log)
    assert "RuntimeException" in msg
    assert "com.example.Foo.bar" in stack


def test_is_line_from_stacktrace_ignores_line_column_position():
    # Newman/JS report a parse error position as "at <line>:<col>". The trailing
    # ":1" (a column) must NOT be mistaken for a ":<lineno>" frame marker, or the
    # informative line is shunted into the stacktrace half and lost from the
    # signature (leaving only the useless "<html>" fragment behind).
    assert not tp.is_line_from_stacktrace("Unexpected token '<' at 1:1")
    # Real file:line frames must still be detected.
    assert tp.is_line_from_stacktrace("\tat com.example.Foo.bar(Foo.java:42)")


def test_detect_log_description_keeps_newman_json_parse_message():
    # RP/Postman failure: the endpoint returned HTML instead of JSON, so Newman's
    # JSON parser fails. The descriptive first line must stay in the description
    # so the failure signature is meaningful (not just "<html>").
    log = "Unexpected token '<' at 1:1\n<html>\n^"
    msg, _stack = tp.detect_log_description_and_stacktrace(log)
    assert "Unexpected token" in msg


def test_preprocess_test_item_name_camel_split():
    out = tp.preprocess_test_item_name("MyClass.shouldDoThing_case")
    assert "should" in out.lower().split()
