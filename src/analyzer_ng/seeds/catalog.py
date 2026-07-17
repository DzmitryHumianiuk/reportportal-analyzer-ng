"""Seed failure-mode catalog + matching rules engine (spec 03 §9).

The catalog is 50 generic failure modes shipped as package data
(``seed_modes.yaml``). Each mode carries matching rules (exception-name regex,
message regex, keyword sets) and a prior (issue-type label + confidence).

This module is **pure logic** — no database. It loads and validates the YAML and
exposes :meth:`SeedCatalog.match`, the clean API T2.3 calls to classify a failure
signature to a seed mode. The lazy per-project persistence lives in ``loader.py``.

Rule semantics (spec 03 §9):

* ``exc_re``  — regex, **case-sensitive**, matched against the EXC classes.
* ``msg_re``  — regex, **case-insensitive**, matched against MSG + template texts.
* ``kw``      — lowercase substring, **ANY-of**, matched against MSG + templates
  + EXC classes.

A mode matches if **any** listed rule group matches; the first matching mode by
priority (catalog list order) wins for ``seed_mode_matched``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

import yaml

_RESOURCE = "seed_modes.yaml"
EXPECTED_MODE_COUNT = 50
VALID_LABELS = frozenset({"pb", "ab", "si", "nd"})

# Message regexes are line-oriented and case-insensitive; several catalog rules
# anchor with ``^`` (e.g. ``^assert\b``) expecting a per-line match. Exception
# regexes stay case-sensitive (class names are case-meaningful, spec §3.2).
_MSG_FLAGS = re.IGNORECASE | re.MULTILINE


class SeedCatalogError(ValueError):
    """The seed catalog failed to load or validate (bad regex, dup key, …)."""


@dataclass(frozen=True)
class SeedMode:
    """One immutable, compiled seed failure mode."""

    mode_key: str
    title: str
    prior_label: str
    prior_confidence: float
    rationale: str
    exc_re: tuple[re.Pattern[str], ...]
    msg_re: tuple[re.Pattern[str], ...]
    kw: tuple[str, ...]

    def matches(self, exc_classes: Sequence[str], msg_haystack: str, kw_haystack: str) -> bool:
        """True if any rule group fires. ``*_haystack`` are precomputed by match()."""
        for pat in self.exc_re:
            if any(pat.search(cls) for cls in exc_classes):
                return True
        for pat in self.msg_re:
            if pat.search(msg_haystack):
                return True
        return any(kw in kw_haystack for kw in self.kw)


class SeedCatalog:
    """Ordered set of :class:`SeedMode` with the first-match classifier."""

    def __init__(self, modes: Sequence[SeedMode]) -> None:
        self.modes: tuple[SeedMode, ...] = tuple(modes)
        self._by_key = {m.mode_key: m for m in self.modes}

    def __len__(self) -> int:
        return len(self.modes)

    def get(self, mode_key: str) -> SeedMode:
        return self._by_key[mode_key]

    def match(
        self,
        exc_classes: Sequence[str] = (),
        msg_text: str = "",
        template_texts: Sequence[str] = (),
    ) -> SeedMode | None:
        """Return the first (highest-priority) mode whose rules match, else None.

        ``exc_classes`` are normalized exception class names (from the signature
        ``EXC:`` field); ``msg_text`` is the cleaned primary message (``MSG:``);
        ``template_texts`` are the Drain template texts of the item's logs.
        """
        exc = [c for c in exc_classes if c]
        parts = [msg_text, *template_texts]
        msg_haystack = "\n".join(p for p in parts if p)
        kw_haystack = "\n".join(p for p in (*parts, *exc) if p).lower()
        for mode in self.modes:
            if mode.matches(exc, msg_haystack, kw_haystack):
                return mode
        return None


def _compile(mode_key: str, group: str, pattern: str, flags: int) -> re.Pattern[str]:
    try:
        return re.compile(pattern, flags)
    except re.error as exc:  # pragma: no cover - defensive, exercised via bad-catalog test
        raise SeedCatalogError(
            f"mode {mode_key!r} {group} pattern {pattern!r} does not compile: {exc}"
        ) from exc


def _build_mode(raw: dict) -> SeedMode:
    try:
        mode_key = raw["mode_key"]
        title = raw.get("title", "")
        prior = raw["prior"]
        rationale = raw.get("rationale", "")
    except (KeyError, TypeError) as exc:
        raise SeedCatalogError(f"malformed mode entry {raw!r}: {exc}") from exc

    label = prior.get("label")
    if label not in VALID_LABELS:
        raise SeedCatalogError(
            f"mode {mode_key!r} label {label!r} not in {sorted(VALID_LABELS)}"
        )
    confidence = float(prior.get("confidence", 0.0))
    if not 0.0 < confidence <= 1.0:
        raise SeedCatalogError(
            f"mode {mode_key!r} confidence {confidence} must satisfy 0 < c <= 1"
        )

    rules = raw.get("rules") or {}
    exc_re = tuple(_compile(mode_key, "exc_re", p, 0) for p in rules.get("exc_re", []))
    msg_re = tuple(_compile(mode_key, "msg_re", p, _MSG_FLAGS) for p in rules.get("msg_re", []))
    kw = tuple(str(k).lower() for k in rules.get("kw", []))
    if not (exc_re or msg_re or kw):
        raise SeedCatalogError(f"mode {mode_key!r} has no matching rules")

    return SeedMode(
        mode_key=mode_key,
        title=title,
        prior_label=label,
        prior_confidence=confidence,
        rationale=rationale,
        exc_re=exc_re,
        msg_re=msg_re,
        kw=kw,
    )


def load_catalog(text: str | None = None) -> SeedCatalog:
    """Parse and validate a catalog. ``text=None`` loads the packaged resource.

    Validates: YAML shape, every regex compiles, ``mode_key`` unique, every
    ``label ∈ {pb,ab,si,nd}``, ``0 < confidence <= 1``, and every mode carries at
    least one rule. Raises :class:`SeedCatalogError` on any violation.
    """
    if text is None:
        text = files(__package__).joinpath(_RESOURCE).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or "modes" not in data:
        raise SeedCatalogError("catalog must be a mapping with a 'modes' list")

    modes = [_build_mode(raw) for raw in data["modes"]]
    keys = [m.mode_key for m in modes]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise SeedCatalogError(f"duplicate mode_key(s): {duplicates}")
    return SeedCatalog(modes)


@lru_cache(maxsize=1)
def default_catalog() -> SeedCatalog:
    """The packaged 50-mode catalog (cached; loaded once per process)."""
    catalog = load_catalog()
    if len(catalog) != EXPECTED_MODE_COUNT:
        raise SeedCatalogError(
            f"expected {EXPECTED_MODE_COUNT} seed modes, found {len(catalog)}"
        )
    return catalog
