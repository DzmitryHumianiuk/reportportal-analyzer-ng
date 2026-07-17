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


_SRC_CONF = {"rp": 1.0, "human": 0.9, "ai_suggested": 0.3}


def _hm(item_id, issue_type, source, days_ago=1.0, is_auto=False, confidence=None):
    return HashMatch(
        item_id=item_id,
        issue_type=issue_type,
        issue_type_group=issue_type[:2],
        label_source=source,
        label_ts=NOW - timedelta(days=days_ago),
        confidence=confidence if confidence is not None else _SRC_CONF.get(source, 0.3),
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


def test_stage_a_single_human_low_confidence_does_not_inherit():
    # §6.1: a single human match must have confidence ≥ 0.9 to inherit.
    matches = [_hm(1, "pb001", "human", confidence=0.5)]
    assert stage_a_inherit(123, matches, now=NOW) is None


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
    assert len(res.features) == 41


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
    assert res.issue_type == "si001"  # base group → RP default locator
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
    assert len(res.features) == 41


# --------------------------------------------------------------------------- #
# GBM serving integration (spec §6.5/§6.6)
# --------------------------------------------------------------------------- #
from dataclasses import dataclass  # noqa: E402

from analyzer_ng.core.decision import METHOD_GBM  # noqa: E402


@dataclass
class _FakeGbm:
    label: str
    max_prob: float
    probs: dict
    model_version: str = "gbm-test"


def _gbm(label, p):
    probs = {"pb": 0.1, "ab": 0.1, "si": 0.1, "nd": 0.1}
    probs[label] = p
    return lambda _vec: _FakeGbm(label=label, max_prob=p, probs=probs)


def test_gbm_auto_band():
    res = decide(DecisionInputs(exception_fp=0, gbm_predict=_gbm("si", 0.9)), now=NOW)
    assert res.method == METHOD_GBM
    assert res.action == ACTION_AUTO
    assert res.label == "si"
    assert res.issue_type == "si001"  # default locator (no candidate)
    assert res.confidence == 0.9
    assert res.probs["si"] == 0.9
    # Minor: the served model version is carried on the decision (no re-fetch).
    assert res.model_version == "gbm-test"


def test_gbm_abstain_still_carries_model_version():
    res = decide(DecisionInputs(exception_fp=0, gbm_predict=_gbm("pb", 0.3)), now=NOW)
    assert res.method == METHOD_GBM
    assert res.model_version == "gbm-test"


def test_rule_paths_have_no_model_version():
    res = decide(DecisionInputs(exception_fp=0), now=NOW)
    assert res.model_version is None


def test_gbm_suggest_band():
    res = decide(DecisionInputs(exception_fp=0, gbm_predict=_gbm("ab", 0.6)), now=NOW)
    assert res.method == METHOD_GBM
    assert res.action == ACTION_SUGGEST
    assert res.label == "ab"


def test_gbm_abstain_band_becomes_ti():
    res = decide(DecisionInputs(exception_fp=0, gbm_predict=_gbm("pb", 0.3)), now=NOW)
    assert res.method == METHOD_GBM
    assert res.label == "ti"
    assert res.issue_type == "ti"
    assert res.action == ACTION_ABSTAIN
    assert res.abstain_reason == "gbm_below_suggest"
    # Full distribution is still carried for audit even on abstain.
    assert set(res.probs) == {"pb", "ab", "si", "nd"}


def test_gbm_uses_candidate_locator_and_relevant_item():
    cand = Candidate(item_id=42, mode_id=None, issue_type="si_custom", cosine=0.9)
    res = decide(
        DecisionInputs(exception_fp=0, stage_c=[cand], gbm_predict=_gbm("si", 0.95)), now=NOW
    )
    assert res.issue_type == "si_custom"  # concrete historical locator wins
    assert res.relevant_item_id == 42


def test_stage_a_short_circuits_over_gbm():
    # A confident hash inherit must decide before the GBM is even consulted.
    res = decide(
        DecisionInputs(
            exception_fp=9,
            hash_matches=[_hm(7, "pb001", "rp")],
            gbm_predict=_gbm("si", 0.99),
        ),
        now=NOW,
    )
    assert res.method == METHOD_HASH
    assert res.label == "pb"


def test_gbm_overrides_seed_cold_fallback():
    # With a model shipped, the GBM decides even when a seed prior exists.
    res = decide(
        DecisionInputs(
            exception_fp=0,
            seed=SeedSignal(label="si", confidence=0.85),
            gbm_predict=_gbm("ab", 0.8),
        ),
        now=NOW,
    )
    assert res.method == METHOD_GBM
    assert res.label == "ab"
