"""Matching stages + cold decision + policy bands (spec 03 §6.1-§6.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from analyzer_ng.core.decision import (
    ACTION_ABSTAIN,
    ACTION_AUTO,
    ACTION_SUGGEST,
    METHOD_HASH,
    METHOD_KB,
    METHOD_RULE_COLD,
    DecisionInputs,
    HashMatch,
    best_kb_match,
    decide,
    kb_short_circuit,
    stage_a_inherit,
)
from analyzer_ng.core.features import SeedSignal
from analyzer_ng.db.repositories.models import Candidate

NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _hm(item_id, issue_type, source, days_ago=1.0, is_auto=False):
    return HashMatch(
        item_id=item_id,
        issue_type=issue_type,
        issue_type_group=issue_type[:2],
        label_source=source,
        label_ts=NOW - timedelta(days=days_ago),
        is_auto_analyzed=is_auto,
    )


# --------------------------------------------------------------------------- #
# Stage A guards (acceptance §11)
# --------------------------------------------------------------------------- #
def test_stage_a_single_ai_match_does_not_inherit():
    matches = [_hm(1, "pb001", "ai_suggested")]
    assert stage_a_inherit(123, matches, now=NOW) is None


def test_stage_a_single_human_match_inherits():
    matches = [_hm(1, "pb001", "rp")]
    got = stage_a_inherit(123, matches, now=NOW)
    assert got is not None and got.item_id == 1


def test_stage_a_two_unanimous_matches_inherit():
    matches = [_hm(1, "ab001", "ai_suggested"), _hm(2, "ab001", "ai_suggested")]
    got = stage_a_inherit(123, matches, now=NOW)
    assert got is not None


def test_stage_a_disagreeing_matches_do_not_inherit():
    matches = [_hm(1, "ab001", "rp"), _hm(2, "pb001", "rp")]
    assert stage_a_inherit(123, matches, now=NOW) is None


def test_stage_a_skips_when_fp_zero():
    assert stage_a_inherit(0, [_hm(1, "pb001", "rp")], now=NOW) is None


def test_stage_a_age_guard():
    matches = [_hm(1, "pb001", "rp"), _hm(2, "pb001", "rp")]
    old = [_hm(1, "pb001", "rp", days_ago=200), _hm(2, "pb001", "rp", days_ago=201)]
    assert stage_a_inherit(1, matches, now=NOW) is not None
    assert stage_a_inherit(1, old, now=NOW) is None


def test_stage_a_auto_nd_never_propagates():
    matches = [_hm(1, "nd001", "ai_suggested"), _hm(2, "nd001", "ai_suggested")]
    assert stage_a_inherit(1, matches, now=NOW) is None


# --------------------------------------------------------------------------- #
# KB scoring / short-circuit
# --------------------------------------------------------------------------- #
def _kbc(mode_id, cosine, jac, fp, purity, support, status):
    return Candidate(
        item_id=None, mode_id=mode_id, cosine=cosine, jaccard_templates=jac,
        same_exception_fp=fp, mode_purity=purity, mode_support=support, mode_status=status,
        issue_type="si001",
    )


def test_kb_score_weights():
    (kb, _cand) = best_kb_match([_kbc(1, 1.0, 1.0, True, 0.9, 12, "confirmed")])
    assert kb.score_mode == 0.6 + 0.25 + 0.15


def test_kb_short_circuit_requires_all_conditions():
    (kb, _c) = best_kb_match([_kbc(1, 1.0, 1.0, True, 0.96, 12, "confirmed")])
    assert kb_short_circuit(kb) is True
    (kb2, _c2) = best_kb_match([_kbc(1, 1.0, 1.0, True, 0.96, 3, "confirmed")])
    assert kb_short_circuit(kb2) is False  # support < 10
    (kb3, _c3) = best_kb_match([_kbc(1, 1.0, 1.0, True, 0.96, 12, "candidate")])
    assert kb_short_circuit(kb3) is False  # not confirmed


# --------------------------------------------------------------------------- #
# Full decide() paths + policy bands
# --------------------------------------------------------------------------- #
def test_decide_stage_a_auto():
    res = decide(
        DecisionInputs(exception_fp=99, hash_matches=[_hm(7, "pb001", "rp")]), now=NOW
    )
    assert res.method == METHOD_HASH
    assert res.action == ACTION_AUTO
    assert res.confidence == 0.95
    assert res.relevant_item_id == 7
    assert res.issue_type == "pb001"
    assert len(res.features) == 39


def test_decide_kb_short_circuit():
    res = decide(
        DecisionInputs(
            exception_fp=1,
            kb_candidates=[_kbc(3, 1.0, 1.0, True, 0.97, 20, "confirmed")],
        ),
        now=NOW,
    )
    assert res.method == METHOD_KB
    assert res.action == ACTION_AUTO
    assert res.matched_mode_id == 3


def test_decide_seed_prior_auto_band():
    res = decide(
        DecisionInputs(exception_fp=0, seed=SeedSignal(label="si", confidence=0.85)), now=NOW
    )
    assert res.method == METHOD_RULE_COLD
    assert res.label == "si"
    assert res.action == ACTION_AUTO  # 0.85 ≥ τ_auto


def test_decide_seed_prior_suggest_band():
    res = decide(
        DecisionInputs(exception_fp=0, seed=SeedSignal(label="ab", confidence=0.7)), now=NOW
    )
    assert res.action == ACTION_SUGGEST  # 0.45 ≤ 0.7 < 0.75


def test_decide_seed_below_threshold_abstains():
    res = decide(
        DecisionInputs(exception_fp=0, seed=SeedSignal(label="ab", confidence=0.6)), now=NOW
    )
    # confidence 0.6 < SEED_PRIOR_MIN_CONF 0.7 → no seed decision → abstain.
    assert res.label == "ti"
    assert res.action == ACTION_ABSTAIN
    assert res.abstain_reason == "no_confident_rule"


def test_decide_pure_abstain_features_present():
    res = decide(DecisionInputs(exception_fp=0), now=NOW)
    assert res.label == "ti"
    assert res.action == ACTION_ABSTAIN
    assert len(res.features) == 39
