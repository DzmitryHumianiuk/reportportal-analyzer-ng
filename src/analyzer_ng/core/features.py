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
import re
from collections.abc import Sequence
from dataclasses import dataclass

from analyzer_ng.db.repositories.models import Candidate

# Bump when the ordered FEATURES list or any definition changes; stamped into
# suggestion.features and model_artifact (spec 03 §6.4). v2 appends the two
# optional LLM-extractor categorical columns (spec 04 §4.2). v3 appends the four
# discriminant-agreement columns (2026-07-18 errata) so the un-masked evidence the
# Stage-A gate uses also reaches the GBM. v4 (2026-07-18b) re-encodes the two
# "agreement" columns so "nothing to compare" no longer reads as "match": both-empty
# status sets and both-no-identifier message sets now encode 0.0 (not 1.0), and a new
# ``identifiers_present`` indicator lets the model separate 0.0-because-no-identifiers
# from 0.0-because-they-disagree (mirrors ``status_codes_present``).
# v5 (2026-07-20) redefines ``identifier_jaccard_top1`` to describe the evidence that
# actually exists: it is now the identifier-token Jaccard against the BEST available
# neighbour — the top-1 exact-hash match when one exists, else the top-1 Stage-C
# candidate (the same neighbour the decision-layer boilerplate guard inspects). On
# cold/migrated projects exact-hash matches barely exist, so under v4 nearly every
# item scored 0.0 here while ``identifiers_present=1`` — the exact boilerplate-trap
# signature the v4 GBM learned to distrust, discounting excellent textual evidence.
# A new ``ident_jaccard_source`` column (hash=1.0 / stage-C=0.5 / none=0.0) preserves
# provenance so the model can still weight the Jaccard by the strength of the neighbour
# it was measured against; a real trap (hash present, disjoint identifiers) still
# encodes jaccard=0.0 + identifiers_present=1.
FEATURE_SCHEMA_VER = 5

# spec 04 §4.2: the extractor's categorical outputs enter the GBM as ordinal
# columns. The sentinel ``unknown`` (= 0) is the on-miss / LLM-off value, so a
# build with the sidecar off is trained and served with these columns present and
# constant — identical to an install where Ollama is never reachable. The enum
# orders mirror the extractor schema (spec 04 §4.2); the ordinal is a stable
# stand-in for LightGBM (tree splits are order-tolerant), not a magnitude.
LLM_UNKNOWN = "unknown"
FAILING_LAYER_ORDINAL: dict[str, int] = {
    LLM_UNKNOWN: 0,
    "test_code": 1,
    "app_code": 2,
    "infrastructure": 3,
    "environment": 4,
}
ERROR_CLASS_ORDINAL: dict[str, int] = {
    LLM_UNKNOWN: 0,
    "assertion": 1,
    "timeout": 2,
    "connection": 3,
    "http_4xx": 4,
    "http_5xx": 5,
    "null_reference": 6,
    "not_found": 7,
    "permission": 8,
    "data_format": 9,
    "resource_exhausted": 10,
    "config": 11,
    "concurrency": 12,
    "other": 13,
}

# Base issue-type groups the model predicts; ``ti`` is the abstain outcome.
BASE_LABELS = ("pb", "ab", "si", "nd")
_LN2 = math.log(2.0)
_LN4 = math.log(4.0)
# Default per-day recency decay factor (ANALYZER_TIME_DECAY). Equivalent to the
# 90-day half-life ``exp(-ln2·d/90)``; the config default equals this exactly so
# wiring the knob leaves the feature vector byte-identical by default.
TIME_DECAY_PER_DAY = 2.0 ** (-1.0 / 90.0)
# si_prior is capped at 0.9 (spec §5 / feature #32 range [0,0.9]).
SI_PRIOR_MAX = 0.9


# --------------------------------------------------------------------------- #
# Identifier-token Jaccard (shared with the Stage-A discriminant gate)
# --------------------------------------------------------------------------- #
# The single source of truth for the message-similarity tokenizer used by BOTH the
# Stage-A inherit gate (decision.py) and the ``identifier_jaccard_top1`` feature, so
# the model learns exactly the signal the gate measured (2026-07-18 errata). Lives
# here (features imports nothing from decision) to avoid an import cycle.
#
# Identifier-like token: carries a dotted path (``com.hawkins.shop.auth.Session``),
# a scope-resolution ``::`` (C++/Rust), or a camelCase hump (``getDiscount``). These
# are the *discriminating* tokens of an exception message; boilerplate words
# (``cannot``, ``invoke``, ``because``, ``null``) are not.
_IDENTIFIER_TOKEN_RE = re.compile(r"\.|::|[a-z][A-Z]")


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity; two empty sets → 1.0, exactly one empty → 0.0."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def identifier_tokens(tokens: frozenset[str]) -> frozenset[str]:
    """The identifier-bearing subset of a masked-message token set (may be empty)."""
    return frozenset(t for t in tokens if _IDENTIFIER_TOKEN_RE.search(t))


def identifier_jaccard(query: frozenset[str], match: frozenset[str]) -> float:
    """Message similarity over identifier tokens when either side carries any (so
    shared NPE/assertion boilerplate can no longer inflate the score for two
    genuinely different app areas); both-sides-empty identifier sets fall back to the
    all-token Jaccard so boilerplate-only messages behave exactly as before."""
    qi, mi = identifier_tokens(query), identifier_tokens(match)
    if qi or mi:
        return jaccard(qi, mi)
    return jaccard(query, match)


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
    # spec 04 §4.2 optional LLM-extractor columns (sentinel 0 = unknown / LLM-off).
    FeatureDef("llm_failing_layer", 0.0),
    FeatureDef("llm_error_class", 0.0),
    # 2026-07-18 errata: discriminant-agreement columns. error_hash is computed over
    # Drain3-masked templates + normalized frames, so it collapses HTTP 500 vs 503
    # and same-exception failures from different app areas — the retrieval features
    # are all masked-similarity-based and cannot see the un-masked disagreement the
    # Stage-A gate acts on. These four carry that evidence to the GBM so it learns to
    # distrust near-miss hash traps. All default to 0.0 = "nothing to compare".
    FeatureDef("status_codes_present", 0.0),  # query has any un-masked status code
    FeatureDef("status_codes_match_top1", 0.0),  # 1.0 exact set match w/ top-1 evidence
    FeatureDef("identifier_jaccard_top1", 0.0),  # identifier-token Jaccard vs best neighbour
    FeatureDef("hash_gate_blocked", 0.0),  # 1.0 = exact hash existed, gate rejected it
    # v4 (2026-07-18b): companion "present" indicator for identifier_jaccard_top1, so
    # the model separates "no identifiers to compare" (0.0) from "identifiers disagree"
    # (also 0.0) — the same disambiguation status_codes_present gives the status column.
    FeatureDef("identifiers_present", 0.0),  # query message carries any identifier token
    # v5 (2026-07-20): provenance of the identifier_jaccard_top1 neighbour — 1.0 when it
    # was the top-1 exact-hash match, 0.5 when the top-1 Stage-C candidate, 0.0 when no
    # neighbour existed at all. Lets the retrained GBM weight the Jaccard by the strength
    # of the evidence it was measured against without losing the trap signal.
    FeatureDef("ident_jaccard_source", 0.0),
)

assert len(FEATURES) == 47, (
    "spec 03 §6.4 (39) + spec 04 §4.2 (2 LLM) + 2026-07-18 errata (4 discriminant) "
    "+ 2026-07-18b (1 identifiers_present) + 2026-07-20 v5 (1 ident_jaccard_source)"
)

# name → registered default, for the forward/backward-compat vector assembly rule.
FEATURE_DEFAULTS: dict[str, float] = {f.name: f.default for f in FEATURES}

# Candidate.label_source vocabulary → label-source weight (spec §6.4 src_w).
# rp=rp_defect_update (human confirm) 1.0; human=human_ui accept 0.9;
# ai_suggested (unreviewed, conservative) 0.3; seed prior 0.6; unknown 0.3.
_SRC_WEIGHT = {"rp": 1.0, "human": 0.9, "ai_suggested": 0.3, "seed": 0.6}
_SRC_WEIGHT_DEFAULT = 0.3


def src_weight(label_source: str | None) -> float:
    """Label-source weight ``src_w(e)`` for a candidate (spec §6.4)."""
    return _SRC_WEIGHT.get(label_source or "", _SRC_WEIGHT_DEFAULT)


def decay(days: float, *, per_day: float = TIME_DECAY_PER_DAY) -> float:
    """Time-decay of a candidate's recency weight (spec §6.4).

    ``per_day`` is the per-day retention factor (ANALYZER_TIME_DECAY). At the
    default it is the 90-day half-life ``exp(-ln2·d/90)`` — computed via the exact
    legacy expression so the default path is bit-identical; a custom factor uses
    ``per_day ** days``.
    """
    if days <= 0:
        return 1.0
    if per_day == TIME_DECAY_PER_DAY:
        return math.exp(-_LN2 * days / 90.0)
    return per_day**days


def base_group(issue_type: str | None) -> str | None:
    """Base issue-type group of an RP locator, or ``None`` if not a GBM class.

    The single source of truth for locator → base group (spec §6.5: "custom
    subtypes map to their base group"). RP locators — standard (``pb001``) and
    **custom** (``pb_myCustom``, ``PB_Regression``) — carry the two-letter group
    prefix, so the base is the leading (case-folded) alpha pair. ``ti`` is the
    abstain outcome and everything unrecognised returns ``None`` (dropped).
    """
    if not issue_type:
        return None
    prefix = "".join(c for c in issue_type[:2] if c.isalpha()).lower()
    return prefix if prefix in BASE_LABELS else None


def _base(issue_type: str | None) -> str | None:
    """Internal alias kept for readability at call sites; see :func:`base_group`."""
    return base_group(issue_type)


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
    # spec 04 §4.2 LLM-extractor categoricals; ``unknown`` on miss / LLM-off.
    llm_failing_layer: str = LLM_UNKNOWN
    llm_error_class: str = LLM_UNKNOWN
    # Discriminant-agreement evidence (2026-07-18 errata). The query's un-masked
    # discriminants and those of its top-1 exact-hash neighbour (the near-miss trap);
    # ``has_hash_top1`` says whether such a neighbour exists to compare against, and
    # ``hash_gate_blocked`` whether the Stage-A gate rejected every exact-hash match.
    # decision.decide() folds these in from the DecisionInputs — see _with_candidates.
    query_status_codes: tuple[str, ...] = ()
    query_msg_tokens: frozenset[str] = frozenset()
    top1_status_codes: tuple[str, ...] = ()
    top1_msg_tokens: frozenset[str] = frozenset()
    has_hash_top1: bool = False
    hash_gate_blocked: bool = False
    # Per-day recency decay factor (ANALYZER_TIME_DECAY); default = 90-day half-life.
    time_decay_per_day: float = TIME_DECAY_PER_DAY


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
        values["recency_top1"] = _clamp01(decay(top1_age, per_day=ctx.time_decay_per_day))
        values["src_weight_top1"] = src_weight(top1.label_source)

        # Weighted history mass per base label: Σ cos·decay·src_w, ÷ Σ all.
        mass: dict[str, float] = {b: 0.0 for b in BASE_LABELS}
        total_mass = 0.0
        for i, c in enumerate(cands):
            age = ages[i] if i < len(ages) else 0.0
            w = max(0.0, _cos(c)) * decay(age, per_day=ctx.time_decay_per_day) * src_weight(
                c.label_source
            )
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

    # spec 04 §4.2: ordinal-encode the LLM-extractor categoricals (sentinel 0 when
    # unknown / LLM-off). Not clamped to [0,1] — the ordinal is the category id.
    values["llm_failing_layer"] = float(FAILING_LAYER_ORDINAL.get(ctx.llm_failing_layer, 0))
    values["llm_error_class"] = float(ERROR_CLASS_ORDINAL.get(ctx.llm_error_class, 0))

    # discriminant-agreement signals (2026-07-18 errata; v4 re-encode 2026-07-18b).
    # Encoding principle: "nothing to compare" must NEVER read as "match" — a bare
    # Jaccard/set-equality returns 1.0 for two empty sets, which a tree happily treats
    # as strong agreement. The FEATURE therefore differs from the Stage-A GATE (which
    # keeps its both-empty fallbacks, §6.1) — gate and feature semantics are distinct.
    #   status_codes_present   — 1.0 iff the query carries any un-masked status code.
    #   status_codes_match_top1 — 1.0 iff the query HAS codes AND a top-1 exact-hash
    #       neighbour exists whose status-code set equals the query's; else 0.0. A
    #       both-empty pair is "nothing to compare" (0.0), never a match. present=1 +
    #       0 here is a genuine near-miss mismatch; has_hash_top1 says a neighbour exists.
    #   identifiers_present    — 1.0 iff the query message carries any identifier token.
    #   identifier_jaccard_top1 — (v5) identifier-token Jaccard vs the BEST AVAILABLE
    #       neighbour: the top-1 exact-hash match when one exists, else the top-1 Stage-C
    #       candidate (the same neighbour the decision-layer boilerplate guard inspects).
    #       0.0 when NEITHER side has identifier tokens ("nothing to compare", not the
    #       gate's all-token fallback) or when there is no neighbour at all. This makes
    #       cold/migrated projects (no exact-hash matches) describe the textual evidence
    #       that actually exists instead of wearing the boilerplate-trap signature.
    #   ident_jaccard_source   — (v5) provenance of that neighbour: 1.0 exact-hash,
    #       0.5 Stage-C candidate, 0.0 none.
    #   hash_gate_blocked      — 1.0 iff ≥1 exact error_hash match existed but the
    #       Stage-A discriminant gate rejected all of them (distrust-this-hash cue).
    query_ids = identifier_tokens(frozenset(ctx.query_msg_tokens))
    values["status_codes_present"] = 1.0 if ctx.query_status_codes else 0.0
    values["identifiers_present"] = 1.0 if query_ids else 0.0
    # status_codes_match_top1 stays defined against the top-1 exact-hash neighbour only —
    # its question ("does the near-miss hash trap share the query's un-masked code?") is
    # specifically about an exact-hash neighbour.
    if ctx.has_hash_top1:
        values["status_codes_match_top1"] = (
            1.0
            if ctx.query_status_codes
            and set(ctx.query_status_codes) == set(ctx.top1_status_codes)
            else 0.0
        )
    # identifier_jaccard_top1 (v5): the best available neighbour — exact-hash top-1 when
    # present, else the Stage-C top-1 (aligned with decision._boilerplate_only_top1,
    # which tokenises stage_c[0].msg_text the same way).
    neighbour_tokens: frozenset[str] | None = None
    if ctx.has_hash_top1:
        neighbour_tokens = frozenset(ctx.top1_msg_tokens)
        values["ident_jaccard_source"] = 1.0
    elif cands:
        neighbour_tokens = frozenset(cands[0].msg_text.split())
        values["ident_jaccard_source"] = 0.5
    if neighbour_tokens is not None:
        neighbour_ids = identifier_tokens(neighbour_tokens)
        values["identifier_jaccard_top1"] = (
            _clamp01(jaccard(query_ids, neighbour_ids))
            if (query_ids or neighbour_ids)
            else 0.0
        )
    values["hash_gate_blocked"] = 1.0 if ctx.hash_gate_blocked else 0.0

    return values


def to_vector(values: dict[str, float]) -> list[float]:
    """Order a name→value mapping into the canonical feature vector, filling any
    missing name with its registered default (forward-compat: an old snapshot lacking
    the newest columns trains with those columns at their defaults, never dropped)."""
    return [float(values.get(f.name, f.default)) for f in FEATURES]


def to_vector_for(values: dict[str, float], names: Sequence[str]) -> list[float]:
    """Assemble a vector for an *explicit* ordered feature-name list (a model's stored
    ``feature_names``), filling any name absent from the snapshot with its registered
    default (0.0 if the name is not in the current registry).

    This is the general forward/backward-compat serving rule: a model trained on
    feature list L is always fed a vector assembled per L — so columns a stale model
    never saw are dropped, and columns missing from an older snapshot are back-filled
    with defaults — independent of whatever the ambient FEATURES registry now holds.
    """
    return [float(values.get(n, FEATURE_DEFAULTS.get(n, 0.0))) for n in names]


def feature_names() -> list[str]:
    """The ordered feature names (for ``modelFeatureNames`` on suggest replies)."""
    return [f.name for f in FEATURES]
