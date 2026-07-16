"""Log-excerpt middle-out truncation and token budgeting (spec 04 §2.4, §3.2).

The excerpt is the only elastic slice of the 4096-token context budget (§2.4).
:func:`middle_out` keeps the root exception block whole, a head and tail window,
and elides the middle behind a literal marker line — shrinking the windows (and,
only as a last resort, the root block tail-first) until the excerpt fits its
token slice.
"""

from __future__ import annotations

import math
import re

# §3.2 window sizes.
HEAD_LINES = 15
TAIL_LINES = 10
ROOT_MAX_LINES = 40

_CAUSED_BY_RE = re.compile(r"^\s*Caused by:", re.IGNORECASE)
# A line that looks like an exception header: a dotted class name ending in
# Exception/Error/Throwable, optionally followed by a message.
_EXCEPTION_RE = re.compile(
    r"^\s*(?:[A-Za-z_][\w$]*\.)*[A-Za-z_][\w$]*(?:Exception|Error|Throwable)\b"
)


def estimate_tokens(text: str) -> int:
    """Estimate token count via the §2.4 chars/4 + 10% headroom heuristic.

    Uses integer arithmetic (``chars * 11 / 40`` == ``chars/4 * 1.1``) so the
    estimate is exact and free of float rounding surprises at slice boundaries."""
    return math.ceil(len(text) * 11 / 40)


def _root_block_bounds(lines: list[str]) -> tuple[int, int]:
    """Return ``[start, end)`` of the root exception block, or ``(-1, -1)``.

    Prefers the **last** ``Caused by:`` chain element (the root cause); falls back
    to the first exception-header line. The block runs to ``ROOT_MAX_LINES`` or a
    blank-line boundary, whichever comes first, and is always kept whole (§3.2).
    """
    anchor = -1
    for i, line in enumerate(lines):
        if _CAUSED_BY_RE.match(line):
            anchor = i
    if anchor == -1:
        for i, line in enumerate(lines):
            if _EXCEPTION_RE.match(line):
                anchor = i
                break
    if anchor == -1:
        return (-1, -1)
    end = anchor + 1
    limit = min(anchor + ROOT_MAX_LINES, len(lines))
    while end < limit and lines[end].strip() != "":
        end += 1
    return (anchor, end)


def _render(lines: list[str], keep: set[int]) -> str:
    """Render kept lines in order, inserting one elision marker per gap."""
    out: list[str] = []
    n = len(lines)
    prev = -1
    for i in range(n):
        if i not in keep:
            continue
        if prev != -1 and i > prev + 1:
            elided = i - prev - 1
            out.append(f"... [{elided} lines elided by analyzer] ...")
        out.append(lines[i])
        prev = i
    return "\n".join(out)


def middle_out(text: str, max_tokens: int) -> str:
    """Middle-out truncate ``text`` to fit ``max_tokens`` (§3.2).

    Keeps the head window, the whole root exception block, and the tail window;
    elides the middle. If still over budget, shrinks the tail then head windows
    before finally truncating the root block tail-first.
    """
    if estimate_tokens(text) <= max_tokens:
        return text
    lines = text.split("\n")
    n = len(lines)
    rs, re_ = _root_block_bounds(lines)

    head, tail = HEAD_LINES, TAIL_LINES
    root_len = re_ - rs if rs != -1 else 0

    while True:
        keep: set[int] = set()
        if head > 0:
            keep |= set(range(0, min(head, n)))
        if tail > 0:
            keep |= set(range(max(n - tail, 0), n))
        if rs != -1 and root_len > 0:
            keep |= set(range(rs, rs + root_len))
        rendered = _render(lines, keep)
        if estimate_tokens(rendered) <= max_tokens:
            return rendered
        # Shrink elastic windows first (tail, then head), root block last.
        if tail > 0:
            tail -= 1
        elif head > 0:
            head -= 1
        elif root_len > 1:
            root_len -= 1  # truncate root block tail-first
        else:
            return rendered  # minimal excerpt; cannot shrink further
