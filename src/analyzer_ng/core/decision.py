"""Matching stages + rule-based cold decision + policy bands (spec 03 §6.1-§6.6).

The GBM does not exist yet (T3.1); spec §6.5 makes the **rule-based cold
fallback** the decision function until it does:

    Stage A (exact error_hash inherit) → KB short-circuit (confirmed, pure) →
    seed-mode prior label (prior_confidence ≥ 0.7) → abstain (ti).

Every path still extracts and returns the full 39-feature snapshot (§6.4) so the
future model trains on serving-identical vectors. The policy bands (§6.6) turn a
``(label, confidence)`` into an auto-label / suggest / abstain action; the route
layer renders that into the legacy wire shapes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from analyzer_ng.core.features import (
    FEATURE_SCHEMA_VER,
    FeatureContext,
    KBMatch,
    SeedSignal,
    extract_features,
)
from analyzer_ng.db.repositories.models import Candidate

# spec §6.6 decision-policy thresholds.
TAU_AUTO = 0.75
TAU_SUGGEST = 0.45

# spec §6.1 Stage-A constants.
STAGE_A_CONFIDENCE = 0.95
STAGE_A_MAX_AGE_DAYS = 180

# spec §6.2 KB short-circuit constants.
KB_STRONG_PURITY = 0.95
KB_STRONG_SUPPORT = 10
KB_STRONG_SCORE = 0.85
KB_CONFIDENCE_CAP = 0.93

# spec §6.5 cold fallback: seed prior threshold.
SEED_PRIOR_MIN_CONF = 0.7

# spec §6.2 score_mode weights.
KB_W_COS = 0.6
KB_W_JACCARD = 0.25
KB_W_FP = 0.15

METHOD_HASH = "hash"
METHOD_KB = "kb"
METHOD_GBM = "gbm"
METHOD_RULE_COLD = "rule_cold"

# Decision actions after applying the policy bands (spec §6.6).
ACTION_AUTO = "auto"  # auto-label on analyze / top-1 on suggest
ACTION_SUGGEST = "suggest"  # abstain on analyze (store) / top-3 on suggest
ACTION_ABSTAIN = "abstain"  # ti; suggest returns []


# RP default sub-type locators for each base group (legacy standard sub-types).
# A base group with no concrete historical locator flows as its default locator.
DEFAULT_LOCATOR = {"pb": "pb001", "ab": "ab001", "si": "si001", "nd": "nd001", "ti": "ti001"}

# §6.1 single-match inherit requires a human label with confidence ≥ this.
STAGE_A_HUMAN_MIN_CONF = 0.9


def default_locator(base: str) -> str:
    """The RP default sub-type locator for a base issue-type group (spec §6.6)."""
    return DEFAULT_LOCATOR.get(base, base)


@dataclass(frozen=True)
class HashMatch:
    """A Stage-A exact-error_hash match with the evidence the guards need."""

    item_id: int
    issue_type: str
    issue_type_group: str  # 'pb'|'ab'|'si'|'nd'|'ti'
    label_source: str | None  # 'rp'|'human'|'ai_suggested'|None
    label_ts: datetime | None
    confidence: float = 0.0  # label-event confidence (derived from source, §6.1 guard)
    is_auto_analyzed: bool = False


@dataclass
class DecisionResult:
    """The decision for one item + its feature snapshot and policy action."""

    label: str  # base group 'pb'|'ab'|'si'|'nd' or 'ti' (abstain)
    issue_type: str  # locator to apply/suggest ('pb001', …) or 'ti'
    confidence: float
    method: str
    action: str
    abstain_reason: str | None
    relevant_item_id: int | None
    matched_mode_id: int | None
    features: dict[str, float]
    feature_schema_ver: int = FEATURE_SCHEMA_VER
    probs: dict[str, float] = field(default_factory=dict)
    # Scoped/boosted Stage-C candidates carried for the suggest route (§6.6).
    stage_c: list[Candidate] = field(default_factory=list)


def _base(issue_type: str | None) -> str:
    if not issue_type:
        return ""
    return "".join(c for c in issue_type[:2] if c.isalpha())


def _age_days(ts: datetime | None, now: datetime) -> float:
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


# --------------------------------------------------------------------------- #
# Stage A — exact error_hash inherit (spec §6.1)
# --------------------------------------------------------------------------- #
def stage_a_inherit(
    exception_fp: int, matches: Sequence[HashMatch], *, now: datetime | None = None
) -> HashMatch | None:
    """Return the match to inherit from, or None when the guards do not all pass."""
    if exception_fp == 0 or not matches:
        return None
    now = now or datetime.now(UTC)
    newest = max(matches, key=lambda m: (m.label_ts or datetime.min.replace(tzinfo=UTC)))

    # Guard: newest match age ≤ 180 days.
    if _age_days(newest.label_ts, now) > STAGE_A_MAX_AGE_DAYS:
        return None

    labels = {m.issue_type for m in matches}
    unanimous = len(labels) == 1
    human = newest.label_source in ("rp", "human")
    # ≥2 unanimous items, OR a single human-sourced label with confidence ≥ 0.9 (§6.1).
    single_human = (
        len(matches) == 1 and human and newest.confidence >= STAGE_A_HUMAN_MIN_CONF
    )
    if not ((len(matches) >= 2 and unanimous) or single_human):
        return None

    base = _base(newest.issue_type)
    # Guard: never inherit ti, nor auto-suggested nd.
    if base == "ti":
        return None
    if base == "nd" and newest.label_source == "ai_suggested":
        return None
    return newest


# --------------------------------------------------------------------------- #
# Stage B — KB mode scoring (spec §6.2)
# --------------------------------------------------------------------------- #
def score_kb_candidate(cand: Candidate) -> KBMatch:
    """Score a KB-mode Candidate (from ``KBStore.match_modes``) per spec §6.2.

    ``fp_overlap`` uses the exact-fingerprint hit (1.0) else 0.0; the 0.5
    "root class matches" tier is not cheaply available from the mode row and is
    conservatively treated as no overlap.
    """
    cos = cand.cosine if cand.cosine is not None else 0.0
    fp_overlap = 1.0 if cand.same_exception_fp else 0.0
    score = KB_W_COS * cos + KB_W_JACCARD * cand.jaccard_templates + KB_W_FP * fp_overlap
    return KBMatch(
        mode_id=cand.mode_id or 0,
        score_mode=score,
        purity=cand.mode_purity if cand.mode_purity is not None else 0.0,
        support=cand.mode_support or 0,
        same_exception_fp=cand.same_exception_fp,
        status=cand.mode_status or "candidate",
    )


def best_kb_match(candidates: Sequence[Candidate]) -> tuple[KBMatch, Candidate] | None:
    """Highest-scoring KB match (deterministic tie-break by mode_id)."""
    scored = [(score_kb_candidate(c), c) for c in candidates if c.mode_id is not None]
    if not scored:
        return None
    return max(scored, key=lambda pair: (pair[0].score_mode, -(pair[0].mode_id)))


def kb_short_circuit(kb: KBMatch) -> bool:
    """A confirmed, pure, well-supported, high-scoring mode decides like Stage A."""
    return (
        kb.status == "confirmed"
        and kb.purity >= KB_STRONG_PURITY
        and kb.support >= KB_STRONG_SUPPORT
        and kb.score_mode >= KB_STRONG_SCORE
    )


# --------------------------------------------------------------------------- #
# Decision assembly
# --------------------------------------------------------------------------- #
def _band_action(confidence: float, short_circuit: bool) -> str:
    if short_circuit or confidence >= TAU_AUTO:
        return ACTION_AUTO
    if confidence >= TAU_SUGGEST:
        return ACTION_SUGGEST
    return ACTION_ABSTAIN


@dataclass
class DecisionInputs:
    """Everything the cold decision function needs for one item (no DB access)."""

    exception_fp: int
    hash_matches: Sequence[HashMatch] = ()
    kb_candidates: Sequence[Candidate] = ()  # from KBStore.match_modes
    seed: SeedSignal | None = None
    stage_c: Sequence[Candidate] = ()  # top-20, post-boost order
    stage_c_ages_days: Sequence[float] = ()
    feature_ctx: FeatureContext | None = None  # non-candidate context (stats/group/flags)


def decide(inputs: DecisionInputs, *, now: datetime | None = None) -> DecisionResult:
    """Rule-based cold decision + feature snapshot + policy band (spec §6.1-§6.6)."""
    now = now or datetime.now(UTC)
    kb_pair = best_kb_match(inputs.kb_candidates)
    kb_best = kb_pair[0] if kb_pair else None

    # Feature snapshot is always produced (training reads only these — §6.4).
    ctx = inputs.feature_ctx or FeatureContext()
    ctx = _with_candidates(ctx, inputs, kb_best)
    features = extract_features(ctx)

    def result(
        label: str,
        issue_type: str,
        confidence: float,
        method: str,
        *,
        short_circuit: bool = False,
        relevant_item_id: int | None = None,
        matched_mode_id: int | None = None,
        abstain_reason: str | None = None,
    ) -> DecisionResult:
        action = ACTION_ABSTAIN if label == "ti" else _band_action(confidence, short_circuit)
        return DecisionResult(
            label=label,
            issue_type=issue_type,
            confidence=confidence,
            method=method,
            action=action,
            abstain_reason=abstain_reason if action == ACTION_ABSTAIN else None,
            relevant_item_id=relevant_item_id,
            matched_mode_id=matched_mode_id,
            features=features,
            probs={label: confidence} if label != "ti" else {},
            stage_c=list(inputs.stage_c),
        )

    # Stage A — exact error_hash inherit.
    inherit = stage_a_inherit(inputs.exception_fp, inputs.hash_matches, now=now)
    if inherit is not None:
        return result(
            _base(inherit.issue_type),
            inherit.issue_type,
            STAGE_A_CONFIDENCE,
            METHOD_HASH,
            short_circuit=True,
            relevant_item_id=inherit.item_id,
        )

    # Stage B — KB short-circuit (confirmed + pure + supported).
    if kb_pair is not None and kb_short_circuit(kb_best):  # type: ignore[arg-type]
        kb, cand = kb_pair
        confidence = min(KB_CONFIDENCE_CAP, kb.purity * kb.score_mode)
        return result(
            _base(cand.issue_type),
            cand.issue_type or _base(cand.issue_type),
            confidence,
            METHOD_KB,
            short_circuit=True,
            matched_mode_id=kb.mode_id,
        )

    # Cold fallback — seed prior when confident enough.
    if inputs.seed is not None and inputs.seed.confidence >= SEED_PRIOR_MIN_CONF:
        base = _base(inputs.seed.label)
        return result(
            base,
            default_locator(base),  # seed carries a base group → RP default locator
            inputs.seed.confidence,
            METHOD_RULE_COLD,
        )

    # Otherwise abstain (ti). Suggest may still surface Stage-C candidates.
    return result("ti", "ti", 0.0, METHOD_RULE_COLD, abstain_reason="no_confident_rule")


def _with_candidates(
    ctx: FeatureContext, inputs: DecisionInputs, kb_best: KBMatch | None
) -> FeatureContext:
    """Fold the Stage-C candidates / KB best / seed into the feature context."""
    from dataclasses import replace

    return replace(
        ctx,
        candidates=list(inputs.stage_c),
        candidate_ages_days=list(inputs.stage_c_ages_days),
        kb_best=kb_best,
        seed=inputs.seed,
    )
