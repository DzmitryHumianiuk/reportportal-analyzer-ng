"""The 39-feature extractor for the decision layer (spec 03 §6.4).

One module owns the exact ordered feature list (``FEATURES``); the full vector is
snapshotted as a name→value JSON object into ``suggestion.features`` at prediction
time, so training (T3.1) reads serving-identical vectors by construction (risk
register #5). The GBM does not exist yet — the values are still extracted and
stored on every decision.

All values are floats, clamped to their documented range, with a defined default
when the underlying data is missing: **no NaN/inf ever reaches the model**
(acceptance §11 "returns exactly len(FEATURES) floats, no NaN/inf").
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from analyzer_ng.db.repositories.models import Candidate

# Bump when the ordered FEATURES list or any definition changes; stamped into
# suggestion.features and model_artifact (spec 03 §6.4).
FEATURE_SCHEMA_VER = 1

# Base issue-type groups the model predicts; ``ti`` is the abstain outcome.
BASE_LABELS = ("pb", "ab", "si", "nd")
_LN2 = math.log(2.0)
_LN4 = math.log(4.0)
# si_prior is capped at 0.9 (spec §5 / feature #32 range [0,0.9]).
SI_PRIOR_MAX = 0.9


@dataclass(frozen=True)
class FeatureDef:
    """One feature's name and its missing-data default (spec §6.4 table)."""

    name: str
    default: float


# The 39 features in EXACT order (spec 03 §6.4). Index == position in the vector.
FEATURES: tuple[FeatureDef, ...] = (
    FeatureDef("top1_cosine", 0.0),
    FeatureDef("top1_rrf", 0.0),
    FeatureDef("top1_jaccard", 0.0),
    FeatureDef("margin_cos", 0.0),
    FeatureDef("mean_top5_cosine", 0.0),
    FeatureDef("n_candidates", 0.0),
    FeatureDef("label_hist_entropy", 1.0),
    FeatureDef("top1_label_frac", 0.0),
    FeatureDef("same_test_case_top1", 0.0),
    FeatureDef("same_error_hash_top1", 0.0),
    FeatureDef("same_exception_fp_top1", 0.0),
    FeatureDef("recency_top1", 0.0),
    FeatureDef("src_weight_top1", 0.0),
    FeatureDef("hist_pb", 0.0),
    FeatureDef("hist_ab", 0.0),
    FeatureDef("hist_si", 0.0),
    FeatureDef("hist_nd", 0.0),
    FeatureDef("kb_top1_score", 0.0),
    FeatureDef("kb_purity", 0.0),
    FeatureDef("kb_support", 0.0),
    FeatureDef("kb_same_fp", 0.0),
    FeatureDef("kb_prior_conf", 0.0),
    FeatureDef("seed_pb", 0.0),
    FeatureDef("seed_ab", 0.0),
    FeatureDef("seed_si", 0.0),
    FeatureDef("seed_nd", 0.0),
    FeatureDef("flakiness_score", 0.5),
    FeatureDef("test_fail_rate_30d", 0.5),
    FeatureDef("flips_30d", 0.0),
    FeatureDef("co_failure_group_size", 0.0),
    FeatureDef("group_dominance", 0.0),
    FeatureDef("launch_fail_fraction", 0.0),
    FeatureDef("si_prior", 0.0),
    FeatureDef("test_age_days", 0.0),
    FeatureDef("item_log_count", 0.0),
    FeatureDef("has_stacktrace", 0.0),
    FeatureDef("is_assertion", 0.0),
    FeatureDef("is_merged_small_logs", 0.0),
    FeatureDef("exception_count", 0.0),
)

assert len(FEATURES) == 39, "spec 03 §6.4 defines exactly 39 features"

# Candidate.label_source vocabulary → label-source weight (spec §6.4 src_w).
# rp=rp_defect_update (human confirm) 1.0; human=human_ui accept 0.9;
# ai_suggested (unreviewed, conservative) 0.3; seed prior 0.6; unknown 0.3.
_SRC_WEIGHT = {"rp": 1.0, "human": 0.9, "ai_suggested": 0.3, "seed": 0.6}
_SRC_WEIGHT_DEFAULT = 0.3


def src_weight(label_source: str | None) -> float:
    """Label-source weight ``src_w(e)`` for a candidate (spec §6.4)."""
    return _SRC_WEIGHT.get(label_source or "", _SRC_WEIGHT_DEFAULT)


def decay(days: float) -> float:
    """Time-decay ``exp(-ln2 · d / 90)`` (half-life 90 days, spec §6.4)."""
    if days <= 0:
        return 1.0
    return math.exp(-_LN2 * days / 90.0)


def _base(issue_type: str | None) -> str | None:
    if not issue_type:
        return None
    prefix = "".join(c for c in issue_type[:2] if c.isalpha())
    return prefix if prefix in BASE_LABELS else None


@dataclass(frozen=True)
class KBMatch:
    """A Stage-B KB-mode match scored per spec §6.2."""

    mode_id: int
    score_mode: float
    purity: float
    support: int
    same_exception_fp: bool
    status: str = "candidate"


@dataclass(frozen=True)
class SeedSignal:
    """The matched seed mode's prior (spec §6.2 / §9)."""

    label: str
    confidence: float


@dataclass(frozen=True)
class FeatureContext:
    """All raw inputs the 39 features are derived from (no DB access here)."""

    candidates: Sequence[Candidate] = ()  # Stage-C, ordered best-first (post-boost)
    candidate_ages_days: Sequence[float] = ()  # aligned to candidates; label age in days
    kb_best: KBMatch | None = None
    seed: SeedSignal | None = None
    # test_history_stats (spec 02 §2.10) — None ⇒ defaults.
    flakiness_score: float | None = None
    window_runs: int = 0
    window_failures: int = 0
    window_flips: int = 0
    test_age_days: float | None = None
    # group / launch context (spec §5)
    group_size: int = 1
    launch_failures: int = 0
    launch_items: int = 0
    si_prior: float = 0.0
    # per-item flags (spec §1.3 / §3.4)
    item_log_count: int = 0
    has_stacktrace: bool = False
    is_assertion: bool = False
    is_merged_small_logs: bool = False
    exception_count: int = 0


def _clamp01(value: float) -> float:
    if value != value or value in (math.inf, -math.inf):  # NaN/inf guard
        return 0.0
    return max(0.0, min(1.0, value))


def _cos(c: Candidate) -> float:
    return c.cosine if c.cosine is not None else 0.0


def extract_features(ctx: FeatureContext) -> dict[str, float]:
    """Compute the ordered 39-feature vector as a name→value mapping (spec §6.4)."""
    values: dict[str, float] = {f.name: f.default for f in FEATURES}
    cands = list(ctx.candidates)

    if cands:
        top1 = cands[0]
        top1_base = _base(top1.issue_type)
        values["top1_cosine"] = _clamp01(_cos(top1))
        values["top1_rrf"] = max(0.0, top1.rrf_score)
        values["top1_jaccard"] = _clamp01(top1.jaccard_templates)
        top2_cos = _cos(cands[1]) if len(cands) > 1 else 0.0
        values["margin_cos"] = _clamp01(_cos(top1) - top2_cos)
        top5 = cands[:5]
        values["mean_top5_cosine"] = _clamp01(sum(_cos(c) for c in top5) / len(top5))
        values["n_candidates"] = _clamp01(len(cands) / 20.0)

        # Label histogram over base groups.
        hist: dict[str, int] = {}
        for c in cands:
            b = _base(c.issue_type)
            if b is not None:
                hist[b] = hist.get(b, 0) + 1
        labeled = sum(hist.values())
        if labeled:
            probs = [n / labeled for n in hist.values()]
            entropy = -sum(p * math.log(p) for p in probs if p > 0)
            values["label_hist_entropy"] = _clamp01(entropy / _LN4)
        else:
            values["label_hist_entropy"] = 1.0
        if top1_base is not None and labeled:
            values["top1_label_frac"] = _clamp01(hist.get(top1_base, 0) / labeled)

        values["same_test_case_top1"] = 1.0 if top1.same_test_case else 0.0
        values["same_error_hash_top1"] = 1.0 if top1.same_error_hash else 0.0
        values["same_exception_fp_top1"] = 1.0 if top1.same_exception_fp else 0.0

        ages = list(ctx.candidate_ages_days)
        top1_age = ages[0] if ages else 0.0
        values["recency_top1"] = _clamp01(decay(top1_age))
        values["src_weight_top1"] = src_weight(top1.label_source)

        # Weighted history mass per base label: Σ cos·decay·src_w, ÷ Σ all.
        mass: dict[str, float] = {b: 0.0 for b in BASE_LABELS}
        total_mass = 0.0
        for i, c in enumerate(cands):
            age = ages[i] if i < len(ages) else 0.0
            w = max(0.0, _cos(c)) * decay(age) * src_weight(c.label_source)
            total_mass += w
            b = _base(c.issue_type)
            if b is not None:
                mass[b] += w
        if total_mass > 0:
            for b in BASE_LABELS:
                values[f"hist_{b}"] = _clamp01(mass[b] / total_mass)

    if ctx.kb_best is not None:
        kb = ctx.kb_best
        values["kb_top1_score"] = _clamp01(kb.score_mode)
        values["kb_purity"] = _clamp01(kb.purity)
        values["kb_support"] = _clamp01(math.log1p(max(0, kb.support)) / math.log1p(1000))
        values["kb_same_fp"] = 1.0 if kb.same_exception_fp else 0.0

    if ctx.seed is not None:
        values["kb_prior_conf"] = _clamp01(ctx.seed.confidence)
        seed_base = _base(ctx.seed.label)
        if seed_base is not None:
            values[f"seed_{seed_base}"] = 1.0

    if ctx.flakiness_score is not None:
        values["flakiness_score"] = _clamp01(ctx.flakiness_score)
    if ctx.window_runs > 0:
        values["test_fail_rate_30d"] = _clamp01(ctx.window_failures / ctx.window_runs)
    values["flips_30d"] = _clamp01(math.log1p(max(0, ctx.window_flips)) / math.log1p(20))

    values["co_failure_group_size"] = _clamp01(
        math.log1p(max(0, ctx.group_size)) / math.log1p(200)
    )
    if ctx.launch_failures > 0:
        values["group_dominance"] = _clamp01(ctx.group_size / ctx.launch_failures)
    if ctx.launch_items > 0:
        values["launch_fail_fraction"] = _clamp01(ctx.launch_failures / ctx.launch_items)
    values["si_prior"] = max(0.0, min(SI_PRIOR_MAX, ctx.si_prior))

    if ctx.test_age_days is not None:
        capped = min(max(0.0, ctx.test_age_days), 365.0)
        values["test_age_days"] = _clamp01(math.log1p(capped) / math.log1p(365))
    values["item_log_count"] = _clamp01(ctx.item_log_count / 20.0)
    values["has_stacktrace"] = 1.0 if ctx.has_stacktrace else 0.0
    values["is_assertion"] = 1.0 if ctx.is_assertion else 0.0
    values["is_merged_small_logs"] = 1.0 if ctx.is_merged_small_logs else 0.0
    values["exception_count"] = _clamp01(min(ctx.exception_count, 5) / 5.0)

    return values


def to_vector(values: dict[str, float]) -> list[float]:
    """Order a name→value mapping into the canonical 39-float vector."""
    return [float(values.get(f.name, f.default)) for f in FEATURES]


def feature_names() -> list[str]:
    """The ordered feature names (for ``modelFeatureNames`` on suggest replies)."""
    return [f.name for f in FEATURES]
