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
    discriminant_gate_blocked,
    kb_short_circuit,
    stage_a_inherit,
)
from analyzer_ng.core.features import SeedSignal
from analyzer_ng.db.repositories.models import Candidate

NOW = datetime(2026, 7, 16, tzinfo=UTC)


_SRC_CONF = {"rp": 1.0, "human": 0.9, "ai_suggested": 0.3}


def _hm(
    item_id,
    issue_type,
    source,
    days_ago=1.0,
    is_auto=False,
    confidence=None,
    status_codes=(),
    msg_tokens=frozenset(),
):
    return HashMatch(
        item_id=item_id,
        issue_type=issue_type,
        issue_type_group=issue_type[:2],
        label_source=source,
        label_ts=NOW - timedelta(days=days_ago),
        confidence=confidence if confidence is not None else _SRC_CONF.get(source, 0.3),
        is_auto_analyzed=is_auto,
        status_codes=tuple(status_codes),
        msg_tokens=frozenset(msg_tokens),
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


def test_stage_a_folds_custom_uppercase_locator_to_base_group():
    # A custom uppercase locator (PB_Regression) must inherit and case-fold to the
    # 'pb' base group, unified with core.features.base_group (the ti-guard uses the
    # empty-string fold, so a real label is never blocked by case).
    from analyzer_ng.core.decision import DecisionInputs, decide

    matches = [_hm(1, "PB_Regression", "rp"), _hm(2, "PB_Regression", "rp")]
    got = stage_a_inherit(9, matches, now=NOW)
    assert got is not None
    res = decide(DecisionInputs(exception_fp=9, hash_matches=matches), now=NOW)
    assert res.label == "pb"
    assert res.issue_type == "PB_Regression"  # concrete locator preserved verbatim


def test_stage_a_never_inherits_unrecognized_locator():
    # A non-GBM locator folds to "" → treated like ti, never inherited.
    matches = [_hm(1, "xx001", "rp"), _hm(2, "xx001", "rp")]
    assert stage_a_inherit(9, matches, now=NOW) is None


def test_stage_a_auto_nd_never_propagates():
    matches = [_hm(1, "nd001", "ai_suggested"), _hm(2, "nd001", "ai_suggested")]
    assert stage_a_inherit(1, matches, now=NOW) is None


# --------------------------------------------------------------------------- #
# Stage A discriminant gate (2026-07-18 errata) — error_hash over Drain3-masked
# templates collapses HTTP codes and app-area detail, so inherit is additionally
# gated on un-masked status_codes / salient message terms.
# --------------------------------------------------------------------------- #
def test_stage_a_status_code_mismatch_does_not_inherit():
    # HTTP 503 query vs a crowd labeled off HTTP 500 (masked to the same hash).
    matches = [
        _hm(1, "pb001", "rp", status_codes=("500",)),
        _hm(2, "pb001", "rp", status_codes=("500",)),
    ]
    assert stage_a_inherit(9, matches, query_status_codes=("503",), now=NOW) is None


def test_stage_a_msg_divergence_does_not_inherit():
    # Same exception_fp, no status codes on either side, but disjoint salient terms
    # (an NPE from a different app area) → gate filters everything out.
    matches = [
        _hm(1, "pb001", "rp", msg_tokens={"widget", "render"}),
        _hm(2, "pb001", "rp", msg_tokens={"widget", "render"}),
    ]
    assert (
        stage_a_inherit(9, matches, query_msg_tokens=frozenset({"checkout", "cart"}), now=NOW)
        is None
    )


def test_stage_a_msg_identical_still_inherits():
    matches = [
        _hm(1, "pb001", "rp", msg_tokens={"widget", "render"}),
        _hm(2, "pb001", "rp", msg_tokens={"widget", "render"}),
    ]
    got = stage_a_inherit(
        9, matches, query_msg_tokens=frozenset({"widget", "render"}), now=NOW
    )
    assert got is not None


def test_stage_a_param_noise_near_duplicate_inherits_unchanged():
    # MUST-group regression guard: identical status + msg discriminants inherit.
    matches = [
        _hm(1, "ab001", "rp", status_codes=("500",), msg_tokens={"timeout", "db"}),
        _hm(2, "ab001", "rp", status_codes=("500",), msg_tokens={"timeout", "db"}),
    ]
    got = stage_a_inherit(
        9,
        matches,
        query_status_codes=("500",),
        query_msg_tokens=frozenset({"timeout", "db"}),
        now=NOW,
    )
    assert got is not None


def test_stage_a_mixed_crowd_filtered_to_zero():
    # 2 unanimous 500-matches cannot out-vote a 503 query by count — the gate strips
    # them first, leaving nothing to inherit.
    matches = [
        _hm(1, "pb001", "rp", status_codes=("500",)),
        _hm(2, "pb001", "rp", status_codes=("500",)),
    ]
    assert stage_a_inherit(9, matches, query_status_codes=("503",), now=NOW) is None
    res = decide(
        DecisionInputs(
            exception_fp=9, hash_matches=matches, query_status_codes=("503",)
        ),
        now=NOW,
    )
    assert res.method != METHOD_HASH
    assert res.label == "ti"


def test_stage_a_both_empty_msg_tokens_passes():
    # Jaccard of two empty token sets is 1.0 → gate does not block.
    matches = [_hm(1, "pb001", "rp")]
    got = stage_a_inherit(9, matches, query_msg_tokens=frozenset(), now=NOW)
    assert got is not None and got.item_id == 1


# --- identifier-aware message gate (2026-07-18 errata) --------------------- #
# Shared NPE/assertion boilerplate must not out-vote the discriminating dotted
# identifiers; the gate scores over identifier tokens when either side has any.
_NPE = "java.lang.NullPointerException: Cannot invoke"
_AUTH = frozenset(
    f'{_NPE} "com.hawkins.shop.auth.Session.userId()" because "session" is null'.split()
)
_TAX = frozenset(
    f'{_NPE} "com.hawkins.shop.tax.Region.rate()" because "region" is null'.split()
)


def test_stage_a_identifier_divergence_blocks_despite_boilerplate():
    # All-token Jaccard of these two NPEs is ~0.5+ from shared boilerplate, but the
    # identifiers (Session.userId vs Region.rate) diverge → gate must block.
    matches = [_hm(1, "pb001", "rp", msg_tokens=_TAX), _hm(2, "pb001", "rp", msg_tokens=_TAX)]
    assert stage_a_inherit(9, matches, query_msg_tokens=_AUTH, now=NOW) is None


def test_stage_a_identical_identifiers_still_inherit():
    matches = [_hm(1, "ab001", "rp", msg_tokens=_AUTH), _hm(2, "ab001", "rp", msg_tokens=_AUTH)]
    got = stage_a_inherit(9, matches, query_msg_tokens=frozenset(_AUTH), now=NOW)
    assert got is not None


def test_stage_a_boilerplate_only_falls_back_to_all_tokens():
    # No identifier tokens on either side → fall back to all-token Jaccard (unchanged
    # behaviour): identical boilerplate still inherits, disjoint still blocks.
    same = [_hm(1, "pb001", "rp", msg_tokens={"timeout", "db"}),
            _hm(2, "pb001", "rp", msg_tokens={"timeout", "db"})]
    assert stage_a_inherit(9, same, query_msg_tokens=frozenset({"timeout", "db"}), now=NOW)
    other = [_hm(1, "pb001", "rp", msg_tokens={"widget", "render"}),
             _hm(2, "pb001", "rp", msg_tokens={"widget", "render"})]
    q = frozenset({"checkout", "cart"})
    assert stage_a_inherit(9, other, query_msg_tokens=q, now=NOW) is None


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
    assert len(res.features) == 45


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
    assert len(res.features) == 45


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


# --------------------------------------------------------------------------- #
# Discriminant-agreement signals reaching the GBM feature vector (errata)
# --------------------------------------------------------------------------- #
def test_discriminant_gate_blocked_helper():
    # ≥1 exact-hash match but the discriminant gate rejects all of them (503 vs 500).
    trap = [_hm(1, "pb001", "rp", status_codes=("500",))]
    assert discriminant_gate_blocked(9, trap, query_status_codes=("503",)) is True
    # Agreeing discriminants → not blocked.
    ok = [_hm(1, "pb001", "rp", status_codes=("503",))]
    assert discriminant_gate_blocked(9, ok, query_status_codes=("503",)) is False
    # No matches / no fingerprint → nothing to block.
    assert discriminant_gate_blocked(9, [], query_status_codes=("503",)) is False
    assert discriminant_gate_blocked(0, trap, query_status_codes=("503",)) is False


def test_decide_threads_hash_gate_blocked_into_features():
    # The 503 near-miss trap: exact hash exists (500-labeled) but status disagrees, so
    # Stage A abstains AND the feature snapshot records the trap for the GBM to learn.
    res = decide(
        DecisionInputs(
            exception_fp=9,
            hash_matches=[_hm(1, "pb001", "rp", status_codes=("500",))],
            query_status_codes=("503",),
        ),
        now=NOW,
    )
    assert res.method != METHOD_HASH  # gate blocked the inherit
    assert res.features["hash_gate_blocked"] == 1.0
    assert res.features["status_codes_present"] == 1.0
    assert res.features["status_codes_match_top1"] == 0.0


def test_decide_threads_status_match_when_discriminants_agree():
    res = decide(
        DecisionInputs(
            exception_fp=9,
            hash_matches=[_hm(1, "pb001", "rp", status_codes=("503",))],
            query_status_codes=("503",),
        ),
        now=NOW,
    )
    # Agreeing status → Stage A inherits; the snapshot still records the agreement.
    assert res.features["hash_gate_blocked"] == 0.0
    assert res.features["status_codes_present"] == 1.0
    assert res.features["status_codes_match_top1"] == 1.0


# --------------------------------------------------------------------------- #
# Deterministic boilerplate-only guard on the GBM suggest band (errata)
# --------------------------------------------------------------------------- #
def _plain_cand(**kw):
    base = dict(
        item_id=5,
        mode_id=None,
        cosine=0.93,
        issue_type="pb001",
        same_exception_fp=False,
        same_error_hash=False,
        jaccard_templates=0.0,
        msg_text="connection pool timeout exhausted",  # no identifier tokens
    )
    base.update(kw)
    return Candidate(**base)


# A query message whose only salient tokens are identifiers with NO overlap with the
# neighbour's boilerplate — a NullReferenceException on a novel call site.
_NOVEL_Q = frozenset({"NullReferenceException", "getUserProfile"})


def test_boilerplate_only_neighbor_demotes_suggest_to_abstain():
    res = decide(
        DecisionInputs(
            exception_fp=0,
            stage_c=[_plain_cand()],
            query_msg_tokens=_NOVEL_Q,
            gbm_predict=_gbm("pb", 0.6),  # suggest band
        ),
        now=NOW,
    )
    assert res.method == METHOD_GBM
    assert res.label == "ti"
    assert res.action == ACTION_ABSTAIN
    assert res.abstain_reason == "gbm_boilerplate_only_neighbor"


def test_guard_does_not_fire_with_any_structural_overlap():
    # Any of: exact fp, exact hash, template overlap, or identifier-token overlap
    # ≥ threshold keeps the suggest.
    for overlap in (
        {"same_exception_fp": True},
        {"same_error_hash": True},
        {"jaccard_templates": 0.4},
        {"msg_text": "NullReferenceException getUserProfile"},  # identifier overlap
    ):
        res = decide(
            DecisionInputs(
                exception_fp=0,
                stage_c=[_plain_cand(**overlap)],
                query_msg_tokens=_NOVEL_Q,
                gbm_predict=_gbm("pb", 0.6),
            ),
            now=NOW,
        )
        assert res.action == ACTION_SUGGEST, overlap
        assert res.label == "pb"


def test_guard_bypassed_on_auto_band():
    # p ≥ τ_auto is auto-labeled regardless of a boilerplate-only neighbour.
    res = decide(
        DecisionInputs(
            exception_fp=0,
            stage_c=[_plain_cand()],
            query_msg_tokens=_NOVEL_Q,
            gbm_predict=_gbm("pb", 0.9),
        ),
        now=NOW,
    )
    assert res.action == ACTION_AUTO
    assert res.label == "pb"


def test_guard_both_empty_identifier_sets_follow_stage_a_fallback():
    # Neither side has identifier tokens → fall back to all-token Jaccard (Stage A
    # semantics). Identical boilerplate → Jaccard 1.0 ≥ threshold → guard does NOT
    # fire even with no fingerprint/hash/template overlap.
    q = frozenset({"connection", "pool", "timeout", "exhausted"})
    res = decide(
        DecisionInputs(
            exception_fp=0,
            stage_c=[_plain_cand()],
            query_msg_tokens=q,
            gbm_predict=_gbm("pb", 0.6),
        ),
        now=NOW,
    )
    assert res.action == ACTION_SUGGEST
    assert res.label == "pb"


# ---------------------------------------------------------------------------
# Label provenance threading (suggest modelInfo "human-confirmed" vs "auto")
# ---------------------------------------------------------------------------
class TestRelevantProvenance:
    def test_stage_a_inherit_carries_source_and_auto_flag(self):
        m = _hm(7, "pb001", "rp", confidence=1.0)
        inputs = DecisionInputs(
            exception_fp=1,
            hash_matches=[m, _hm(8, "pb001", "rp")],
            kb_candidates=[],
            seed=None,
            stage_c=[],
            stage_c_ages_days=[],
            feature_ctx=None,
        )
        d = decide(inputs)
        assert d.method == "hash"
        assert d.relevant_label_source == "rp"
        assert d.relevant_is_auto_analyzed is False

    def test_gbm_result_carries_candidate_source(self):
        cand = Candidate(
            item_id=11, mode_id=None, issue_type="ab001",
            label_source="ai_suggested", cosine=0.9, rrf_score=0.03,
            same_exception_fp=True,
        )
        inputs = DecisionInputs(
            exception_fp=1,
            hash_matches=[],
            kb_candidates=[],
            seed=None,
            stage_c=[cand],
            stage_c_ages_days=[1.0],
            feature_ctx=None,
            gbm_predict=_gbm("ab", 0.6),
        )
        d = decide(inputs)
        assert d.method == "gbm"
        assert d.relevant_item_id == 11
        assert d.relevant_label_source == "ai_suggested"
