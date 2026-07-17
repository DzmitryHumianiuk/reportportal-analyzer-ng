"""Middle-out excerpt truncation + token budget tests (spec 04 §2.4, §3.2)."""

from __future__ import annotations

from analyzer_ng.llm.excerpt import estimate_tokens, middle_out

_MARKER = "lines elided by analyzer"


def _make_log(n_lines: int) -> str:
    lines = [f"line {i} some stack frame at com.acme.Thing.method({i})" for i in range(n_lines)]
    # Put a clear root exception near the end so the truncator must keep it.
    lines.insert(
        n_lines - 20,
        "Caused by: java.net.ConnectException: Connection refused at host:5432",
    )
    return "\n".join(lines)


def test_short_text_returned_unchanged() -> None:
    text = "line1\nline2\nCaused by: X\nline4"
    assert middle_out(text, max_tokens=10_000) == text


def test_middle_out_keeps_head_root_and_tail_with_marker() -> None:
    log = _make_log(10_000)
    out = middle_out(log, max_tokens=2200)
    assert estimate_tokens(out) <= 2200
    # Head window preserved.
    assert "line 0 " in out
    # Root exception block preserved whole.
    assert "Caused by: java.net.ConnectException" in out
    # Tail window preserved.
    assert "line 9999 " in out
    # Elision marker present.
    assert _MARKER in out


def test_budget_enforced_on_huge_log() -> None:
    out = middle_out(_make_log(50_000), max_tokens=800)
    assert estimate_tokens(out) <= 800
    assert "Caused by:" in out  # root survives window shrink


def test_single_exception_when_no_caused_by() -> None:
    lines = [f"noise line {i}" for i in range(5000)]
    lines[2500] = "java.lang.NullPointerException: boom at com.acme.A.b(A.java:1)"
    out = middle_out("\n".join(lines), max_tokens=500)
    assert "NullPointerException" in out
    assert estimate_tokens(out) <= 500


def test_estimate_tokens_has_headroom() -> None:
    # chars/4 * 1.1 heuristic — 400 chars ≈ 110 tokens.
    assert estimate_tokens("x" * 400) == 110
