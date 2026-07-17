"""Ingest → preprocess → template → signature orchestration (spec 03 §1, §3.4).

This is the seam the AMQP routes (T2.2/T2.3) call into. It is intentionally
side-effect-free except for feeding the per-project :class:`DrainManager`:

* :func:`filter_item_logs` — §1.1 level filter, near-duplicate drop, tail cap.
* :func:`clean_log` — §1.3 ordered cleaning stages + extracted features (pure).
* :func:`process_log` — :func:`clean_log` plus Drain template mining for one log.
* :func:`build_item_signature` — the full item-level flow producing a
  :class:`~analyzer_ng.ml.signature.SignatureResult`.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from analyzer_ng.ml import signature as sig
from analyzer_ng.ml.drain import DrainManager
from analyzer_ng.ml.signature import ProcessedLog, SignatureResult
from analyzer_ng.preprocessing import log_preparation, text_processing

logger = logging.getLogger(__name__)

# spec 03 §1.1 constants.
ERROR_LOGGING_LEVEL = 40000
SIMILARITY_THRESHOLD_TO_DROP = 0.95
NUMBER_OF_LOGS_TO_INDEX = 20
# §3.4 "many tiny logs" merge threshold.
SMALL_LOG_MAX_CHARS = 100


@dataclass(frozen=True, slots=True)
class LogInput:
    """A raw log as delivered over AMQP (message + numeric level)."""

    message: str
    log_level: int = ERROR_LOGGING_LEVEL


@dataclass(frozen=True, slots=True)
class CleanedLog:
    """§1.3 cleaned views + extracted features for one log (pure, Drain-free)."""

    unified: str  # `u` — input to Drain masking
    msg: str  # description half
    stack_raw: str  # stacktrace half, parens/line-numbers intact (frame parsing)
    stack: str  # bracket-/number-cleaned stacktrace (stored feature)
    exceptions: list[str] = field(default_factory=list)
    status_codes: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    has_stacktrace: bool = False


def filter_item_logs(
    logs: Sequence[LogInput], *, max_logs: int = NUMBER_OF_LOGS_TO_INDEX
) -> list[str]:
    """§1.1 filtering: ERROR+ only, drop near-duplicates, keep the last ``max_logs``."""
    # 1. Level filter + drop empty messages.
    messages = [
        log.message for log in logs if log.log_level >= ERROR_LOGGING_LEVEL and log.message.strip()
    ]
    if not messages:
        return []
    # 2. Near-duplicate drop (keeps the last occurrence of each cluster).
    if len(messages) > 1:
        try:
            keep_indices = text_processing.find_last_unique_texts(
                SIMILARITY_THRESHOLD_TO_DROP, messages
            )
        except ValueError:
            # Degenerate TF-IDF vocabulary (e.g. all-punctuation messages) — keep all.
            keep_indices = list(range(len(messages)))
        messages = [messages[i] for i in keep_indices]
    # 3. Cap: keep the last N surviving logs.
    return messages[-max_logs:]


def clean_log(raw_message: str) -> CleanedLog:
    """§1.3 ordered cleaning stages + extracted per-log features (pure)."""
    basic = log_preparation.basic_prepare(raw_message)
    unified = log_preparation.unify_message(basic)  # `u`

    msg, stack_raw = text_processing.detect_log_description_and_stacktrace(unified)
    stack = text_processing.remove_numbers(text_processing.clean_from_brackets(stack_raw))

    exceptions = text_processing.get_found_exceptions(unified)
    status_codes = text_processing.get_unique_potential_status_codes(msg)
    urls = text_processing.extract_urls(msg)
    paths = text_processing.extract_paths(text_processing.remove_urls(msg, urls))

    return CleanedLog(
        unified=unified,
        msg=msg,
        stack_raw=stack_raw,
        stack=stack,
        exceptions=exceptions,
        status_codes=status_codes,
        urls=urls,
        paths=paths,
        has_stacktrace=bool(stack_raw.strip()),
    )


def process_log(raw_message: str, drain: DrainManager | None = None) -> ProcessedLog:
    """§1.3 cleaning + Drain template mining for a single log."""
    cleaned = clean_log(raw_message)
    template_hashes = (
        [hit.hash_hex for hit in drain.add_message(cleaned.unified)] if drain is not None else []
    )
    return ProcessedLog(
        msg=cleaned.msg,
        stack_raw=cleaned.stack_raw,
        exceptions=cleaned.exceptions,
        status_codes=cleaned.status_codes,
        template_hashes=template_hashes,
        has_stacktrace=cleaned.has_stacktrace,
    )


def maybe_merge_small_logs(messages: Sequence[str]) -> tuple[list[str], bool]:
    """§3.4: merge many tiny stacktrace-free logs into one pseudo-log.

    Returns ``(messages, is_merged)``. Merging only happens when there are ≥2
    messages, all shorter than :data:`SMALL_LOG_MAX_CHARS`, and none looks like a
    stacktrace — mirroring legacy ``merged_small_logs`` behaviour.
    """
    if len(messages) < 2:
        return list(messages), False
    if all(
        len(m) < SMALL_LOG_MAX_CHARS
        and not any(text_processing.is_line_from_stacktrace(ln) for ln in m.split("\n"))
        for m in messages
    ):
        return ["\n".join(messages)], True
    return list(messages), False


def build_item_signature(
    test_item_name: str,
    logs: Sequence[LogInput],
    drain: DrainManager,
    *,
    in_app_prefixes: set[str] | None = None,
    merge_small_logs: bool = True,
) -> SignatureResult:
    """Full §1-§3 flow for one test item: filter → clean → mine → signature.

    An item whose logs are all filtered out yields an empty signature with
    ``exception_fp = error_hash = 0`` (never analyzed, §3.4).
    """
    kept = filter_item_logs(logs)
    if not kept:
        return SignatureResult(signature_text="", exception_fp=0, error_hash=0)

    is_merged = False
    if merge_small_logs:
        kept, is_merged = maybe_merge_small_logs(kept)

    processed = [process_log(message, drain) for message in kept]
    result = sig.build_item_signature(test_item_name, processed, in_app_prefixes=in_app_prefixes)
    if is_merged:
        # Reflect the §3.4 merge in the result (feature flag for the decision layer).
        result = dataclasses.replace(result, is_merged_small_logs=True)
    return result
