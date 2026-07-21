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
    (0, "top1_cosine", "cosine similarity to best match", "cosine of best Stage-C candidate", "[0,1]", 0.0, "retrieval"),
    (1, "top1_rrf", "fused retrieval score of best match", "RRF (reciprocal-rank fusion) score of top1, post-boost", "[0,~0.05]", 0.0, "retrieval"),
    (2, "top1_jaccard", "template overlap with best match", "template-set Jaccard vs top1", "[0,1]", 0.0, "retrieval"),
    (3, "margin_cos", "similarity gap to runner-up match", "top1_cosine - top2_cosine", "[0,1]", 0.0, "retrieval"),
    (4, "mean_top5_cosine", "average similarity of top-5 matches", "mean cosine of top-5", "[0,1]", 0.0, "retrieval"),
    (5, "n_candidates", "how many candidates were retrieved", "len(candidates)/20", "[0,1]", 0.0, "retrieval"),
    (6, "label_hist_entropy", "label disagreement among candidates", "Shannon entropy of candidate labels / ln 4; 1 = maximally mixed", "[0,1]", 1.0, "retrieval"),
    (7, "top1_label_frac", "candidates agreeing with best match's label", "fraction of candidates sharing top1's label", "[0,1]", 0.0, "retrieval"),
    (8, "same_test_case_top1", "best match is the same test", "top1 has same test_case_hash", "{0,1}", 0.0, "retrieval"),
    (9, "same_error_hash_top1", "best match has identical error hash", "top1 error_hash equal", "{0,1}", 0.0, "retrieval"),
    (10, "same_exception_fp_top1", "best match has same exception fingerprint", "top1 exception_fp equal", "{0,1}", 0.0, "retrieval"),
    (11, "recency_top1", "how recent the best match is", "decay(age_days(top1)); 1 = labeled today", "(0,1]", 0.0, "retrieval"),
    (12, "src_weight_top1", "trust in best match's label source", "label-source weight of top1 (human > auto)", "[0.3,1]", 0.0, "retrieval"),
    (13, "hist_pb", "history vote for Product Bug", "weighted candidate mass for pb", "[0,1]", 0.0, "history"),
    (14, "hist_ab", "history vote for Automation Bug", "weighted candidate mass for ab", "[0,1]", 0.0, "history"),
    (15, "hist_si", "history vote for System Issue", "weighted candidate mass for si", "[0,1]", 0.0, "history"),
    (16, "hist_nd", "history vote for No Defect", "weighted candidate mass for nd", "[0,1]", 0.0, "history"),
    (17, "kb_top1_score", "match score of best KB failure-mode", "best score_mode (§6.2)", "[0,1]", 0.0, "kb"),
    (18, "kb_purity", "label consistency of matched KB mode", "purity of best mode", "[0,1]", 0.0, "kb"),
    (19, "kb_support", "evidence volume behind matched KB mode", "log1p(support)/log1p(1000)", "[0,1]", 0.0, "kb"),
    (20, "kb_same_fp", "KB mode shares this exception fingerprint", "exception_fp in best mode fps", "{0,1}", 0.0, "kb"),
    (21, "kb_prior_conf", "confidence of the matched seed rule", "prior confidence of matched seed mode", "[0,1]", 0.0, "kb"),
    (22, "seed_pb", "seed rule says Product Bug", "one-hot matched seed prior=pb", "{0,1}", 0.0, "kb"),
    (23, "seed_ab", "seed rule says Automation Bug", "one-hot matched seed prior=ab", "{0,1}", 0.0, "kb"),
    (24, "seed_si", "seed rule says System Issue", "one-hot matched seed prior=si", "{0,1}", 0.0, "kb"),
    (25, "seed_nd", "seed rule says No Defect", "one-hot matched seed prior=nd", "{0,1}", 0.0, "kb"),
    (26, "flakiness_score", "how flaky this test has been", "1 - p(same outcome as previous run), 30d", "[0,1]", 0.5, "history"),
    (27, "test_fail_rate_30d", "test's failure rate, last 30 days", "failures/runs, 30d", "[0,1]", 0.5, "history"),
    (28, "flips_30d", "pass/fail flips, last 30 days", "log1p(pass<->fail flips)/log1p(20)", "[0,1]", 0.0, "history"),
    (29, "co_failure_group_size", "how many failures share this signature", "log1p(group size)/log1p(200)", "[0,1]", 0.0, "grouping"),
    (30, "group_dominance", "group's share of launch failures", "group size / launch failures", "(0,1]", 0.0, "grouping"),
    (31, "launch_fail_fraction", "fraction of launch items failing", "launch failures / launch items", "[0,1]", 0.0, "grouping"),
    (32, "si_prior", "launch-wide burst prior (si)", "burst prior from §5: many simultaneous look-alike failures suggest a system issue", "[0,0.9]", 0.0, "grouping"),
    (33, "test_age_days", "how old this test is", "log1p(min(days,365))/log1p(365)", "[0,1]", 0.0, "history"),
    (34, "item_log_count", "how many logs survived filtering", "surviving logs / 20", "[0,1]", 0.0, "signal"),
    (35, "has_stacktrace", "failure carries a stack trace", "1 = a stack trace was detected in the logs (§1.3)", "{0,1}", 0.0, "signal"),
    (36, "is_assertion", "failure is an assertion error", "1 = the primary exception is an assertion (§3.4)", "{0,1}", 0.0, "signal"),
    (37, "is_merged_small_logs", "many small logs merged into one", "1 = small logs were merged into one document before analysis (§3.4)", "{0,1}", 0.0, "signal"),
    (38, "exception_count", "distinct exceptions in the logs", "min(len(exceptions),5)/5", "[0,1]", 0.0, "signal"),
    # v4 (2026-07-18) — LLM extractor + exact-detail agreement (discriminant). Labels
    # and definitions are the microcopy §3 strings so the waterfall/tooltip read plainly.
    (39, "llm_failing_layer", "LLM: which layer failed", "Layer where the failure sits according to the LLM extractor, as a code; 0 = unknown or LLM off (spec 04 §4.2).", "{0,1,2,3,4}", 0.0, "llm"),
    (40, "llm_error_class", "LLM: extracted error class", "Error class according to the LLM extractor, as a code; 0 = unknown or LLM off (spec 04 §4.2).", "{0..13}", 0.0, "llm"),
    (41, "status_codes_present", "has status codes to compare", "1 = this failure's text contains unmasked status codes (e.g. HTTP codes), so agreement can be checked at all.", "{0,1}", 0.0, "discriminant"),
    (42, "status_codes_match_top1", "status codes match best match", "1 = this failure and the best match carry exactly the same set of status codes.", "{0,1}", 0.0, "discriminant"),
    (43, "identifier_jaccard_top1", "shared identifiers with best match", "Overlap of identifier tokens (class names, endpoints, test names) with the best match, 0 to 1 (identifier-token Jaccard). 0 can mean \"nothing to compare\" — see \"has identifiers to compare\".", "[0,1]", 0.0, "discriminant"),
    (44, "hash_gate_blocked", "exact match found but rejected", "1 = an identical error ID (error_hash) existed, but the safety gate rejected inheriting its label because unmasked details disagreed (status codes / identifiers). A warning sign for look-alike traps.", "{0,1}", 0.0, "discriminant"),
    (45, "identifiers_present", "has identifiers to compare", "1 = this failure's message contains identifier tokens, so \"shared identifiers\" 0 means real disagreement, not missing data (v4, 2026-07-18b).", "{0,1}", 0.0, "discriminant"),
    (46, "ident_jaccard_source", "where the identifier neighbour came from", "Where the identifier comparison's neighbour came from: 1.0 = exact-hash match, 0.5 = closest history candidate (Stage C), 0 = no neighbour (v5, 2026-07-20).", "{0,0.5,1}", 0.0, "discriminant"),
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
