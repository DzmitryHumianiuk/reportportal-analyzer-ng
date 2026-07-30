"""The cold-start rubric (spec 04 §4.4), as one table with no dependencies.

Its own module, and deliberately import-free beyond the standard library, so the
Inspector image can carry this single file the way it already carries
``queries.py`` and show a reader the same rules the analyzer applied. One source,
no second copy to drift.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# --------------------------------------------------------------------------- #
# The rubric (spec 04 §4.4), as ONE table.
# --------------------------------------------------------------------------- #
# Every consumer is derived from this: the prompt text the model reads, the
# rule -> label agreement check, the schema enum, and the plain-English name a
# reader is shown instead of a bare "R6". Before this, the prompt text and the
# label map lived apart and could drift; adding a rule now means adding one row.
#
# ``name`` is user-facing copy: plain global English, says what happened rather
# than how the analyzer is built, because it lands in the modal and in the defect
# comment that outlives it. ``when`` is the prompt line, and stays terse because
# the model reads it.
#
# Production data will grow this table. Keep ids stable once shipped: they are
# stored in llm_event.output and in suggestion.features, so renumbering would
# orphan history.


class RubricRule(NamedTuple):
    label: str
    confidence: str
    name: str
    when: str


RUBRIC: dict[str, RubricRule] = {
    "R1": RubricRule(
        "pb",
        "high",
        "Check failed on the app's answer",
        "Assertion/expectation failure (AssertionError, expected vs actual, matcher diff)\n"
        "    AND failing frame in test code -> pb, high  (the check fired; product output wrong)",
    ),
    "R2": RubricRule(
        "ab",
        "med",
        "Setup or test data failed",
        "Assertion failure in setup/fixture/beforeEach, or test-data preparation error -> ab, med",
    ),
    "R3": RubricRule(
        "ab",
        "high",
        "Element not found on the page",
        "Element/locator not found, stale element, selector timeout (UI automation) -> ab, high",
    ),
    "R4": RubricRule(
        "ab",
        "high",
        "Test code did not build",
        "Compilation, NoSuchMethod/ClassNotFound/ImportError, missing dependency in\n"
        "    test harness -> ab, high",
    ),
    "R5": RubricRule(
        "ab",
        "med",
        "Empty value inside test code",
        "NullPointer/TypeError with root frame inside test code or page objects -> ab, med",
    ),
    "R6": RubricRule(
        "si",
        "high",
        "Could not reach the service",
        "Connection refused/reset, UnknownHost/DNS, broker/database unreachable,\n"
        "    TLS handshake to shared infra -> si, high",
    ),
    "R7": RubricRule(
        "si",
        "high",
        "Gateway returned an error",
        "HTTP 502/503/504 or gateway/proxy errors from any dependency -> si, high",
    ),
    "R8": RubricRule(
        "si",
        "med",
        "Runner ran out of space or memory",
        'Disk full, out-of-memory of the runner/container, "no space left",\n'
        "    environment variable/config missing -> si, med",
    ),
    "R9": RubricRule(
        "pb",
        "med",
        "App rejected a valid request",
        "HTTP 4xx (except 408/429) returned by the application under test to a\n"
        "    well-formed request -> pb, med",
    ),
    "R10": RubricRule(
        "pb",
        "high",
        "App failed with a server error",
        "HTTP 5xx (500) or unhandled exception with root frame in application code -> pb, high",
    ),
    "R11": RubricRule(
        "pb",
        "low",
        "App answered too slowly",
        "Timeout waiting on the application (not infra) with otherwise healthy calls -> pb, low",
    ),
    "R12": RubricRule(
        "nd",
        "high",
        "Passed when run again",
        "Failure disappeared on retry in the same launch (retry_passed=true), or known\n"
        "    timing/order flakiness pattern -> nd, high",
    ),
    "R13": RubricRule(
        "si",
        "med",
        "Rate limit or quota reached",
        "429/408, rate limiting, quota exceeded on shared services -> si, med",
    ),
    "R14": RubricRule(
        "ab",
        "low",
        "Parallel runs clashed",
        'Concurrency artifacts: deadlock/optimistic-lock/"database is locked" in test\n'
        "    parallel runs -> ab, low",
    ),
}

# Rule -> the label it must produce (client-side agreement check).
RUBRIC_LABEL = {rid: r.label for rid, r in RUBRIC.items()}

# Rule -> the plain-English name shown to a reader. "none" is a real schema value:
# the model may report that nothing matched.
RUBRIC_NAME = {rid: r.name for rid, r in RUBRIC.items()}


def rubric_rule_name(rule_id: str | None) -> str:
    """The reader-facing name of a rubric rule, or '' when there is none."""
    if not rule_id or rule_id == "none":
        return ""
    return RUBRIC_NAME.get(rule_id, "")


# Rule ids leaking into the prose. The model volunteers them even though the
# prompt never asked, and the sentence they land in becomes the defect comment,
# read by people who never opened the modal and have nothing to resolve "R6"
# against. Strip the reference and tidy what it leaves behind: a dangling
# connective ("aligning with", "matching") and doubled punctuation.
_RULE_ID_RE = re.compile(r"\(?\bR(?:[1-9]|1[0-4])\b\)?", re.IGNORECASE)

# The connective the reference hung off, which reads as debris once it is gone.
_DANGLING_RE = re.compile(
    r"[,;(]?\s*\b(?:in\s+line\s+with|consistent\s+with|align(?:ing|ed|s)?\s+with|"
    r"according\s+to|as\s+per|per|matching|matches|matched|match|rule)\b\s*[,;)]?\s*$",
    re.IGNORECASE,
)

# The same connective sitting at the FRONT of the clause ("Matches R12, the
# failure passed on retry").
_LEADING_RE = re.compile(
    r"^\s*(?:this\s+)?(?:in\s+line\s+with|consistent\s+with|align(?:ing|ed|s)?\s+with|"
    r"according\s+to|as\s+per|per|matching|matches|matched|match|rule)\b\s*[,;:]?\s*",
    re.IGNORECASE,
)

_MIN_WORDS = 3


def strip_rule_ids(reason: str | None) -> str:
    """Remove rubric rule references from a reader-facing sentence.

    Sentence by sentence, so a clause that existed only to cite the rule is
    dropped whole rather than left as debris ("Rule R3 applies." would otherwise
    become "applies."). A sentence that still says something keeps its wording.
    """
    if not reason:
        return ""
    kept: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+", reason.strip()):
        if not _RULE_ID_RE.search(raw):
            kept.append(raw.strip())
            continue
        tail = raw[-1] if raw[-1:] in ".!?" else ""
        body = raw[:-1] if tail else raw
        body = _RULE_ID_RE.sub("", body)
        body = _DANGLING_RE.sub("", body)
        body = _LEADING_RE.sub("", body)
        body = re.sub(r"\s+([.,;])", r"\1", body)
        body = re.sub(r"([,;])\s*([,;])", r"\1", body)
        body = re.sub(r"\s{2,}", " ", body).strip(" ,;()")
        if len(body.split()) < _MIN_WORDS:
            continue
        kept.append(body[0].upper() + body[1:] + tail)
    return " ".join(k for k in kept if k).strip()
