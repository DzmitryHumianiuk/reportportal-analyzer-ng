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
    base = dict(
        item_id=1,
        mode_id=None,
        cosine=0.9,
        rrf_score=0.03,
        jaccard_templates=0.5,
        issue_type="pb001",
        label_source="rp",
    )
    base.update(kw)
    return Candidate(**base)


def test_exactly_64_features_unique_order():
    # 39 classical (spec 03 §6.4) + 4 discriminant-agreement columns (2026-07-18 errata)
    # + 1 identifiers_present indicator (2026-07-18b, v4)
    # + 1 ident_jaccard_source provenance column (2026-07-20, v5)
    # + 19 one-hot LLM-extractor columns (2026-07-21, v6: 5 failing_layer + 14
    #   error_class, replacing the 2 ordinal columns that flipped as the cache warmed).
    assert len(FEATURES) == 64
    names = [f.name for f in FEATURES]
    assert len(set(names)) == 64
    assert names[0] == "top1_cosine"
    assert names[38] == "exception_count"
    # v6: the two LLM ordinals are GONE from the registry (dropped, not re-mapped).
    assert "llm_failing_layer" not in names
    assert "llm_error_class" not in names
    assert names[39:45] == [
        "status_codes_present",
        "status_codes_match_top1",
        "identifier_jaccard_top1",
        "hash_gate_blocked",
        "identifiers_present",
        "ident_jaccard_source",
    ]
    # v6 one-hot block, appended at the tail: 5 failing-layer then 14 error-class.
    assert names[45:50] == [
        "llm_failing_layer_unknown",
        "llm_failing_layer_test_code",
        "llm_failing_layer_app_code",
        "llm_failing_layer_infrastructure",
        "llm_failing_layer_environment",
    ]
    assert names[50:] == [
        "llm_error_class_unknown",
        "llm_error_class_assertion",
        "llm_error_class_timeout",
        "llm_error_class_connection",
        "llm_error_class_http_4xx",
        "llm_error_class_http_5xx",
        "llm_error_class_null_reference",
        "llm_error_class_not_found",
        "llm_error_class_permission",
        "llm_error_class_data_format",
        "llm_error_class_resource_exhausted",
        "llm_error_class_config",
        "llm_error_class_concurrency",
        "llm_error_class_other",
    ]


def test_empty_context_returns_defaults_no_nan():
    values = extract_features(FeatureContext())
    vec = to_vector(values)
    assert len(vec) == 64
    # v6: on an empty/cold context the one-hot LLM blocks land on their explicit
    # ``unknown`` level (1.0), every real level 0.0 — the honest cold-cache / LLM-off
    # state, and exactly one column hot per group.
    assert values["llm_failing_layer_unknown"] == 1.0
    assert values["llm_error_class_unknown"] == 1.0
    assert sum(v for k, v in values.items() if k.startswith("llm_failing_layer_")) == 1.0
    assert sum(v for k, v in values.items() if k.startswith("llm_error_class_")) == 1.0
    assert all(math.isfinite(v) for v in vec)
    # Documented defaults for the missing-data case.
    assert values["label_hist_entropy"] == 1.0
    assert values["flakiness_score"] == 0.5
    assert values["test_fail_rate_30d"] == 0.5
    assert values["top1_cosine"] == 0.0


def test_all_ranges_respected_on_rich_context():
    cands = [
        _cand(
            item_id=1,
            cosine=0.95,
            issue_type="pb001",
            same_error_hash=True,
            same_exception_fp=True,
            same_test_case=True,
        ),
        _cand(item_id=2, cosine=0.80, issue_type="ab001", label_source="human"),
        _cand(item_id=3, cosine=0.70, issue_type="si001", label_source="ai_suggested"),
    ]
    ctx = FeatureContext(
        candidates=cands,
        candidate_ages_days=[10.0, 45.0, 200.0],
        kb_best=KBMatch(
            mode_id=7,
            score_mode=0.82,
            purity=0.9,
            support=25,
            same_exception_fp=True,
            status="confirmed",
        ),
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
    assert values["identifier_jaccard_top1"] == 0.0  # empty neighbour text, empty query
    assert values["ident_jaccard_source"] == 0.5  # stage-C top-1 exists (no hash top-1)
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


def test_llm_extractor_columns_one_hot_encoded():
    # v6 (tech-debt #3, Defect A): a cache hit supplies the categoricals; they ONE-HOT
    # encode. The named level is hot (1.0), every sibling — including ``unknown`` — 0.0.
    ctx = FeatureContext(llm_failing_layer="infrastructure", llm_error_class="http_5xx")
    values = extract_features(ctx)
    assert values["llm_failing_layer_infrastructure"] == 1.0
    assert values["llm_failing_layer_unknown"] == 0.0
    assert values["llm_failing_layer_app_code"] == 0.0
    assert values["llm_error_class_http_5xx"] == 1.0
    assert values["llm_error_class_unknown"] == 0.0
    # Exactly one column hot per group (a true one-hot, no leakage across levels).
    assert sum(v for k, v in values.items() if k.startswith("llm_failing_layer_")) == 1.0
    assert sum(v for k, v in values.items() if k.startswith("llm_error_class_")) == 1.0


def test_llm_one_hot_covers_every_level_including_unknown():
    from analyzer_ng.core.features import ERROR_CLASS_LEVELS, FAILING_LAYER_LEVELS

    for lvl in FAILING_LAYER_LEVELS:
        v = extract_features(FeatureContext(llm_failing_layer=lvl))
        assert v[f"llm_failing_layer_{lvl}"] == 1.0
        assert sum(x for k, x in v.items() if k.startswith("llm_failing_layer_")) == 1.0
    for lvl in ERROR_CLASS_LEVELS:
        v = extract_features(FeatureContext(llm_error_class=lvl))
        assert v[f"llm_error_class_{lvl}"] == 1.0
        assert sum(x for k, x in v.items() if k.startswith("llm_error_class_")) == 1.0


def test_llm_one_hot_unknown_on_unrecognised_value():
    # An unrecognised category is NOT NaN and is NOT blended into a real class — it lands
    # on the explicit ``unknown`` level, so a cold-cache / garbage read is a distinct,
    # honest state (the Defect-A volatility fix).
    junk = extract_features(FeatureContext(llm_error_class="bogus", llm_failing_layer="nope"))
    assert junk["llm_error_class_unknown"] == 1.0
    assert junk["llm_failing_layer_unknown"] == 1.0
    assert all(
        math.isfinite(v)
        for k, v in junk.items()
        if k.startswith(("llm_error_class_", "llm_failing_layer_"))
    )


def test_llm_one_hot_backfills_old_snapshot_to_unknown():
    # Defect A back-fill: a v5 snapshot has no one-hot columns at all. Assembled for the
    # current (v6) registry, the missing one-hot columns back-fill to their registered
    # defaults — each ``*_unknown`` column defaults to 1.0 — so an old cold-cache row is
    # read as the honest ``unknown`` state, never blended into a real class.
    all_names = [f.name for f in FEATURES]
    classical_only = {n: 0.2 for n in all_names[:39]}  # predates every appended block
    vec = to_vector_for(classical_only, all_names)
    idx = {n: i for i, n in enumerate(all_names)}
    assert vec[idx["llm_failing_layer_unknown"]] == 1.0
    assert vec[idx["llm_error_class_unknown"]] == 1.0
    assert vec[idx["llm_failing_layer_infrastructure"]] == 0.0
    assert vec[idx["llm_error_class_http_5xx"]] == 0.0


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
    absent = extract_features(FeatureContext(top1_status_codes=("500",), has_hash_top1=True))
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
    # cannot inflate it). Query carries identifier tokens → identifiers_present=1.0.
    assert hi["identifier_jaccard_top1"] == 1.0
    assert hi["identifiers_present"] == 1.0
    assert lo["identifier_jaccard_top1"] == 0.0
    assert lo["identifiers_present"] == 1.0
    # When identifier tokens exist on either side the feature equals the gate's Jaccard.
    assert hi["identifier_jaccard_top1"] == identifier_jaccard(q, same)
    assert lo["identifier_jaccard_top1"] == identifier_jaccard(q, diff)
    # Provenance: an exact-hash neighbour supplied the Jaccard → source = 1.0.
    assert hi["ident_jaccard_source"] == 1.0
    assert lo["ident_jaccard_source"] == 1.0
    # Absent (0.0) with source=0.0 when there is no neighbour at all to compare against.
    none_ctx = extract_features(FeatureContext(query_msg_tokens=q))
    assert none_ctx["identifier_jaccard_top1"] == 0.0
    assert none_ctx["ident_jaccard_source"] == 0.0


def test_identifier_jaccard_top1_uses_stage_c_neighbour_when_no_hash_match():
    # The item-3688 shape: NO exact-hash neighbour, but a strong Stage-C candidate whose
    # message shares the query's identifier tokens. v4 scored this 0.0 (trap signature);
    # v5 must describe the evidence that exists — Jaccard vs the Stage-C top-1 > 0, with
    # provenance source=0.5 and identifiers_present=1 (NOT the boilerplate trap).
    q = frozenset({"cannot", "invoke", "Session.userId", "because", "null"})
    neighbour = _cand(item_id=9, msg_text="cannot invoke Session.userId because null")
    v = extract_features(
        FeatureContext(query_msg_tokens=q, candidates=[neighbour], candidate_ages_days=[0.0])
    )
    assert v["identifier_jaccard_top1"] > 0.0
    assert v["identifier_jaccard_top1"] == 1.0  # identifier token Session.userId shared
    assert v["ident_jaccard_source"] == 0.5  # provenance: Stage-C candidate
    assert v["identifiers_present"] == 1.0


def test_identifier_jaccard_top1_stage_c_disjoint_identifiers_preserves_trap():
    # A REAL trap with no hash match: the Stage-C top-1's identifiers are disjoint from
    # the query's → Jaccard 0.0 with identifiers_present=1 (the NET-XUN-15 signature must
    # survive the v5 broadening; only genuine textual overlap should lift the score).
    q = frozenset({"cannot", "invoke", "Session.userId", "because", "null"})
    neighbour = _cand(item_id=9, msg_text="cannot invoke Region.rate because null")
    v = extract_features(
        FeatureContext(query_msg_tokens=q, candidates=[neighbour], candidate_ages_days=[0.0])
    )
    assert v["identifier_jaccard_top1"] == 0.0  # disjoint identifiers → trap preserved
    assert v["ident_jaccard_source"] == 0.5
    assert v["identifiers_present"] == 1.0


def test_identifier_jaccard_top1_hash_neighbour_takes_priority_over_stage_c():
    # When BOTH an exact-hash neighbour and a Stage-C candidate exist, the exact-hash
    # neighbour wins (source=1.0) — v5 only changes behaviour where hash matches are
    # absent, so hash-present behaviour is byte-identical to v4.
    q = frozenset({"cannot", "invoke", "Session.userId", "because", "null"})
    hash_same = frozenset({"cannot", "invoke", "Session.userId", "because", "null"})
    stage_c_diff = _cand(item_id=9, msg_text="cannot invoke Region.rate because null")
    v = extract_features(
        FeatureContext(
            query_msg_tokens=q,
            top1_msg_tokens=hash_same,
            has_hash_top1=True,
            candidates=[stage_c_diff],
            candidate_ages_days=[0.0],
        )
    )
    assert v["identifier_jaccard_top1"] == 1.0  # from the hash neighbour, not stage-C
    assert v["ident_jaccard_source"] == 1.0


def test_identifier_jaccard_top1_nothing_to_compare_is_zero_not_match():
    # v4/v5: neither side carries an identifier token (pure boilerplate). The Stage-A
    # GATE falls back to all-token Jaccard (→ 1.0 for identical boilerplate), but the
    # FEATURE must encode "nothing to compare" as 0.0, never "match", present=0 to say so.
    boiler = frozenset({"cannot", "invoke", "because", "null"})
    v = extract_features(
        FeatureContext(query_msg_tokens=boiler, top1_msg_tokens=boiler, has_hash_top1=True)
    )
    assert identifier_jaccard(boiler, boiler) == 1.0  # gate concession (unchanged)
    assert v["identifier_jaccard_top1"] == 0.0  # feature: nothing to compare
    assert v["identifiers_present"] == 0.0


def test_identifier_jaccard_top1_no_neighbour_at_all_is_absent():
    # No hash neighbour AND no Stage-C candidate → the feature stays at its 0.0 default
    # and source=0.0, even though the query carries identifier tokens (present=1).
    q = frozenset({"cannot", "invoke", "Session.userId", "because", "null"})
    v = extract_features(FeatureContext(query_msg_tokens=q))
    assert v["identifier_jaccard_top1"] == 0.0
    assert v["ident_jaccard_source"] == 0.0
    assert v["identifiers_present"] == 1.0


def test_hash_gate_blocked_flag_passes_through():
    on = extract_features(FeatureContext(hash_gate_blocked=True))
    off = extract_features(FeatureContext(hash_gate_blocked=False))
    assert on["hash_gate_blocked"] == 1.0
    assert off["hash_gate_blocked"] == 0.0


# --------------------------------------------------------------------------- #
# Forward/backward-compat vector assembly (schema invariant)
# --------------------------------------------------------------------------- #
def test_to_vector_for_old_list_drops_new_columns():
    # A model trained on an OLDER, shorter feature list, fed a full NEW snapshot,
    # assembles exactly its own trained columns in its own order — columns it never saw
    # are dropped (schema-robust serving invariant).
    old_names = [f.name for f in FEATURES][:41]
    full = extract_features(FeatureContext(query_status_codes=("503",), has_hash_top1=True))
    vec = to_vector_for(full, old_names)
    assert len(vec) == 41
    assert vec == [float(full[n]) for n in old_names]


def test_to_vector_for_new_list_backfills_missing_with_defaults():
    # A NEW-list (v6) model fed an OLDER snapshot back-fills the missing columns with
    # their registered defaults, never dropping the row. Note the one-hot ``*_unknown``
    # columns default to 1.0 (their honest cold state), which FEATURE_DEFAULTS carries.
    all_names = [f.name for f in FEATURES]
    old_snapshot = {n: 0.5 for n in all_names[:41]}  # an older, shorter snapshot
    vec = to_vector_for(old_snapshot, all_names)
    assert len(vec) == 64
    assert vec[:41] == [0.5] * 41
    assert vec[41:] == [FEATURE_DEFAULTS[n] for n in all_names[41:]]
    # the back-filled one-hot unknown columns are 1.0, real levels 0.0.
    idx = {n: i for i, n in enumerate(all_names)}
    assert vec[idx["llm_failing_layer_unknown"]] == 1.0
    assert vec[idx["llm_error_class_unknown"]] == 1.0


def test_launch_fail_fraction_zero_when_total_unknown():
    # §6.4 #31: 0 when the launch's total item count is unknown (launch_items=0),
    # even though group_dominance still uses the known failing count.
    values = extract_features(FeatureContext(group_size=3, launch_failures=6, launch_items=0))
    assert values["launch_fail_fraction"] == 0.0
    assert values["group_dominance"] == 3 / 6
