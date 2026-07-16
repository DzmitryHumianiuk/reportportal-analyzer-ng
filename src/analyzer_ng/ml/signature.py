"""Failure signature construction (spec 03 §3).

Builds, per test item, the single embeddable/searchable **signature document**
plus its two deterministic identity hashes:

* ``exception_fp`` — XXH3-64 of the normalized exception chain (root-cause first)
  and the top in-app stack frames (§3.2).
* ``error_hash`` — XXH3-64 of ``exception_fp`` and the ordered template hashes
  (§3.3). Same failure (same exception chain, frames, template sequence) →
  identical ``error_hash`` across runs and processes.

Inputs are :class:`ProcessedLog` records — the per-log output of the
preprocessing pipeline (``analyzer_ng.preprocessing.pipeline``) enriched with the
Drain template hashes (``analyzer_ng.ml.drain``). This module is otherwise pure
and deterministic, which is what makes it golden-testable (§11).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from analyzer_ng.ml.drain import mask_text
from analyzer_ng.ml.hashing import xxh3_64_signed
from analyzer_ng.preprocessing.text_processing import (
    first_lines,
    get_potential_status_codes,
    is_line_from_stacktrace,
    preprocess_test_item_name,
)

# §3.1 field markers, in order.
MSG_FALLBACK = "<no message>"
SIGNATURE_MAX_CHARS = 2000
MSG_MAX_LINES = 8
MAX_TEMPLATES = 30
MAX_EXCEPTION_CLASSES = 4
TOP_FRAMES = 3

# §3.2 step 4: framework prefixes that are never "in-app".
FRAMEWORK_DENYLIST: tuple[str, ...] = (
    "java.",
    "javax.",
    "jdk.",
    "org.springframework.",
    "org.apache.",
    "com.google.",
    "io.netty.",
    "okhttp3.",
    "requests.",
    "urllib3.",
    "selenium.",
    "org.openqa.",
    "node_modules",
)
# §3.2 step 3: additional generated/framework frame prefixes to drop outright.
FRAME_DROP_PREFIXES: tuple[str, ...] = (
    "jdk.internal.",
    "sun.reflect.",
    "java.lang.reflect.",
    "org.junit.",
    "org.testng.",
    "pytest",
    "unittest",
    "reflect.",
)

# §3.2 step 2: package-private / synthetic suffixes stripped from class & frame names.
_CLASS_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\$Lambda.*"),
    re.compile(r"\$\$EnhancerBy\w+.*"),
    re.compile(r"\$Proxy\d+"),
    re.compile(r"\.\.EnhancerBySpring.*"),
    re.compile(r"\$\d+"),
)

# Chain markers that mean the *last* exception in the text is the root cause.
_CHAIN_MARKER = re.compile(
    r"caused by:|during handling of the above exception|"
    r"the above exception was the direct cause",
    re.IGNORECASE,
)

# §3.4 assertion detection + value masking.
_ASSERTION_RE = re.compile(r"(?i)expected.*(but was|received|actual)")
_ASSERTION_VALUE_RE = re.compile(r"(?i)\b(expected|but was|actual|received)\b\s*[:=]?\s*[^,\n]+")

# Frame parsing.
_JAVA_AT_FRAME = re.compile(r"\bat\s+([\w$.]+)\s*\(")
_JAVA_BARE_FRAME = re.compile(r"^\s*([\w$.]+)\([^)]*\)\s*$")
_PY_FRAME = re.compile(r'File\s+"([^"]+)",\s*line\s+\d+,\s*in\s+(\S+)')


@dataclass(frozen=True, slots=True)
class ProcessedLog:
    """Per-log data the signature builder consumes (already preprocessed + mined)."""

    msg: str  # cleaned description half (detect_log_description_and_stacktrace)
    stack_raw: str  # stacktrace half with parens/line-numbers intact (for frame parsing)
    exceptions: Sequence[str] = ()  # get_found_exceptions over the unified text
    status_codes: Sequence[str] = ()  # get_unique_potential_status_codes over msg
    template_hashes: Sequence[str] = ()  # ordered per-line Drain template hex hashes
    has_stacktrace: bool = False


@dataclass(frozen=True, slots=True)
class SignatureResult:
    """Everything the retrieval layer needs about one item's failure signature."""

    signature_text: str
    exception_fp: int
    error_hash: int
    exc_classes: list[str] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)
    template_hashes: list[str] = field(default_factory=list)
    status_codes: list[str] = field(default_factory=list)
    msg_text: str = ""
    is_assertion: bool = False
    has_stacktrace: bool = False
    is_merged_small_logs: bool = False


def normalize_class(name: str) -> str:
    """Strip synthetic/proxy suffixes from a class or frame name (§3.2 step 2)."""
    result = name
    for pattern in _CLASS_SUFFIX_PATTERNS:
        result = pattern.sub("", result)
    return result


def _dedupe(seq: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_exception_chain(exceptions: Sequence[str], chain_text: str) -> list[str]:
    """Root-cause-first, normalized, de-duplicated exception classes (≤4) (§3.2 step 1-2)."""
    classes = list(exceptions)
    if classes and _CHAIN_MARKER.search(chain_text):
        # Chain markers present: the last exception is the root cause (§3.2 step 1).
        classes = [classes[-1], *classes[:-1]]
    normalized = [normalize_class(c) for c in classes]
    return _dedupe(normalized)[:MAX_EXCEPTION_CLASSES]


def _parse_frame(line: str) -> str | None:
    m = _JAVA_AT_FRAME.search(line)
    if m:
        return m.group(1)
    m = _JAVA_BARE_FRAME.match(line)
    if m:
        return m.group(1)
    m = _PY_FRAME.search(line)
    if m:
        path, method = m.group(1), m.group(2)
        module = path.replace("\\", "/").rsplit("/", 1)[-1]
        module = re.sub(r"\.\w+$", "", module)  # drop extension
        return f"{module}.{method}"
    return None


def _is_dropped(frame: str) -> bool:
    return any(frame.startswith(p) for p in FRAME_DROP_PREFIXES)


def _is_in_app(frame: str, in_app_prefixes: set[str] | None) -> bool:
    if any(frame.startswith(p) for p in FRAMEWORK_DENYLIST):
        return False
    if in_app_prefixes is None:  # cold start: all non-denylisted frames are in-app
        return True
    prefix2 = ".".join(frame.split(".")[:2])
    prefix3 = ".".join(frame.split(".")[:3])
    return prefix2 in in_app_prefixes or prefix3 in in_app_prefixes


def select_frames(stack_raw: str, in_app_prefixes: set[str] | None = None) -> list[str]:
    """Top-N normalized in-app frames from a stacktrace (§3.2 steps 3-5)."""
    parsed: list[str] = []
    for line in stack_raw.split("\n"):
        if not is_line_from_stacktrace(line):
            continue
        frame = _parse_frame(line)
        if frame is None:
            continue
        frame = normalize_class(frame)
        if _is_dropped(frame):
            continue
        parsed.append(frame)
    parsed = _dedupe(parsed)
    in_app = [f for f in parsed if _is_in_app(f, in_app_prefixes)]
    if len(in_app) >= TOP_FRAMES:
        return in_app[:TOP_FRAMES]
    # Pad with the first non-denylisted frames (§3.2 step 5).
    padding = [
        f
        for f in parsed
        if f not in in_app and not any(f.startswith(p) for p in FRAMEWORK_DENYLIST)
    ]
    return (in_app + padding)[:TOP_FRAMES]


def exception_fingerprint(exc_classes: Sequence[str], frames: Sequence[str]) -> int:
    """``exception_fp`` = signed XXH3-64 of the chain + frames (§3.2 step 6).

    Returns ``0`` when there is neither an exception class nor a frame — the
    hash would be too weak, so Stage-A/exact matching is disabled for the item
    (§3.4).
    """
    if not exc_classes and not frames:
        return 0
    payload = "|".join(exc_classes) + "#" + "|".join(frames)
    return xxh3_64_signed(payload)


def error_hash(exception_fp: int, template_hashes: Sequence[str]) -> int:
    """``error_hash`` = signed XXH3-64 of ``exception_fp`` + ordered template hashes (§3.3)."""
    payload = str(exception_fp) + "#" + "|".join(template_hashes)
    return xxh3_64_signed(payload)


def _mask_msg(msg: str, is_assertion: bool) -> str:
    text = first_lines(msg, MSG_MAX_LINES)
    if is_assertion:
        # Strip the expected/actual literals so diffs of different values still match.
        text = _ASSERTION_VALUE_RE.sub(r"\1 <VAL>", text)
    text = mask_text(text)
    text = " ".join(text.split("\n")).strip()
    return text


def _truncate_signature(sections: dict[str, str]) -> str:
    """Assemble the document, truncating MSG→TEMPLATES→FRAMES to fit the char cap."""
    order = ["TEST", "EXC", "MSG", "FRAMES", "TEMPLATES", "CODES"]

    def render(secs: dict[str, str]) -> str:
        return "\n".join(f"{k}: {secs[k]}" for k in order if k in secs)

    doc = render(sections)
    if len(doc) <= SIGNATURE_MAX_CHARS:
        return doc
    # Truncate low-priority fields from their tail until it fits (never TEST/EXC/CODES).
    for key in ("MSG", "TEMPLATES", "FRAMES"):
        while len(doc) > SIGNATURE_MAX_CHARS and sections.get(key):
            overflow = len(doc) - SIGNATURE_MAX_CHARS
            value = sections[key]
            trimmed = value[: max(0, len(value) - max(overflow, 1))].rstrip()
            if not trimmed:
                del sections[key]
            else:
                sections[key] = trimmed
            doc = render(sections)
    return doc[:SIGNATURE_MAX_CHARS]


def build_signature_document(
    *,
    test_item_name: str,
    exc_classes: Sequence[str],
    msg_text: str,
    frames: Sequence[str],
    template_hashes: Sequence[str],
    status_codes: Sequence[str],
) -> str:
    """Assemble the §3.1 signature document (field markers, truncation rules)."""
    sections: dict[str, str] = {}
    test = preprocess_test_item_name(test_item_name) if test_item_name else ""
    if test:
        sections["TEST"] = test
    if exc_classes:
        sections["EXC"] = " ".join(exc_classes)
    # MSG is always present (fallback when empty).
    sections["MSG"] = msg_text if msg_text else MSG_FALLBACK
    if frames:
        sections["FRAMES"] = " ".join(frames)
    if template_hashes:
        sections["TEMPLATES"] = " ".join(template_hashes[:MAX_TEMPLATES])
    if status_codes:
        sections["CODES"] = " ".join(f"HTTP_{c}" for c in status_codes)
    return _truncate_signature(sections)


def _select_primary(logs: Sequence[ProcessedLog]) -> ProcessedLog | None:
    """Primary log = last (by order) with a stacktrace, else the last log (§3.1)."""
    if not logs:
        return None
    for log in reversed(logs):
        if log.has_stacktrace:
            return log
    return logs[-1]


def build_item_signature(
    test_item_name: str,
    logs: Sequence[ProcessedLog],
    *,
    in_app_prefixes: set[str] | None = None,
) -> SignatureResult:
    """Build the full signature (doc + fingerprints) for one test item (§3.1-§3.4).

    ``logs`` must be in time order (oldest → newest). An item with no logs gets
    an empty signature and ``exception_fp = error_hash = 0`` (never analyzed,
    §3.4).
    """
    primary = _select_primary(logs)
    if primary is None:
        return SignatureResult(signature_text="", exception_fp=0, error_hash=0)

    # Exception chain over the whole item (occurrence order across logs).
    all_exceptions: list[str] = []
    for log in logs:
        all_exceptions.extend(log.exceptions)
    chain_text = "\n".join(f"{log.msg}\n{log.stack_raw}" for log in logs)
    exc_classes = extract_exception_chain(all_exceptions, chain_text)

    frames = select_frames(primary.stack_raw, in_app_prefixes) if primary.has_stacktrace else []

    # Ordered unique template hashes across all logs (occurrence order).
    ordered_hashes: list[str] = []
    for log in logs:
        ordered_hashes.extend(log.template_hashes)
    ordered_hashes = _dedupe(ordered_hashes)[:MAX_TEMPLATES]

    is_assertion = bool(_ASSERTION_RE.search(primary.msg))
    msg_text = _mask_msg(primary.msg, is_assertion)

    status_codes = get_potential_status_codes(primary.msg)

    exc_fp = exception_fingerprint(exc_classes, frames)
    err_hash = error_hash(exc_fp, ordered_hashes)

    signature_text = build_signature_document(
        test_item_name=test_item_name,
        exc_classes=exc_classes,
        msg_text=msg_text,
        frames=frames,
        template_hashes=ordered_hashes,
        status_codes=status_codes,
    )

    return SignatureResult(
        signature_text=signature_text,
        exception_fp=exc_fp,
        error_hash=err_hash,
        exc_classes=exc_classes,
        frames=frames,
        template_hashes=ordered_hashes,
        status_codes=status_codes,
        msg_text=msg_text,
        is_assertion=is_assertion,
        has_stacktrace=primary.has_stacktrace,
    )
