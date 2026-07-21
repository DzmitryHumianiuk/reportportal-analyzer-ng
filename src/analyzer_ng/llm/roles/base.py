"""Shared role mechanics: fact-block rendering, prompt scaffolding, hashing.

Each role (explainer/extractor/judge/coldstart) subclasses :class:`Role` and
supplies its exact prompts (spec 04 §4), draft-07 schema, cache content-key inputs
and post-validation rule. The generic request→validate→cache→store flow lives in
:mod:`analyzer_ng.llm.engine`; roles are pure prompt/validation logic and hold no
I/O, so they unit-test against plain input dicts.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any

from analyzer_ng.llm import excerpt as excerpt_mod
from analyzer_ng.llm.sanitizer import sanitize, wrap_untrusted

# §2.4 fact-block array caps (over-long fact blocks are truncated).
_TOP_FRAMES_CAP = 8
_TEMPLATE_IDS_CAP = 12
_STATUS_CODES_CAP = 8
_ARRAY_CAPS = {
    "top_frames": _TOP_FRAMES_CAP,
    "template_ids": _TEMPLATE_IDS_CAP,
    "error_templates": _TEMPLATE_IDS_CAP,
    "status_codes": _STATUS_CODES_CAP,
    "exception_chain": _TOP_FRAMES_CAP,
}

# §2.4 excerpt token slices per role.
EXCERPT_SLICE_TOKENS = 2200
JUDGE_EXCERPT_SLICE_TOKENS = 800


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize_leaf(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, list):
        return [_sanitize_leaf(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_leaf(v) for k, v in value.items()}
    return value


def _cap_arrays(fact_block: dict[str, Any]) -> dict[str, Any]:
    capped: dict[str, Any] = {}
    for key, value in fact_block.items():
        if isinstance(value, list) and key in _ARRAY_CAPS:
            capped[key] = value[: _ARRAY_CAPS[key]]
        else:
            capped[key] = value
    return capped


def prepare_fact_block(fact_block: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (rendered JSON, sanitized string leaves) for a fact block (§3.1).

    Every string value is sanitized (§5.2 applies to fact strings, not just the
    excerpt); arrays are capped (§2.4). The returned leaf list is the allow-list
    the explainer/extractor substring post-validation checks quotes against.
    """
    capped = _cap_arrays(fact_block)
    sanitized = {k: _sanitize_leaf(v) for k, v in capped.items()}
    rendered = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, indent=2)
    leaves: list[str] = []
    _collect_strings(sanitized, leaves)
    return rendered, leaves


def _collect_strings(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, list):
        for v in value:
            _collect_strings(v, out)
    elif isinstance(value, dict):
        for v in value.values():
            _collect_strings(v, out)


def build_untrusted_excerpt(
    raw_excerpt: str, nonce: str, slice_tokens: int = EXCERPT_SLICE_TOKENS
) -> tuple[str, str]:
    """Sanitize + middle-out truncate + envelope-wrap a raw log excerpt.

    Returns (wrapped_block, sanitized_truncated_text). The second value is the
    post-validation corpus (what the model actually saw as log data).
    """
    sanitized = sanitize(raw_excerpt)
    truncated = excerpt_mod.middle_out(sanitized, slice_tokens)
    return wrap_untrusted(truncated, nonce), truncated


class Role(ABC):
    """Prompt + schema + validation for one LLM role."""

    name: str
    ttl_days: int
    num_predict: int
    schema: dict[str, Any]
    # Free-text (narrative) output fields whose mid-sentence truncation is *masked*
    # by constrained decoding — schema validation passes because the grammar closes
    # the open string and emits the remaining fields. The engine screens these for
    # a length-stopped cut and retries/marks them (§3.0 step 6b). Structured fields
    # (enums, copied identifiers) are excluded: a cut there breaks JSON → schema_fail,
    # which the ordinary retry/drop path already catches, never silently persisted.
    free_text_fields: tuple[str, ...] = ()

    @abstractmethod
    def content_key(self, inp: dict[str, Any]) -> str:
        """Canonical, nonce-free string hashed into ``prompt_hash`` / cache key.

        Must be deterministic and identical across calls for the same semantic
        input, so caching works despite the per-request envelope nonce (§3.0)."""

    @abstractmethod
    def build_prompt(self, inp: dict[str, Any], nonce: str) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for a live call."""

    def post_validate(self, output: dict[str, Any], inp: dict[str, Any]) -> bool:
        """Role-specific checks beyond schema (§3.0 step 5). Default: accept."""
        return True

    def template_hash(self, inp: dict[str, Any]) -> int | None:
        """Extractor-only: the template-set hash the output is cached against."""
        return None
