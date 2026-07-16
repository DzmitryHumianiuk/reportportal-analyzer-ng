"""Signature / fingerprint tests (spec 03 §3, acceptance §11 Signature/fingerprint)."""

from __future__ import annotations

from analyzer_ng.ml import signature as sig
from analyzer_ng.ml.drain import DrainManager
from analyzer_ng.ml.signature import (
    exception_fingerprint,
    extract_exception_chain,
    normalize_class,
    select_frames,
)
from analyzer_ng.preprocessing.pipeline import LogInput, build_item_signature

NPE_LOG = (
    "2024-01-01 10:00:00,123 ERROR com.example - Request "
    "550e8400-e29b-41d4-a716-446655440000 failed\n"
    "java.lang.NullPointerException: null value 0xdeadbeef1234\n"
    "\tat com.example.Service.handle(Service.java:42)\n"
    "\tat com.example.Worker.run(Worker.java:88)"
)
# Same failure, different timestamp / UUID / hex / line numbers.
NPE_LOG_VARIANT = (
    "2025-07-16 22:14:59,999 ERROR com.example - Request "
    "11112222-3333-4444-5555-666677778888 failed\n"
    "java.lang.NullPointerException: null value 0xcafebabe9876\n"
    "\tat com.example.Service.handle(Service.java:7)\n"
    "\tat com.example.Worker.run(Worker.java:101)"
)


def _sig(name: str, raw: str):
    return build_item_signature(name, [LogInput(raw)], DrainManager())


# --- fingerprint unit level ----------------------------------------------
def test_normalize_class_strips_synthetic_suffixes():
    assert normalize_class("com.example.Foo$$Lambda$12/0x0") == "com.example.Foo"
    assert normalize_class("com.example.Foo$1") == "com.example.Foo"
    assert normalize_class("com.example.Foo$$EnhancerBySpringCGLIB$$abcd") == "com.example.Foo"


def test_exception_fingerprint_zero_when_no_class_no_frame():
    assert exception_fingerprint([], []) == 0


def test_exception_fingerprint_deterministic():
    a = exception_fingerprint(["java.lang.NullPointerException"], ["com.example.Foo.bar"])
    b = exception_fingerprint(["java.lang.NullPointerException"], ["com.example.Foo.bar"])
    assert a == b != 0


def test_root_cause_ordering_puts_root_first():
    text = (
        "com.example.ServiceException: wrapper\n"
        "Caused by: java.lang.NullPointerException: root\n"
        "\tat com.example.Foo.bar(Foo.java:10)"
    )
    chain = extract_exception_chain(
        ["com.example.ServiceException", "java.lang.NullPointerException"], text
    )
    assert chain[0].endswith("NullPointerException")


def test_select_frames_drops_framework_and_caps_three():
    stack = (
        "\tat com.example.Service.handle(Service.java:42)\n"
        "\tat org.junit.runner.Run(Run.java:1)\n"
        "\tat com.example.Worker.run(Worker.java:88)\n"
        "\tat com.example.Db.query(Db.java:9)\n"
        "\tat java.base.Thread.run(Thread.java:1)"
    )
    frames = select_frames(stack)
    assert "org.junit.runner.Run" not in frames
    assert all(not f.startswith("java.") for f in frames)
    assert len(frames) == 3
    assert frames[0] == "com.example.Service.handle"


# --- item level ----------------------------------------------------------
def test_error_hash_invariant_to_volatile_values():
    a = _sig("MyTest.shouldWork", NPE_LOG)
    b = _sig("MyTest.shouldWork", NPE_LOG_VARIANT)
    assert a.exception_fp == b.exception_fp != 0
    assert a.error_hash == b.error_hash


def test_signature_document_has_expected_fields():
    result = _sig("LoginTest.shouldLogin", NPE_LOG)
    lines = dict(line.split(": ", 1) for line in result.signature_text.split("\n"))
    assert "TEST" in lines and "EXC" in lines and "MSG" in lines
    assert lines["EXC"].startswith("java.lang.NullPointerException")
    assert "FRAMES" in lines
    assert "com.example.Service.handle" in lines["FRAMES"]


def test_error_hash_stable_across_runs():
    first = _sig("T", NPE_LOG)
    second = _sig("T", NPE_LOG)
    assert (first.exception_fp, first.error_hash, first.signature_text) == (
        second.exception_fp,
        second.error_hash,
        second.signature_text,
    )


def test_empty_item_has_zero_hashes():
    result = build_item_signature("T", [LogInput("", log_level=40000)], DrainManager())
    assert result.exception_fp == 0
    assert result.error_hash == 0
    assert result.signature_text == ""


def test_pathological_large_log_is_capped():
    big = "com.example.BoomException: bad\n" + ("filler token value here\n" * 100_000)
    result = build_item_signature("Huge.test", [LogInput(big)], DrainManager())
    assert len(result.signature_text) <= sig.SIGNATURE_MAX_CHARS
    # TEST and EXC are never truncated.
    assert result.signature_text.startswith("TEST: ")
    assert "EXC: com.example.BoomException" in result.signature_text


def test_below_error_level_logs_filtered_out():
    result = build_item_signature(
        "T",
        [LogInput("some info noise", log_level=20000), LogInput(NPE_LOG, log_level=40000)],
        DrainManager(),
    )
    assert result.exception_fp != 0
    assert "NullPointerException" in result.signature_text
