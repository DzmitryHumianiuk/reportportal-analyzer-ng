"""39-feature extractor (spec 03 §6.4) — order, ranges, no NaN/inf, defaults."""

from __future__ import annotations

import math

from analyzer_ng.core.features import (
    FEATURES,
    FeatureContext,
    KBMatch,
    SeedSignal,
    decay,
    extract_features,
    src_weight,
    to_vector,
)
from analyzer_ng.db.repositories.models import Candidate


def _cand(**kw):
    base = dict(item_id=1, mode_id=None, cosine=0.9, rrf_score=0.03, jaccard_templates=0.5,
               issue_type="pb001", label_source="rp")
    base.update(kw)
    return Candidate(**base)


def test_exactly_39_features_unique_order():
    assert len(FEATURES) == 39
    names = [f.name for f in FEATURES]
    assert len(set(names)) == 39
    assert names[0] == "top1_cosine"
    assert names[38] == "exception_count"


def test_empty_context_returns_defaults_no_nan():
    values = extract_features(FeatureContext())
    vec = to_vector(values)
    assert len(vec) == 39
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


def test_launch_fail_fraction_zero_when_total_unknown():
    # §6.4 #31: 0 when the launch's total item count is unknown (launch_items=0),
    # even though group_dominance still uses the known failing count.
    values = extract_features(
        FeatureContext(group_size=3, launch_failures=6, launch_items=0)
    )
    assert values["launch_fail_fraction"] == 0.0
    assert values["group_dominance"] == 3 / 6
