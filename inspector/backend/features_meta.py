"""Static metadata for the 46-element LightGBM feature vector (spec 03 §6.4, v4).

Used to render the Features & Decision view with human-readable names, the exact
definition, the valid range and the documented default so an inspector viewer
can read a stored ``suggestion.features`` vector the way the model does.

Indices 0-38 are the original schema; 39-45 are the v4 additions (llm extractor
+ discriminant / exact-detail-agreement groups, 2026-07-18). Their labels and
definitions are the ready-to-paste microcopy strings (lens-microcopy §3).
"""

from __future__ import annotations

# ruff: noqa: E501 — this is a fixed-width data table; wrapping each tuple hurts readability.

# (index, key, human label, definition, range, default, group)
FEATURE_DEFS: list[tuple[int, str, str, str, str, float, str]] = [
    (0, "top1_cosine", "Top-1 cosine", "cosine of best Stage-C candidate", "[0,1]", 0.0, "retrieval"),
    (1, "top1_rrf", "Top-1 RRF", "RRF score of top1 (post-boost)", "[0,~0.05]", 0.0, "retrieval"),
    (2, "top1_jaccard", "Top-1 Jaccard", "template-set Jaccard vs top1", "[0,1]", 0.0, "retrieval"),
    (3, "margin_cos", "Cosine margin", "top1_cosine - top2_cosine", "[0,1]", 0.0, "retrieval"),
    (4, "mean_top5_cosine", "Mean top-5 cosine", "mean cosine of top-5", "[0,1]", 0.0, "retrieval"),
    (5, "n_candidates", "# candidates", "len(candidates)/20", "[0,1]", 0.0, "retrieval"),
    (6, "label_hist_entropy", "Label entropy", "Shannon entropy of candidate labels / ln 4", "[0,1]", 1.0, "retrieval"),
    (7, "top1_label_frac", "Top-1 label frac", "fraction of candidates sharing top1's label", "[0,1]", 0.0, "retrieval"),
    (8, "same_test_case_top1", "Same test-case", "top1 has same test_case_hash", "{0,1}", 0.0, "retrieval"),
    (9, "same_error_hash_top1", "Same error-hash", "top1 error_hash equal", "{0,1}", 0.0, "retrieval"),
    (10, "same_exception_fp_top1", "Same exception-fp", "top1 exception_fp equal", "{0,1}", 0.0, "retrieval"),
    (11, "recency_top1", "Top-1 recency", "decay(age_days(top1))", "(0,1]", 0.0, "retrieval"),
    (12, "src_weight_top1", "Top-1 source weight", "label-source weight of top1", "[0.3,1]", 0.0, "retrieval"),
    (13, "hist_pb", "Hist pb", "weighted candidate mass for pb", "[0,1]", 0.0, "history"),
    (14, "hist_ab", "Hist ab", "weighted candidate mass for ab", "[0,1]", 0.0, "history"),
    (15, "hist_si", "Hist si", "weighted candidate mass for si", "[0,1]", 0.0, "history"),
    (16, "hist_nd", "Hist nd", "weighted candidate mass for nd", "[0,1]", 0.0, "history"),
    (17, "kb_top1_score", "KB top-1 score", "best score_mode (§6.2)", "[0,1]", 0.0, "kb"),
    (18, "kb_purity", "KB purity", "purity of best mode", "[0,1]", 0.0, "kb"),
    (19, "kb_support", "KB support", "log1p(support)/log1p(1000)", "[0,1]", 0.0, "kb"),
    (20, "kb_same_fp", "KB same-fp", "exception_fp in best mode fps", "{0,1}", 0.0, "kb"),
    (21, "kb_prior_conf", "KB prior conf", "prior confidence of matched seed mode", "[0,1]", 0.0, "kb"),
    (22, "seed_pb", "Seed pb", "one-hot matched seed prior=pb", "{0,1}", 0.0, "kb"),
    (23, "seed_ab", "Seed ab", "one-hot matched seed prior=ab", "{0,1}", 0.0, "kb"),
    (24, "seed_si", "Seed si", "one-hot matched seed prior=si", "{0,1}", 0.0, "kb"),
    (25, "seed_nd", "Seed nd", "one-hot matched seed prior=nd", "{0,1}", 0.0, "kb"),
    (26, "flakiness_score", "Flakiness", "1 - p(same outcome as previous run), 30d", "[0,1]", 0.5, "history"),
    (27, "test_fail_rate_30d", "Fail rate 30d", "failures/runs, 30d", "[0,1]", 0.5, "history"),
    (28, "flips_30d", "Flips 30d", "log1p(pass<->fail flips)/log1p(20)", "[0,1]", 0.0, "history"),
    (29, "co_failure_group_size", "Group size", "log1p(group size)/log1p(200)", "[0,1]", 0.0, "grouping"),
    (30, "group_dominance", "Group dominance", "group size / launch failures", "(0,1]", 0.0, "grouping"),
    (31, "launch_fail_fraction", "Launch fail frac", "launch failures / launch items", "[0,1]", 0.0, "grouping"),
    (32, "si_prior", "SI prior", "burst prior from §5", "[0,0.9]", 0.0, "grouping"),
    (33, "test_age_days", "Test age", "log1p(min(days,365))/log1p(365)", "[0,1]", 0.0, "history"),
    (34, "item_log_count", "Log count", "surviving logs / 20", "[0,1]", 0.0, "signal"),
    (35, "has_stacktrace", "Has stacktrace", "§1.3 flag", "{0,1}", 0.0, "signal"),
    (36, "is_assertion", "Is assertion", "§3.4 flag", "{0,1}", 0.0, "signal"),
    (37, "is_merged_small_logs", "Merged small logs", "§3.4 flag", "{0,1}", 0.0, "signal"),
    (38, "exception_count", "Exception count", "min(len(exceptions),5)/5", "[0,1]", 0.0, "signal"),
    # v4 (2026-07-18) — LLM extractor + exact-detail agreement (discriminant). Labels
    # and definitions are the microcopy §3 strings so the waterfall/tooltip read plainly.
    (39, "llm_failing_layer", "LLM: which layer failed", "Layer where the failure sits according to the LLM extractor, as a code; 0 = unknown or LLM off (spec 04 §4.2).", "{0,1,2,3,4}", 0.0, "llm"),
    (40, "llm_error_class", "LLM: error class", "Error class according to the LLM extractor, as a code; 0 = unknown or LLM off (spec 04 §4.2).", "{0..13}", 0.0, "llm"),
    (41, "status_codes_present", "has status codes to compare", "1 = this failure's text contains unmasked status codes (e.g. HTTP codes), so agreement can be checked at all.", "{0,1}", 0.0, "discriminant"),
    (42, "status_codes_match_top1", "status codes match best match", "1 = this failure and the best match carry exactly the same set of status codes.", "{0,1}", 0.0, "discriminant"),
    (43, "identifier_jaccard_top1", "shared identifiers with best match", "Overlap of identifier tokens (class names, endpoints, test names) with the best match, 0 to 1 (identifier-token Jaccard). 0 can mean \"nothing to compare\" — see \"has identifiers to compare\".", "[0,1]", 0.0, "discriminant"),
    (44, "hash_gate_blocked", "exact match found but rejected", "1 = an identical error ID (error_hash) existed, but the safety gate rejected inheriting its label because unmasked details disagreed (status codes / identifiers). A warning sign for look-alike traps.", "{0,1}", 0.0, "discriminant"),
    (45, "identifiers_present", "has identifiers to compare", "1 = this failure's message contains identifier tokens, so \"shared identifiers\" 0 means real disagreement, not missing data (v4, 2026-07-18b).", "{0,1}", 0.0, "discriminant"),
]

FEATURE_INDEX = {key: i for i, key, *_ in FEATURE_DEFS}


def describe(key: str) -> dict[str, object]:
    for idx, k, label, definition, rng, default, group in FEATURE_DEFS:
        if k == key:
            return {
                "index": idx,
                "key": k,
                "label": label,
                "definition": definition,
                "range": rng,
                "default": default,
                "group": group,
            }
    return {
        "index": -1,
        "key": key,
        "label": key,
        "definition": "(unknown feature — not in schema)",
        "range": "?",
        "default": 0.0,
        "group": "other",
    }
