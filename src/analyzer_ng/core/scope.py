"""analyzerMode → retrieval scope filters & boosts (spec 03 §6.0).

Replicates the legacy ``analyzer_service.py`` scope semantics: ``analyzerMode``
selects which slice of a project's labeled history is eligible evidence and how
same-name / same-launch candidates are re-weighted.

The verbatim hybrid-retrieval SQL (spec 02 §5.1) is never re-written (global rule
1); the project-scoped, labeled candidate set it returns is narrowed (hard
filters, ``analyze`` route) or re-weighted (soft boosts, ``suggest`` route) here
in deterministic Python. Both surfaces are pure functions so the §6.0 table is
unit-testable directly (acceptance: "analyzerMode fixtures … SQL/filters").
"""

from __future__ import annotations

from dataclasses import dataclass

# Legacy BoostLaunch (spec 03 §6.0). Config-overridable at the call site.
LAUNCH_BOOST = 1.1

# Canonical analyzerMode strings (legacy vocabulary).
LAUNCH_NAME = "LAUNCH_NAME"
CURRENT_AND_THE_SAME_NAME = "CURRENT_AND_THE_SAME_NAME"
CURRENT_LAUNCH = "CURRENT_LAUNCH"
PREVIOUS_LAUNCH = "PREVIOUS_LAUNCH"
ALL = "ALL"

_KNOWN_MODES = frozenset(
    {LAUNCH_NAME, CURRENT_AND_THE_SAME_NAME, CURRENT_LAUNCH, PREVIOUS_LAUNCH, ALL}
)


@dataclass(frozen=True)
class ScopeQuery:
    """The item-under-analysis context the scope table keys off."""

    project_id: int
    launch_id: int
    launch_name: str = ""
    previous_launch_id: int = 0


@dataclass(frozen=True)
class ScopeCandidate:
    """The minimal candidate view the scope table inspects."""

    launch_id: int
    launch_name: str = ""
    issue_type_group: str | None = None  # 'pb'|'ab'|'si'|'nd'|'ti'
    is_labeled: bool = True


def _mode(analyzer_mode: str | None) -> str | None:
    """Normalize an analyzerMode to a known constant, or None for default/unset."""
    if analyzer_mode is None:
        return None
    token = analyzer_mode.strip().upper()
    return token if token in _KNOWN_MODES else None


def passes_base(cand: ScopeCandidate) -> bool:
    """Every scope requires a labeled, non-``ti`` candidate (spec §6.0 footer)."""
    return cand.is_labeled and cand.issue_type_group not in (None, "ti")


def in_analyze_scope(analyzer_mode: str | None, q: ScopeQuery, cand: ScopeCandidate) -> bool:
    """Hard-filter predicate for the ``analyze`` route (spec §6.0, analyze column)."""
    if not passes_base(cand):
        return False
    mode = _mode(analyzer_mode)
    if mode == LAUNCH_NAME:
        return cand.launch_name == q.launch_name and cand.launch_id != q.launch_id
    if mode == CURRENT_AND_THE_SAME_NAME:
        return cand.launch_name == q.launch_name
    if mode == CURRENT_LAUNCH:
        return cand.launch_id == q.launch_id
    if mode == PREVIOUS_LAUNCH:
        return cand.launch_id == q.previous_launch_id
    if mode == ALL:
        return cand.launch_id != q.launch_id
    return True  # default/unset: no hard filter


def analyze_boost(
    analyzer_mode: str | None,
    q: ScopeQuery,
    cand: ScopeCandidate,
    *,
    launch_boost: float = LAUNCH_BOOST,
) -> float:
    """Soft multiplier applied on the ``analyze`` route (spec §6.0, analyze column).

    ``CURRENT_AND_THE_SAME_NAME`` lifts the current launch; **default/unset** (no
    hard filter) additionally ×boosts same name and same launch_id. The other
    modes express their preference purely through the hard filter (boost 1.0).
    """
    mode = _mode(analyzer_mode)
    same_name = bool(q.launch_name) and cand.launch_name == q.launch_name
    same_launch = cand.launch_id == q.launch_id
    if mode == CURRENT_AND_THE_SAME_NAME:
        return launch_boost if same_launch else 1.0
    if mode is None:  # default/unset
        boost = 1.0
        if same_name:
            boost *= launch_boost
        if same_launch:
            boost *= launch_boost
        return boost
    return 1.0


def suggest_boost(
    analyzer_mode: str | None,
    q: ScopeQuery,
    cand: ScopeCandidate,
    *,
    launch_boost: float = LAUNCH_BOOST,
) -> float:
    """Soft multiplier applied on the ``suggest`` route (spec §6.0, suggest column).

    ``suggest`` never hard-filters — every mode expresses its preference as a
    multiplier on the fused ``rrf`` score.
    """
    mode = _mode(analyzer_mode)
    same_name = bool(q.launch_name) and cand.launch_name == q.launch_name
    same_launch = cand.launch_id == q.launch_id
    boost = 1.0
    if mode in (LAUNCH_NAME, ALL):
        if same_name:
            boost *= launch_boost
        if same_launch:
            boost /= launch_boost
        return boost
    if mode == PREVIOUS_LAUNCH:
        if q.previous_launch_id and cand.launch_id == q.previous_launch_id:
            boost *= launch_boost
        return boost
    # CURRENT_AND_THE_SAME_NAME, CURRENT_LAUNCH, default/unset:
    # ×boost same name and ×boost same launch_id.
    if same_name:
        boost *= launch_boost
    if same_launch:
        boost *= launch_boost
    return boost
