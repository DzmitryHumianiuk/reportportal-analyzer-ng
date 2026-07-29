"""The ``plabel=`` token: how much the model believes THIS row's own label.

Issue #8, last open item. ``conf=`` reports the calibrated probability of the
model's OWN answer, whatever group that answer named. It says nothing about a row
that offers a different group — an abstain with argmax ``pb`` at 0.63 next to a
System Issue row means 0.63 is the ``pb`` number, and printing it beside a System
Issue pill would state something the model never said. Every suggest row now also
carries the calibrated probability of its own base group, and carries NOTHING when
that probability is not known (hash / KB short circuit, cold rule, legacy result).
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from analyzer_ng.amqp.models import AnalyzerConf, Log, TestItemInfo
from analyzer_ng.core.analysis import AnalysisEngine
from analyzer_ng.core.decision import (
    ACTION_ABSTAIN,
    ACTION_SUGGEST,
    METHOD_GBM,
    METHOD_HASH,
    METHOD_KB,
    METHOD_RULE_COLD,
    DecisionResult,
    calibrated_label_prob,
)
from analyzer_ng.db.repositories.models import Candidate

TestItemInfo.__test__ = False  # type: ignore[attr-defined]

PROJECT = 77
ITEM = 2001
LAUNCH = 555


def _engine(**kwargs: Any) -> AnalysisEngine:
    return AnalysisEngine(
        retrieval=object(),  # type: ignore[arg-type]
        kb=object(),
        stats=object(),
        pipeline=object(),  # type: ignore[arg-type]
        sidecar=None,
        **kwargs,
    )


def _info() -> TestItemInfo:
    return TestItemInfo(
        testItemId=ITEM,
        launchId=LAUNCH,
        launchName="nightly",
        launchNumber=12,
        clusterId=999,
        project=PROJECT,
        analyzerConfig=AnalyzerConf(numberOfLogLines=7, minShouldMatch=42),
        logs=[Log(logId=6001, message="boom")],
    )


def _rep() -> Any:
    return SimpleNamespace(signature=SimpleNamespace(is_merged_small_logs=False))


def _cand(item_id: int, cosine: float, issue_type: str = "ab001") -> Candidate:
    return Candidate(
        item_id=item_id,
        mode_id=None,
        cosine=cosine,
        issue_type=issue_type,
        label_source="human",
    )


def _decision(
    *,
    label: str = "ti",
    issue_type: str = "ti",
    confidence: float = 0.0,
    method: str = METHOD_GBM,
    action: str = ACTION_SUGGEST,
    abstain_reason: str | None = None,
    relevant_item_id: int | None = None,
    stage_c: list[Candidate] | None = None,
    probs: dict[str, float] | None = None,
    calibrated_max_prob: float | None = None,
) -> DecisionResult:
    return DecisionResult(
        label=label,
        issue_type=issue_type,
        confidence=confidence,
        method=method,
        action=action,
        abstain_reason=abstain_reason,
        relevant_item_id=relevant_item_id,
        matched_mode_id=None,
        features={},
        stage_c=stage_c or [],
        probs=probs if probs is not None else {},
        calibrated_max_prob=calibrated_max_prob,
    )


def _plabel_of(row: Any) -> float | None:
    m = re.search(r"(?:^|;)plabel=([0-9.]+)(?:;|$)", row.modelInfo)
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# The number on the row is the row's own label, not the model's argmax
# --------------------------------------------------------------------------- #


def test_token_carries_offered_label_not_argmax() -> None:
    """The stand case: the model held pb at a calibrated 0.63 and made no call.
    A System Issue row must report the System Issue probability — never the 0.63,
    which belongs to a group that row does not offer."""
    decision = _decision(
        confidence=0.63,
        action=ACTION_ABSTAIN,
        abstain_reason="gbm_below_suggest",
        calibrated_max_prob=0.63,
        probs={"pb": 0.70, "ab": 0.12, "si": 0.15, "nd": 0.03},
        stage_c=[_cand(41, 0.98, "si001"), _cand(42, 0.97, "pb001")],
    )
    out = _engine()._render_suggestions(_info(), _rep(), decision, elapsed=0.1)

    by_item = {r.relevantItem: r for r in out}
    # si: raw 0.15 of the 0.30 non-argmax mass, rescaled onto the leftover 1-0.63.
    assert _plabel_of(by_item[41]) == 0.1850
    assert _plabel_of(by_item[41]) != 0.63
    # pb IS the argmax, so it takes the calibrated p* itself.
    assert _plabel_of(by_item[42]) == 0.63


def test_token_matches_conf_on_the_decisions_own_row() -> None:
    """On the row the model actually chose, its belief in that row's label is the
    same number conf= reports — the two tokens can never disagree."""
    decision = _decision(
        label="ab",
        issue_type="ab001",
        confidence=0.60,
        relevant_item_id=41,
        calibrated_max_prob=0.60,
        probs={"pb": 0.20, "ab": 0.55, "si": 0.15, "nd": 0.10},
        stage_c=[_cand(41, 0.60, "ab001")],
    )
    out = _engine()._render_suggestions(_info(), _rep(), decision, elapsed=0.1)

    assert ";conf=0.6000" in out[0].modelInfo
    assert ";plabel=0.6000" in out[0].modelInfo


def test_token_sits_before_the_decline_narration() -> None:
    """why= stays the last token (its free text may contain ';')."""
    decision = _decision(
        confidence=0.32,
        action=ACTION_ABSTAIN,
        abstain_reason="gbm_below_suggest",
        calibrated_max_prob=0.32,
        probs={"pb": 0.64, "ab": 0.19, "si": 0.03, "nd": 0.14},
        stage_c=[_cand(41, 0.44, "pb001")],
    )
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )

    assert ";plabel=" in out[0].modelInfo
    assert out[0].modelInfo.endswith(
        ";ek=decline;why=model probability stayed below the suggest threshold"
    )


# --------------------------------------------------------------------------- #
# Silence when the distribution is not known
# --------------------------------------------------------------------------- #


def test_no_token_when_distribution_is_unknown() -> None:
    """No calibrated distribution behind the decision → no token at all, so the
    UI reads a missing token as "we do not know" and renders nothing."""
    decision = _decision(
        stage_c=[_cand(41, 0.98, "pb001"), _cand(42, 0.97, "si001")],
    )
    out = _engine()._render_suggestions(_info(), _rep(), decision, elapsed=0.1)

    assert len(out) == 2
    assert all(";plabel=" not in r.modelInfo for r in out)


def test_deterministic_decision_emits_no_token() -> None:
    """A Stage-A hash inherit is not a model belief. Its probs is the placeholder
    {label: confidence}, which says nothing about the other groups, so neither its
    own row nor a neighbour row may claim a probability."""
    decision = _decision(
        label="pb",
        issue_type="pb001",
        confidence=0.95,
        method=METHOD_HASH,
        relevant_item_id=41,
        probs={"pb": 0.95},
        stage_c=[_cand(41, 0.95, "pb001"), _cand(42, 0.90, "si001")],
    )
    out = _engine()._render_suggestions(_info(), _rep(), decision, elapsed=0.1)

    assert [r.relevantItem for r in out] == [41, 42]
    assert all(";plabel=" not in r.modelInfo for r in out)
    assert ";conf=0.9500" in out[0].modelInfo  # conf= semantics untouched


def test_row_outside_the_base_groups_emits_no_token() -> None:
    """A locator the GBM has no class for (ti / unrecognized) has no probability."""
    decision = _decision(
        confidence=0.50,
        calibrated_max_prob=0.50,
        probs={"pb": 0.50, "ab": 0.20, "si": 0.20, "nd": 0.10},
        stage_c=[_cand(41, 0.98, "ti001"), _cand(42, 0.97, "pb001")],
    )
    out = _engine()._render_suggestions(_info(), _rep(), decision, elapsed=0.1)

    by_item = {r.relevantItem: r for r in out}
    assert _plabel_of(by_item[41]) is None
    assert _plabel_of(by_item[42]) == 0.50


# --------------------------------------------------------------------------- #
# The mapping itself
# --------------------------------------------------------------------------- #


def test_calibrated_label_prob_is_a_distribution_topped_by_p_star() -> None:
    """The argmax keeps its measured calibrated value and the leftover mass is
    split by the raw ratios, so the four groups still sum to 1."""
    decision = _decision(
        calibrated_max_prob=0.40,
        probs={"pb": 0.70, "ab": 0.12, "si": 0.15, "nd": 0.03},
    )
    values = {g: calibrated_label_prob(decision, g) for g in ("pb", "ab", "si", "nd")}

    assert values["pb"] == 0.40
    assert all(v is not None for v in values.values())
    assert abs(sum(v for v in values.values() if v is not None) - 1.0) < 1e-9
    # raw order is preserved among the non-argmax groups
    assert values["si"] > values["ab"] > values["nd"]  # type: ignore[operator]


def test_calibrated_label_prob_unknown_paths_return_none() -> None:
    """Every path that never ran the calibrated model answers None, not a number."""
    for method in (METHOD_HASH, METHOD_KB, METHOD_RULE_COLD):
        placeholder = _decision(label="pb", method=method, confidence=0.9, probs={"pb": 0.9})
        assert calibrated_label_prob(placeholder, "pb") is None
        assert calibrated_label_prob(placeholder, "si") is None

    no_probs = _decision(calibrated_max_prob=0.8)
    assert calibrated_label_prob(no_probs, "pb") is None

    empty_model = _decision(calibrated_max_prob=0.8, probs=dict.fromkeys(("pb", "si"), 0.0))
    assert calibrated_label_prob(empty_model, "pb") is None


def test_calibrated_label_prob_certain_argmax_leaves_nothing_for_the_rest() -> None:
    """A raw distribution with all its mass on one group gives the others 0.0 —
    a known zero, not an unknown (the guard must not divide by zero)."""
    decision = _decision(calibrated_max_prob=0.75, probs={"pb": 1.0, "ab": 0.0, "si": 0.0})

    assert calibrated_label_prob(decision, "pb") == 0.75
    assert calibrated_label_prob(decision, "si") == 0.0
