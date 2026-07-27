"""Nightly LLM evaluation & per-project kill-switch (spec 04 §6.2).

Once a day the maintenance scheduler compares each project/role's LLM output
against the ``label_event`` ground-truth stream over a trailing 30-day window and,
when a role is *demonstrably* worse than the classical path, flips its
``llm_role_state`` row off for **that project only**. The role stays off until an
admin re-enables it (row delete/update); this job re-evaluates but **never**
auto-re-enables (it only ever writes ``enabled=false``).

Auto-disable fires iff all of these hold (§6.2, amended):

* the comparison set has ``N ≥ 50`` gated cases,
* the classical arm has ``n ≥ 50`` resolved cases (a tiny arm cannot bind),
* ``precision_llm < 0.95`` (a role above the absolute floor is never killed
  by a relative comparison — the comparison is precision-only and ignores
  that the LLM arm usually covers far more cases than the classical one),
* ``precision_llm < precision_classical − 0.02``,
* the 95 % Wilson lower bound of ``(precision_llm − precision_classical)`` is ``< 0``.

The Wilson math lives here as pure functions (unit-tested at the boundaries). The
difference bound uses the MOVER (Method of Variance Estimates Recovery) combination
of the two arms' Wilson intervals — treating the arms as independent, which is
conservative (wider) versus a paired estimate, so the kill-switch never fires on a
narrower-than-real interval. The judge comparison is paired (same items, equal arm
sizes); cold-start compares two independent groups; both feed the same math.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# §6.2 auto-disable thresholds.
AUTO_DISABLE_MIN_N = 50
AUTO_DISABLE_GAP = 0.02
# The classical arm must be at least this large before the relative comparison
# binds. A tiny perfect arm is degenerate: Wilson at p̂ = 1 has upper bound
# exactly 1, so it contributes zero width to the MOVER bound and reads as
# certainty (observed live: rule_cold 12/12 disabling a 96 %-precision rubric).
AUTO_DISABLE_MIN_CLASSICAL_N = 50
# Never auto-disable a role whose own precision clears this absolute floor,
# regardless of the classical arm. The comparison is precision-only and
# coverage-blind (the rubric arm typically answers far more cases than
# rule_cold); a role right ≥ 95 % of the time under human review is doing its
# job even against a locally perfect baseline.
AUTO_DISABLE_PRECISION_FLOOR = 0.95
WILSON_Z = 1.96  # 95 % two-sided

EVAL_WINDOW_DAYS = 30
AUTO_DISABLE_REASON = "auto_disabled_precision"

# The four adopted roles the eval reports the disabled-project gauge for (§6.3).
ALL_ROLES = ("explainer", "extractor", "judge", "coldstart")


def wilson_interval(successes: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score interval ``(lower, upper)`` for a binomial proportion.

    Degenerate (``n == 0``) returns ``(0.0, 0.0)``. The interval is always inside
    ``[0, 1]`` and never inverted; at ``p̂ = 0`` the lower bound is 0 and at
    ``p̂ = 1`` the upper bound is 1 (the score interval's boundary behaviour).
    """
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)) / denom
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return (lo, hi)


def mover_difference_lower(
    s_llm: int, n_llm: int, s_classical: int, n_classical: int, z: float = WILSON_Z
) -> float:
    """95 % (by default) lower bound of ``p_llm − p_classical`` (MOVER-Wilson).

    Combines each arm's Wilson interval into a lower confidence bound for the
    difference (Newcombe/Zou MOVER). With empty arms the difference is 0 and the
    bound reflects only the non-empty arm's spread.
    """
    p1 = (s_llm / n_llm) if n_llm > 0 else 0.0
    p2 = (s_classical / n_classical) if n_classical > 0 else 0.0
    l1, _u1 = wilson_interval(s_llm, n_llm, z)
    _l2, u2 = wilson_interval(s_classical, n_classical, z)
    diff = p1 - p2
    return diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)


@dataclass(frozen=True)
class RoleComparison:
    """One project/role comparison set over the eval window (§6.2).

    ``n`` is the gate count (paired items for the judge; the gated LLM-arm case
    count for cold-start). ``s_*``/``n_*`` are each arm's successes/total.
    """

    n: int
    s_llm: int
    n_llm: int
    s_classical: int
    n_classical: int


@dataclass(frozen=True)
class RoleEval:
    """The outcome of evaluating one project/role."""

    project_id: int
    role: str
    n: int
    precision_llm: float
    precision_classical: float
    diff_lower: float
    disabled: bool
    reason: str

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "precision_llm": self.precision_llm,
            "precision_classical": self.precision_classical,
            "diff_lower": self.diff_lower,
            "gap": self.precision_llm - self.precision_classical,
        }


def evaluate_role(project_id: int, role: str, cmp: RoleComparison) -> RoleEval:
    """Apply the §6.2 auto-disable criteria to one comparison set (pure)."""
    p_llm = (cmp.s_llm / cmp.n_llm) if cmp.n_llm > 0 else 0.0
    p_classical = (cmp.s_classical / cmp.n_classical) if cmp.n_classical > 0 else 0.0
    diff_lower = mover_difference_lower(cmp.s_llm, cmp.n_llm, cmp.s_classical, cmp.n_classical)

    disabled = False
    if cmp.n < AUTO_DISABLE_MIN_N:
        reason = "n_below_min"
    elif cmp.n_classical < AUTO_DISABLE_MIN_CLASSICAL_N:
        reason = "classical_arm_below_min"
    elif p_llm >= AUTO_DISABLE_PRECISION_FLOOR:
        reason = "llm_above_precision_floor"
    elif not (p_llm < p_classical - AUTO_DISABLE_GAP):
        reason = "gap_within_tolerance"
    elif not (diff_lower < 0.0):
        reason = "wilson_bound_nonnegative"
    else:
        disabled = True
        reason = AUTO_DISABLE_REASON
    return RoleEval(
        project_id=project_id,
        role=role,
        n=cmp.n,
        precision_llm=p_llm,
        precision_classical=p_classical,
        diff_lower=diff_lower,
        disabled=disabled,
        reason=reason,
    )


class _ComparisonSource(Protocol):
    def role_comparisons(self, since: datetime) -> dict[tuple[int, str], RoleComparison]: ...


class _RoleStateSink(Protocol):
    def is_enabled(self, project_id: int, role: str) -> bool: ...
    def set_state(
        self,
        project_id: int,
        role: str,
        *,
        enabled: bool,
        reason: str | None = None,
        stats: dict | None = None,
    ) -> None: ...


class _Metrics(Protocol):
    def observe_llm_role_disabled(self, counts: dict[str, int]) -> None: ...


@dataclass
class LlmEvalJob:
    """Nightly paired-comparison job + per-project kill-switch writer (§6.2/§6.3)."""

    source: _ComparisonSource
    role_state: _RoleStateSink
    metrics: _Metrics | None = None
    window_days: int = EVAL_WINDOW_DAYS
    clock: Any = field(default=lambda: datetime.now(UTC))

    def run(self) -> list[RoleEval]:
        since = self.clock() - timedelta(days=self.window_days)
        comparisons = self.source.role_comparisons(since)
        results: list[RoleEval] = []
        disabled_counts: dict[str, int] = defaultdict(int)
        for (project_id, role), cmp in sorted(comparisons.items()):
            ev = evaluate_role(project_id, role, cmp)
            results.append(ev)
            if not ev.disabled:
                continue
            disabled_counts[role] += 1
            # Only ever write enabled=false, and only when currently on — an admin
            # re-enable is respected until the next underperforming window, and we
            # never auto-re-enable (§6.2).
            if self.role_state.is_enabled(project_id, role):
                self.role_state.set_state(
                    project_id,
                    role,
                    enabled=False,
                    reason=AUTO_DISABLE_REASON,
                    stats=ev.stats,
                )
                logger.warning(
                    "LLM role '%s' auto-disabled for project %s: precision_llm=%.3f "
                    "< precision_classical=%.3f (N=%d, diff_lower=%.3f)",
                    role,
                    project_id,
                    ev.precision_llm,
                    ev.precision_classical,
                    ev.n,
                    ev.diff_lower,
                )
        if self.metrics is not None:
            # Reset every role's gauge each run so a re-enable clears it (§6.3).
            self.metrics.observe_llm_role_disabled(
                {role: disabled_counts.get(role, 0) for role in ALL_ROLES}
            )
        return results
