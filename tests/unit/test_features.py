"""39-feature extractor (spec 03 §6.4) — order, ranges, no NaN/inf, defaults."""

from __future__ import annotations

import math

from analyzer_ng.core.features import (
    FEATURE_DEFAULTS,
    FEATURES,
    FeatureContext,
    KBMatch,
    SeedSignal,
    decay,
    extract_features,
    identifier_jaccard,
    src_weight,
    to_vector,
    to_vector_for,
)
from analyzer_ng.db.repositories.models import Candidate


def _cand(**kw):
    base = dict(item_id=1, mode_id=None, cosine=0.9, rrf_score=0.03, jaccard_templates=0.5,
               issue_type="pb001", label_source="rp")
    base.update(kw)
    return Candidate(**base)


def test_exactly_45_features_unique_order():
    # 39 classical (spec 03 §6.4) + 2 LLM-extractor columns (spec 04 §4.2)
    # + 4 discriminant-agreement columns (2026-07-18 errata).
    assert len(FEATURES) == 45
    names = [f.name for f in FEATURES]
    assert len(set(names)) == 45
    assert names[0] == "top1_cosine"
    assert names[38] == "exception_count"
    assert names[39:41] == ["llm_failing_layer", "llm_error_class"]
    assert names[41:] == [
        "status_codes_present",
        "status_codes_match_top1",
        "identifier_jaccard_top1",
        "hash_gate_blocked",
    ]


def test_empty_context_returns_defaults_no_nan():
    values = extract_features(FeatureContext())
    vec = to_vector(values)
    assert len(vec) == 45
    # LLM-extractor columns default to the ``unknown`` sentinel (spec 04 §4.2).
    assert values["llm_failing_layer"] == 0.0
    assert values["llm_error_class"] == 0.0
    assert all(math.isfinite(v) for v in vec)
    # Documented defaults for the missing-data case.
    assert values["label_hist_entropy"] == 1.0
    assert values["flakiness_score"] == 0.5
    assert values["test_fail_rate_30d"] == 0.5
    assert values["top1_cosine"] == 0.0


def test_all_ranges_respected_on_rich_context():
    cands = [
        _cand(item_id=1, cosine=0.95, issue_type="pb001", same_error_hash=True,
              same_exception_fp=True, same_test_case=True),
        _cand(item_id=2, cosine=0.80, issue_type="ab001", label_source="human"),
        _cand(item_id=3, cosine=0.70, issue_type="si001", label_source="ai_suggested"),
    ]
    ctx = FeatureContext(
        candidates=cands,
        candidate_ages_days=[10.0, 45.0, 200.0],
        kb_best=KBMatch(mode_id=7, score_mode=0.82, purity=0.9, support=25,
                        same_exception_fp=True, status="confirmed"),
        seed=SeedSignal(label="si", confidence=0.8),
        flakiness_score=0.3,
        window_runs=20,
        window_failures=5,
        window_flips=4,
        test_age_days=120.0,
        group_size=12,
        launch_failures=20,
        launch_items=50,
        si_prior=0.7,
        item_log_count=8,
        has_stacktrace=True,
        is_assertion=True,
        is_merged_small_logs=False,
        exception_count=3,
    )
    values = extract_features(ctx)
    for f in FEATURES:
        v = values[f.name]
        assert math.isfinite(v)
    # spot-checks
    assert values["top1_cosine"] == 0.95
    assert values["status_codes_present"] == 0.0  # no query status codes given
    assert values["status_codes_match_top1"] == 0.0  # no hash top-1 evidence
    assert values["identifier_jaccard_top1"] == 0.0
    assert values["hash_gate_blocked"] == 0.0
    assert values["same_error_hash_top1"] == 1.0
    assert values["same_exception_fp_top1"] == 1.0
    assert values["same_test_case_top1"] == 1.0
    assert values["seed_si"] == 1.0
    assert values["seed_pb"] == 0.0
    assert values["kb_same_fp"] == 1.0
    assert 0.0 <= values["si_prior"] <= 0.9
    assert values["exception_count"] == 3 / 5
    assert values["item_log_count"] == 8 / 20
    assert values["has_stacktrace"] == 1.0
    assert values["test_fail_rate_30d"] == 0.25
    assert values["group_dominance"] == 12 / 20
    assert values["launch_fail_fraction"] == 20 / 50


def test_margin_and_mean_top5():
    cands = [_cand(item_id=1, cosine=0.9), _cand(item_id=2, cosine=0.6)]
    values = extract_features(FeatureContext(candidates=cands, candidate_ages_days=[0, 0]))
    assert values["margin_cos"] == 0.9 - 0.6
    assert values["mean_top5_cosine"] == (0.9 + 0.6) / 2


def test_label_hist_entropy_uniform_is_one():
    cands = [
        _cand(item_id=1, issue_type="pb001"),
        _cand(item_id=2, issue_type="ab001"),
        _cand(item_id=3, issue_type="si001"),
        _cand(item_id=4, issue_type="nd001"),
    ]
    values = extract_features(FeatureContext(candidates=cands, candidate_ages_days=[0] * 4))
    assert values["label_hist_entropy"] == 1.0  # 4 uniform labels → entropy/ln4 = 1


def test_hist_masses_sum_to_one_when_labeled():
    cands = [
        _cand(item_id=1, issue_type="pb001", cosine=0.9),
        _cand(item_id=2, issue_type="si001", cosine=0.8),
    ]
    values = extract_features(FeatureContext(candidates=cands, candidate_ages_days=[0, 0]))
    total = values["hist_pb"] + values["hist_ab"] + values["hist_si"] + values["hist_nd"]
    assert total == 1.0


def test_src_weight_and_decay():
    assert src_weight("rp") == 1.0
    assert src_weight("human") == 0.9
    assert src_weight("ai_suggested") == 0.3
    assert src_weight(None) == 0.3
    assert decay(0) == 1.0
    assert decay(90) == 0.5


def test_si_prior_capped_at_09():
    values = extract_features(FeatureContext(si_prior=5.0))
    assert values["si_prior"] == 0.9


def test_llm_extractor_columns_ordinal_encoded():
    # spec 04 §4.2: a cache hit supplies the categoricals; they ordinal-encode.
    ctx = FeatureContext(llm_failing_layer="infrastructure", llm_error_class="http_5xx")
    values = extract_features(ctx)
    assert values["llm_failing_layer"] == 3.0
    assert values["llm_error_class"] == 5.0
    # An unrecognised value falls back to the sentinel (0), never NaN.
    junk = extract_features(FeatureContext(llm_error_class="bogus"))
    assert junk["llm_error_class"] == 0.0
    assert math.isfinite(junk["llm_error_class"])


# --------------------------------------------------------------------------- #
# Discriminant-agreement features (2026-07-18 errata)
# --------------------------------------------------------------------------- #
def test_status_codes_match_top1_encoding():
    # present + exact set match with the top-1 exact-hash neighbour → 1.0/1.0.
    match = extract_features(
        FeatureContext(
            query_status_codes=("503",),
            top1_status_codes=("503",),
            has_hash_top1=True,
        )
    )
    assert match["status_codes_present"] == 1.0
    assert match["status_codes_match_top1"] == 1.0

    # present but disagreeing set (the near-miss trap: 503 query vs 500 neighbour).
    mismatch = extract_features(
        FeatureContext(
            query_status_codes=("503",),
            top1_status_codes=("500",),
            has_hash_top1=True,
        )
    )
    assert mismatch["status_codes_present"] == 1.0
    assert mismatch["status_codes_match_top1"] == 0.0


def test_status_codes_present_zero_means_nothing_to_compare():
    # Query carries no status code → present=0 ("nothing to compare"), match stays 0.
    absent = extract_features(
        FeatureContext(top1_status_codes=("500",), has_hash_top1=True)
    )
    assert absent["status_codes_present"] == 0.0
    assert absent["status_codes_match_top1"] == 0.0
    # No top-1 evidence at all → match stays at its 0.0 default even if present=1.
    no_evidence = extract_features(FeatureContext(query_status_codes=("500",)))
    assert no_evidence["status_codes_present"] == 1.0
    assert no_evidence["status_codes_match_top1"] == 0.0


def test_identifier_jaccard_top1_matches_shared_tokenizer():
    q = frozenset({"cannot", "invoke", "Session.userId", "because", "null"})
    same = frozenset({"cannot", "invoke", "Session.userId", "because", "null"})
    diff = frozenset({"cannot", "invoke", "Region.rate", "because", "null"})
    hi = extract_features(
        FeatureContext(query_msg_tokens=q, top1_msg_tokens=same, has_hash_top1=True)
    )
    lo = extract_features(
        FeatureContext(query_msg_tokens=q, top1_msg_tokens=diff, has_hash_top1=True)
    )
    # Identical identifier token → 1.0; divergent identifier token → 0.0 (boilerplate
    # cannot inflate it). The feature reuses the Stage-A gate tokenizer exactly.
    assert hi["identifier_jaccard_top1"] == identifier_jaccard(q, same) == 1.0
    assert lo["identifier_jaccard_top1"] == identifier_jaccard(q, diff) == 0.0
    # Absent when there is no top-1 neighbour to compare against.
    assert extract_features(FeatureContext(query_msg_tokens=q))["identifier_jaccard_top1"] == 0.0


def test_hash_gate_blocked_flag_passes_through():
    on = extract_features(FeatureContext(hash_gate_blocked=True))
    off = extract_features(FeatureContext(hash_gate_blocked=False))
    assert on["hash_gate_blocked"] == 1.0
    assert off["hash_gate_blocked"] == 0.0


# --------------------------------------------------------------------------- #
# Forward/backward-compat vector assembly (schema invariant)
# --------------------------------------------------------------------------- #
def test_to_vector_for_old_list_drops_new_columns():
    # A model trained on the OLD (pre-errata) 41-name list, fed a full NEW snapshot,
    # assembles exactly its 41 trained columns in its own order — the 4 new columns
    # it never saw are dropped.
    old_names = [f.name for f in FEATURES][:41]
    full = extract_features(
        FeatureContext(query_status_codes=("503",), has_hash_top1=True)
    )
    vec = to_vector_for(full, old_names)
    assert len(vec) == 41
    assert vec == [float(full[n]) for n in old_names]


def test_to_vector_for_new_list_backfills_missing_with_defaults():
    # A NEW-list model fed an OLD 41-key snapshot back-fills the 4 missing columns
    # with their registered defaults (all 0.0 here), never dropping the row.
    all_names = [f.name for f in FEATURES]
    old_snapshot = {n: 0.5 for n in all_names[:41]}  # lacks the 4 errata columns
    vec = to_vector_for(old_snapshot, all_names)
    assert len(vec) == 45
    assert vec[:41] == [0.5] * 41
    assert vec[41:] == [FEATURE_DEFAULTS[n] for n in all_names[41:]]


def test_launch_fail_fraction_zero_when_total_unknown():
    # §6.4 #31: 0 when the launch's total item count is unknown (launch_items=0),
    # even though group_dominance still uses the known failing count.
    values = extract_features(
        FeatureContext(group_size=3, launch_failures=6, launch_items=0)
    )
    assert values["launch_fail_fraction"] == 0.0
    assert values["group_dominance"] == 3 / 6
