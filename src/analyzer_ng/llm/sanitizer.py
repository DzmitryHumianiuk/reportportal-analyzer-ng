"""Prompt-injection sanitizer and untrusted-data envelope (spec 04 §5).

Logs are untrusted input. Every log-derived string (excerpt or fact value) is run
through :func:`sanitize` before it enters a prompt, and every excerpt is wrapped
in a nonce-bearing envelope (:func:`wrap_untrusted`) whose closing marker an
attacker cannot forge (they cannot guess the per-request nonce, and forged marker
lines are stripped from the data itself, §5.1).

:func:`sanitize` is a pure function with golden-file tests (§5.2 / §7).
"""

from __future__ import annotations

import re
import secrets

# §5.2(1) chat-template control tokens: ``<|im_start|>``, ``<|im_end|>``,
# ``<|endoftext|>``, Granite ``<|start_of_role|>``/``<|end_of_role|>``, etc.
_CONTROL_TOKEN_RE = re.compile(r"<\|[a-zA-Z0-9_]{1,32}\|>")
# Literal Llama/Mistral instruction + system markers (not covered by the pipe form).
_LITERAL_MARKERS = ("[INST]", "[/INST]", "<<SYS>>", "<</SYS>>")
_STRIPPED = "⟨stripped⟩"  # ⟨stripped⟩

# §5.2(2) role-line prefixes at line start. ``[^\S\n]`` is "whitespace but not a
# newline" so a greedy match can never span lines (the spec writes ``\s*``; we
# pin it per-line to keep the rewrite line-local and deterministic).
_ROLE_LINE_RE = re.compile(
    r"(?im)^([^\S\n]*(?:system|assistant|user|tool|developer)[^\S\n]*):",
)
_ROLE_COLON = "∶"  # ∶  (U+2236 RATIO) — shape destroyed, content preserved

# §5.1 forged envelope markers embedded in the data itself.
_ENVELOPE_MARKER_RE = re.compile(r"(?im)^=+\s*(?:BEGIN|END)\s+UNTRUSTED.*$")

# §5.2(4) C0 controls to drop (everything 0x00–0x1F and 0x7F except \n and \t).
_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# §5.2(4) collapse runs of >3 consecutive newlines down to 3.
_BLANK_RUN_RE = re.compile(r"\n{4,}")


def sanitize(text: str) -> str:
    """Neutralize prompt-injection vectors in an untrusted log/fact string (§5.2).

    Order: (1) strip chat-template control tokens and literal instruction markers
    → ``⟨stripped⟩``; (1b) strip forged untrusted-envelope marker lines (§5.1);
    (2) defang role-line prefixes by swapping the colon for U+2236; (4) drop C0
    controls (keeping ``\\n``/``\\t``) and collapse long blank runs. Soft switches
    (``/think``, ``/no_think``) and JSON ``"format":`` fragments are left as data
    (§5.2(3)) — harmless once the structural vectors above are gone.
    """
    text = _CONTROL_TOKEN_RE.sub(_STRIPPED, text)
    for marker in _LITERAL_MARKERS:
        text = text.replace(marker, _STRIPPED)
    text = _ENVELOPE_MARKER_RE.sub(_STRIPPED, text)
    text = _ROLE_LINE_RE.sub(lambda m: m.group(1) + _ROLE_COLON, text)
    text = _C0_CONTROL_RE.sub("", text)
    text = _BLANK_RUN_RE.sub("\n\n\n", text)
    return text


def new_nonce() -> str:
    """A fresh 8-hex-char envelope nonce (§5.1), unguessable per request."""
    return secrets.token_hex(4)


def wrap_untrusted(sanitized_text: str, nonce: str) -> str:
    """Wrap already-sanitized log text in the nonce-bearing envelope (§5.1)."""
    return (
        f"===== BEGIN UNTRUSTED LOG DATA ({nonce}) =====\n"
        f"{sanitized_text}\n"
        f"===== END UNTRUSTED LOG DATA ({nonce}) ====="
    )
