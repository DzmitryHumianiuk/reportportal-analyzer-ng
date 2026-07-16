"""analyzerMode scope filters/boosts (spec 03 §6.0) — the 6-mode table."""

from __future__ import annotations

import pytest

from analyzer_ng.core import scope
from analyzer_ng.core.scope import ScopeCandidate, ScopeQuery

Q = ScopeQuery(project_id=1, launch_id=100, launch_name="Nightly", previous_launch_id=99)


def _c(launch_id: int, name: str = "Nightly", group: str = "pb", labeled: bool = True):
    return ScopeCandidate(
        launch_id=launch_id, launch_name=name, issue_type_group=group, is_labeled=labeled
    )


def test_base_requirement_excludes_unlabeled_and_ti():
    assert not scope.passes_base(_c(1, labeled=False))
    assert not scope.passes_base(_c(1, group="ti"))
    assert not scope.passes_base(_c(1, group=None))
    assert scope.passes_base(_c(1, group="ab"))


@pytest.mark.parametrize(
    "mode,cand,expected",
    [
        # LAUNCH_NAME: same name AND different launch.
        ("LAUNCH_NAME", _c(101, "Nightly"), True),
        ("LAUNCH_NAME", _c(100, "Nightly"), False),  # same launch excluded
        ("LAUNCH_NAME", _c(101, "Other"), False),  # different name excluded
        # CURRENT_AND_THE_SAME_NAME: same name (any launch).
        ("CURRENT_AND_THE_SAME_NAME", _c(100, "Nightly"), True),
        ("CURRENT_AND_THE_SAME_NAME", _c(101, "Nightly"), True),
        ("CURRENT_AND_THE_SAME_NAME", _c(101, "Other"), False),
        # CURRENT_LAUNCH: same launch only.
        ("CURRENT_LAUNCH", _c(100, "Nightly"), True),
        ("CURRENT_LAUNCH", _c(101, "Nightly"), False),
        # PREVIOUS_LAUNCH: previous launch id only.
        ("PREVIOUS_LAUNCH", _c(99, "Nightly"), True),
        ("PREVIOUS_LAUNCH", _c(100, "Nightly"), False),
        # ALL: any launch except the current one.
        ("ALL", _c(101, "Other"), True),
        ("ALL", _c(100, "Nightly"), False),
        # default/unset: no hard filter (still requires base).
        (None, _c(555, "Whatever"), True),
        ("BOGUS", _c(555, "Whatever"), True),
    ],
)
def test_analyze_hard_filters(mode, cand, expected):
    assert scope.in_analyze_scope(mode, Q, cand) is expected


def test_analyze_hard_filter_still_requires_base():
    assert scope.in_analyze_scope("ALL", Q, _c(101, group="ti")) is False


def test_analyze_boost_only_for_current_and_same_name():
    assert scope.analyze_boost("CURRENT_AND_THE_SAME_NAME", Q, _c(100)) == pytest.approx(1.1)
    assert scope.analyze_boost("CURRENT_AND_THE_SAME_NAME", Q, _c(101)) == pytest.approx(1.0)
    assert scope.analyze_boost("ALL", Q, _c(100)) == pytest.approx(1.0)


def test_suggest_boosts_launch_name_and_all():
    # same name up, same launch down.
    assert scope.suggest_boost("LAUNCH_NAME", Q, _c(101, "Nightly")) == pytest.approx(1.1)
    assert scope.suggest_boost("LAUNCH_NAME", Q, _c(100, "Nightly")) == pytest.approx(1.1 / 1.1)
    assert scope.suggest_boost("ALL", Q, _c(100, "Other")) == pytest.approx(1 / 1.1)


def test_suggest_boost_previous_launch():
    assert scope.suggest_boost("PREVIOUS_LAUNCH", Q, _c(99)) == pytest.approx(1.1)
    assert scope.suggest_boost("PREVIOUS_LAUNCH", Q, _c(101)) == pytest.approx(1.0)


def test_suggest_boost_default_stacks_name_and_launch():
    # same name AND same launch → boost twice.
    assert scope.suggest_boost(None, Q, _c(100, "Nightly")) == pytest.approx(1.1 * 1.1)
    assert scope.suggest_boost("CURRENT_LAUNCH", Q, _c(100, "Nightly")) == pytest.approx(1.1 * 1.1)
