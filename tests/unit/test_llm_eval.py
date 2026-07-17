"""Nightly LLM eval + per-project kill-switch (spec 04 §6.2) — TDD from §7.

Covers the Wilson-bound math at boundaries, the auto-disable decision at the
N≥50 / gap>0.02 / Wilson-lower<0 thresholds, and the job's per-project isolation
(only the underperforming project's role is disabled) with /metrics visibility.
"""

from __future__ import annotations

import math

import pytest
from _llm_fakes import FakeRoleStateStore

from analyzer_ng.llm.eval import (
    AUTO_DISABLE_GAP,
    AUTO_DISABLE_MIN_N,
    LlmEvalJob,
    RoleComparison,
    evaluate_role,
    mover_difference_lower,
    wilson_interval,
)


# ---- Wilson interval: boundaries ---- #
def test_wilson_zero_n_is_degenerate() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_wilson_all_success_upper_is_one_lower_below_one() -> None:
    lo, hi = wilson_interval(20, 20)
    assert hi == pytest.approx(1.0, abs=1e-9)
    assert 0.0 < lo < 1.0


def test_wilson_all_failure_lower_is_zero() -> None:
    lo, hi = wilson_interval(0, 20)
    assert lo == pytest.approx(0.0, abs=1e-9)
    assert 0.0 < hi < 1.0


def test_wilson_known_reference_half() -> None:
    # p̂=0.5, n=100, z=1.96 → classic Wilson ≈ (0.4038, 0.5962).
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3)
    assert hi == pytest.approx(0.5962, abs=1e-3)
    assert (lo + hi) / 2 == pytest.approx(0.5, abs=1e-9)


def test_wilson_bounds_ordered_and_in_unit_interval() -> None:
    for s, n in [(1, 3), (7, 10), (99, 100), (3, 1000)]:
        lo, hi = wilson_interval(s, n)
        assert 0.0 <= lo <= hi <= 1.0


# ---- MOVER difference lower bound ---- #
def test_mover_difference_negative_gap_lower_below_zero() -> None:
    # p_llm=0.70, p_classical=0.80, n=60 each → point diff −0.10, lower bound < −0.10.
    lo = mover_difference_lower(42, 60, 48, 60)
    assert lo < 0.0
    assert lo < -0.10


def test_mover_difference_equal_arms_straddles_zero() -> None:
    lo = mover_difference_lower(30, 60, 30, 60)
    assert lo < 0.0  # symmetric interval around 0


# ---- Auto-disable decision (§6.2 exact criteria) ---- #
def _cmp(n: int, s_llm: int, n_llm: int, s_cls: int, n_cls: int) -> RoleComparison:
    return RoleComparison(n=n, s_llm=s_llm, n_llm=n_llm, s_classical=s_cls, n_classical=n_cls)


def test_role_disables_at_n60_gap_10() -> None:
    ev = evaluate_role(1, "coldstart", _cmp(60, 42, 60, 48, 60))
    assert ev.disabled is True
    assert ev.precision_llm == pytest.approx(0.70)
    assert ev.precision_classical == pytest.approx(0.80)


def test_role_not_disabled_below_min_n() -> None:
    ev = evaluate_role(1, "coldstart", _cmp(40, 28, 40, 32, 40))
    assert ev.disabled is False
    assert ev.reason == "n_below_min"


def test_role_not_disabled_when_gap_within_tolerance() -> None:
    # gap exactly 0.01 (< 0.02 tolerance) → not disabled even at N=200.
    ev = evaluate_role(1, "judge", _cmp(200, 158, 200, 160, 200))
    assert ev.disabled is False
    assert ev.reason == "gap_within_tolerance"


def test_role_not_disabled_when_llm_better() -> None:
    ev = evaluate_role(1, "judge", _cmp(100, 90, 100, 70, 100))
    assert ev.disabled is False


def test_constants_match_spec() -> None:
    assert AUTO_DISABLE_MIN_N == 50
    assert AUTO_DISABLE_GAP == 0.02


# ---- Job: per-project isolation + metrics ---- #
class _FakeSource:
    def __init__(self, groups: dict[tuple[int, str], RoleComparison]) -> None:
        self._groups = groups
        self.since_seen: object = None

    def role_comparisons(self, since: object) -> dict[tuple[int, str], RoleComparison]:
        self.since_seen = since
        return dict(self._groups)


class _FakeMetrics:
    def __init__(self) -> None:
        self.role_disabled: dict[str, int] = {}

    def observe_llm_role_disabled(self, counts: dict[str, int]) -> None:
        self.role_disabled = dict(counts)


def test_job_disables_only_underperforming_project() -> None:
    state = FakeRoleStateStore()
    metrics = _FakeMetrics()
    groups = {
        # project 1: underperforming coldstart (N=60, gap −0.10) → disable
        (1, "coldstart"): RoleComparison(n=60, s_llm=42, n_llm=60, s_classical=48, n_classical=60),
        # project 2: same role, healthy (llm better) → keep
        (2, "coldstart"): RoleComparison(n=60, s_llm=52, n_llm=60, s_classical=48, n_classical=60),
        # project 1: judge with too few cases (N=40) → keep
        (1, "judge"): RoleComparison(n=40, s_llm=28, n_llm=40, s_classical=32, n_classical=40),
    }
    job = LlmEvalJob(_FakeSource(groups), state, metrics=metrics)
    results = job.run()

    assert state.is_enabled(1, "coldstart") is False  # disabled
    assert state.is_enabled(2, "coldstart") is True  # isolated: untouched
    assert state.is_enabled(1, "judge") is True  # below N → untouched
    # role state carries the auto-disable reason + numbers.
    assert (1, "coldstart") in {(r.project_id, r.role) for r in results if r.disabled}
    # /metrics visibility: exactly one project disabled for coldstart, zero for judge.
    assert metrics.role_disabled.get("coldstart") == 1
    assert metrics.role_disabled.get("judge", 0) == 0


def test_job_sets_real_metrics_gauge_and_renders() -> None:
    from analyzer_ng.metrics import Metrics

    state = FakeRoleStateStore()
    metrics = Metrics()
    groups = {
        (1, "coldstart"): RoleComparison(n=60, s_llm=42, n_llm=60, s_classical=48, n_classical=60),
    }
    LlmEvalJob(_FakeSource(groups), state, metrics=metrics).run()
    body, _ct = metrics.render()
    text = body.decode()
    assert 'analyzer_llm_role_disabled{role="coldstart"} 1.0' in text
    assert 'analyzer_llm_role_disabled{role="judge"} 0.0' in text


def test_job_never_auto_re_enables() -> None:
    state = FakeRoleStateStore()
    # A healthy role that an admin (or default) has enabled must never be flipped on
    # by the job — the job only ever disables.
    groups = {(5, "explainer"): _cmp(100, 95, 100, 90, 100)}
    job = LlmEvalJob(_FakeSource(groups), state)
    job.run()
    assert state.is_enabled(5, "explainer") is True
    # And a role an admin manually disabled stays disabled (job writes only False).
    state.set_state(5, "explainer", enabled=False, reason="admin")
    job.run()
    assert state.is_enabled(5, "explainer") is False


def test_disable_writes_stats_and_reason() -> None:
    recorded: list[dict] = []

    class RecordingState(FakeRoleStateStore):
        def set_state(  # type: ignore[override]
            self, project_id, role, *, enabled, reason=None, stats=None
        ):
            recorded.append(
                {
                    "project_id": project_id,
                    "role": role,
                    "enabled": enabled,
                    "reason": reason,
                    "stats": stats,
                }
            )
            super().set_state(project_id, role, enabled=enabled, reason=reason, stats=stats)

    groups = {(9, "coldstart"): _cmp(80, 48, 80, 64, 80)}
    LlmEvalJob(_FakeSource(groups), RecordingState()).run()
    assert len(recorded) == 1
    rec = recorded[0]
    assert rec["enabled"] is False
    assert rec["reason"] == "auto_disabled_precision"
    assert rec["stats"]["n"] == 80
    assert rec["stats"]["precision_llm"] == pytest.approx(0.60)
    assert rec["stats"]["precision_classical"] == pytest.approx(0.80)
    assert math.isfinite(rec["stats"]["diff_lower"])
