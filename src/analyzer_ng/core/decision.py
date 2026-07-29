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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from analyzer_ng.core.features import (
    BASE_LABELS,
    FEATURE_SCHEMA_VER,
    FeatureContext,
    KBMatch,
    SeedSignal,
    base_group,
    extract_features,
    identifier_jaccard,
)
from analyzer_ng.db.repositories.models import Candidate

# spec §6.6 decision-policy thresholds.
TAU_AUTO = 0.75
TAU_SUGGEST = 0.45

# spec §6.1 Stage-A constants.
STAGE_A_CONFIDENCE = 0.95
STAGE_A_MAX_AGE_DAYS = 180
# Discriminant gate (2026-07-18 errata, extends §6.1): a hash match must share the
# query's masked salient-message terms above this Jaccard to be inherit-eligible.
STAGE_A_MSG_JACCARD = 0.5

# spec §6.2 KB short-circuit constants.
KB_STRONG_PURITY = 0.95
KB_STRONG_SUPPORT = 10
KB_STRONG_SCORE = 0.85
KB_CONFIDENCE_CAP = 0.93
# spec §6.2: score_mode ≥ 0.70 is a *candidate* KB match (features only) — strong
# enough to record the item as a member of that mode so the mode can learn (§6.7/§9).
KB_CANDIDATE_SCORE = 0.70

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
    # Discriminants for the inherit gate (2026-07-18 errata, extends §6.1). Drain3
    # masking collapses HTTP codes and app-area detail into a shared error_hash, so
    # the raw hash is not sufficient to inherit; these carry the un-masked evidence.
    exception_fp: int = 0
    status_codes: tuple[str, ...] = ()
    msg_tokens: frozenset[str] = frozenset()


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
    # Version of the shipped GBM that produced this decision (None for rule paths);
    # carried straight from the prediction so the suggestion stamp needs no re-fetch.
    model_version: str | None = None
    # Scoped/boosted Stage-C candidates carried for the suggest route (§6.6).
    stage_c: list[Candidate] = field(default_factory=list)
    # Label provenance of the chosen relevant item (None when the answer comes from
    # a KB mode or no item was matched) — surfaces "human-confirmed" vs
    # "auto-analyzed" in the suggest response's modelInfo.
    relevant_label_source: str | None = None
    relevant_is_auto_analyzed: bool = False
    # Isotonic-calibrated probability of the RAW ARGMAX group (§6.5), set ONLY by the
    # GBM path. ``confidence`` is not a substitute: the hash-floor promotion carries
    # the policy floor there, and the abstain path carries it under label ``ti``.
    # None means "no calibrated distribution behind this decision" — every rule /
    # short-circuit path and any hand-built result leaves it unset, and readers must
    # then say nothing rather than guess. See :func:`calibrated_label_prob`.
    calibrated_max_prob: float | None = None


def calibrated_label_prob(decision: DecisionResult, group: str) -> float | None:
    """The model's calibrated probability for ONE base group, or ``None`` if unknown.

    ``DecisionResult.probs`` holds the GBM's RAW softmax distribution, while the
    per-project isotonic fit (§6.5) is fitted on ``raw max-prob → P(argmax correct)``
    — so the argmax group is the only class whose calibrated value is measured. This
    maps the raw distribution onto that calibrated scale: the argmax group takes its
    calibrated value ``p*``, and the leftover calibrated mass ``1 - p*`` is split
    among the other groups in their raw ratios. The result stays a distribution and
    its argmax entry equals ``p*`` exactly, so a per-label number can never contradict
    the confidence the same decision reports.

    Returns ``None``, never a fallback number, when the answer is not known: the
    non-GBM paths (Stage-A hash, KB short circuit, cold rule) store a placeholder
    ``{label: confidence}`` in ``probs`` that says nothing about the other groups,
    and a legacy or hand-built result carries no calibrated max-prob at all.
    """
    if decision.calibrated_max_prob is None:
        return None
    probs = decision.probs
    if not probs or group not in probs:
        return None
    raw_max = max(probs.values())
    if raw_max <= 0.0:
        return None
    p_star = max(0.0, min(1.0, decision.calibrated_max_prob))
    if group == max(probs, key=lambda g: probs[g]):
        return p_star
    raw_rest = 1.0 - raw_max
    if raw_rest <= 0.0:
        return 0.0
    return max(0.0, min(1.0, probs[group] * (1.0 - p_star) / raw_rest))


def _base(issue_type: str | None) -> str:
    """Base issue-type group, case-folded via the single source of truth
    (:func:`analyzer_ng.core.features.base_group`) so a custom uppercase locator
    (``PB_Regression``) unifies with the GBM's lowercase base labels. A non-GBM /
    unrecognized locator (including ``ti``) folds to the empty string here."""
    return base_group(issue_type) or ""


def _age_days(ts: datetime | None, now: datetime) -> float:
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


# --------------------------------------------------------------------------- #
# Stage A — exact error_hash inherit (spec §6.1)
# --------------------------------------------------------------------------- #
# The identifier-token Jaccard tokenizer lives in core.features (the single feature
# registry) so the ``identifier_jaccard_top1`` GBM feature and this gate measure the
# exact same thing. ``_msg_gate_jaccard`` is retained as a local alias for readability
# at the gate call sites (2026-07-18 errata).
_msg_gate_jaccard = identifier_jaccard


def _discriminant_filter(
    matches: Sequence[HashMatch],
    query_status_codes: tuple[str, ...],
    query_msg_tokens: frozenset[str],
) -> list[HashMatch]:
    """Keep only exact-hash matches that agree with the query on the un-masked
    discriminants (status codes + salient message terms). Shared by the inherit gate
    and the ``hash_gate_blocked`` feature so both act on one definition of agreement."""
    return [
        m
        for m in matches
        if set(m.status_codes) == set(query_status_codes)
        and _msg_gate_jaccard(query_msg_tokens, m.msg_tokens) >= STAGE_A_MSG_JACCARD
    ]


def discriminant_gate_blocked(
    exception_fp: int,
    matches: Sequence[HashMatch],
    *,
    query_status_codes: tuple[str, ...] = (),
    query_msg_tokens: frozenset[str] = frozenset(),
) -> bool:
    """True when ≥1 exact error_hash match existed but the discriminant gate rejected
    every one of them — the near-miss trap (identical masked hash, disagreeing
    un-masked evidence). Feeds the ``hash_gate_blocked`` feature so the GBM learns to
    distrust exactly the hash matches the Stage-A rule refused to inherit."""
    if exception_fp == 0 or not matches:
        return False
    return not _discriminant_filter(list(matches), query_status_codes, query_msg_tokens)


def stage_a_inherit(
    exception_fp: int,
    matches: Sequence[HashMatch],
    *,
    query_status_codes: tuple[str, ...] = (),
    query_msg_tokens: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> HashMatch | None:
    """Return the match to inherit from, or None when the guards do not all pass.

    Guards (spec §6.1 + discriminant gate, 2026-07-18 errata): the un-masked
    discriminant gate runs FIRST — ``error_hash`` is computed over Drain3-masked
    templates + normalized frames, so it collapses HTTP 500 vs 503 (the code is
    masked away) and same-exception failures from different app areas that share no
    in-app frames. The gate filters ``matches`` down to those that agree with the
    query on the un-masked evidence before the unanimity/single-human logic runs:

    * status gate: ``set(m.status_codes) == set(query_status_codes)`` — both empty
      passes; any asymmetry fails (abstain-by-default). The status extractor now
      captures assertion/response idioms (``Actual: 503``, ``-> 500``, ``HTTP/1.x
      <code>``), so an upstream-503 (si) no longer shares an empty status set with a
      server-500 (pb) of the same test — the un-masked code survives Drain masking.
    * message gate: ``_msg_gate_jaccard(query_msg_tokens, m.msg_tokens) >=
      STAGE_A_MSG_JACCARD``. The similarity is computed over *identifier-bearing*
      tokens (dotted paths, ``::``, quoted member refs, camelCase) whenever either
      side has any, so shared NPE/assertion boilerplate (``cannot invoke ... because
      ... null``) can no longer inflate the score above the threshold for two
      genuinely different app areas (``Session.userId`` vs ``Region.rate``).
      Both-sides-empty identifier sets fall back to the all-token Jaccard (Jaccard
      of two empty sets = 1.0, one-empty-one-not = 0.0).

    Running unanimity/count on the FILTERED list means a crowd labeled off the
    wrong discriminant (e.g. all HTTP 500) can no longer out-vote the query by
    count. Only this rule-based inherit is gated; GBM feature computation still
    sees the unfiltered hash matches.
    """
    if exception_fp == 0 or not matches:
        return None
    now = now or datetime.now(UTC)

    # Discriminant gate (2026-07-18 errata) — keep only matches that agree with the
    # query on the un-masked status codes and salient message terms.
    matches = _discriminant_filter(list(matches), query_status_codes, query_msg_tokens)
    if not matches:
        return None

    newest = max(matches, key=lambda m: m.label_ts or datetime.min.replace(tzinfo=UTC))

    # Guard: newest match age ≤ 180 days.
    if _age_days(newest.label_ts, now) > STAGE_A_MAX_AGE_DAYS:
        return None

    labels = {m.issue_type for m in matches}
    unanimous = len(labels) == 1
    human = newest.label_source in ("rp", "human")
    # ≥2 unanimous items, OR a single human-sourced label with confidence ≥ 0.9 (§6.1).
    single_human = len(matches) == 1 and human and newest.confidence >= STAGE_A_HUMAN_MIN_CONF
    if not ((len(matches) >= 2 and unanimous) or single_human):
        return None

    base = _base(newest.issue_type)
    # Guard: never inherit ti or an unrecognized/non-GBM locator (both fold to ""),
    # nor an auto-suggested nd.
    if base == "":
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
def _band_action(confidence: float, short_circuit: bool, tau_auto: float = TAU_AUTO) -> str:
    if short_circuit or confidence >= tau_auto:
        return ACTION_AUTO
    if confidence >= TAU_SUGGEST:
        return ACTION_SUGGEST
    return ACTION_ABSTAIN


class GbmDecision(Protocol):
    """Structural view of a served GBM prediction (spec §6.5/§6.6).

    Kept structural so :mod:`decision` stays pure — it never imports the serving
    layer; the engine passes a ``gbm_predict`` callable that returns one of these
    (a :class:`analyzer_ng.ml.serving.GbmPrediction`) or ``None`` when cold. The
    members are read-only properties so a frozen dataclass satisfies the protocol.
    """

    @property
    def label(self) -> str: ...
    @property
    def max_prob(self) -> float: ...
    @property
    def probs(self) -> dict[str, float]: ...
    @property
    def model_version(self) -> str: ...


@dataclass
class DecisionInputs:
    """Everything the decision function needs for one item (no DB access).

    ``gbm_predict`` is the serving hook (spec §6.5): given the ordered feature
    vector it returns the calibrated GBM decision, or ``None`` when no model is
    shipped (cold install) — in which case the rule-based fallback decides.
    """

    exception_fp: int
    hash_matches: Sequence[HashMatch] = ()
    # Query-side discriminants for the Stage-A inherit gate (2026-07-18 errata).
    query_status_codes: tuple[str, ...] = ()
    query_msg_tokens: frozenset[str] = frozenset()
    kb_candidates: Sequence[Candidate] = ()  # from KBStore.match_modes
    seed: SeedSignal | None = None
    stage_c: Sequence[Candidate] = ()  # top-20, post-boost order
    stage_c_ages_days: Sequence[float] = ()
    feature_ctx: FeatureContext | None = None  # non-candidate context (stats/group/flags)
    # Serving hook: receives the name→value feature snapshot (NOT a pre-ordered
    # vector) so the serving layer assembles the vector from the *model's own* stored
    # feature list (schema-robust; see analyzer_ng.ml.serving.GbmPredictor.predict).
    gbm_predict: Callable[[dict[str, float]], GbmDecision | None] | None = None


def decide(
    inputs: DecisionInputs, *, now: datetime | None = None, tau_auto: float = TAU_AUTO
) -> DecisionResult:
    """Rule-based cold decision + feature snapshot + policy band (spec §6.1-§6.6).

    ``tau_auto`` is the auto-apply threshold (ANALYZER_AUTO_MIN_PROB); it defaults to
    the spec constant :data:`TAU_AUTO` so an unwired caller is unaffected.
    """
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
        probs: dict[str, float] | None = None,
        model_version: str | None = None,
        relevant_label_source: str | None = None,
        relevant_is_auto_analyzed: bool = False,
        calibrated_max_prob: float | None = None,
    ) -> DecisionResult:
        action = (
            ACTION_ABSTAIN if label == "ti" else _band_action(confidence, short_circuit, tau_auto)
        )
        default_probs = {label: confidence} if label != "ti" else {}
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
            probs=probs if probs is not None else default_probs,
            model_version=model_version,
            stage_c=list(inputs.stage_c),
            relevant_label_source=relevant_label_source,
            relevant_is_auto_analyzed=relevant_is_auto_analyzed,
            calibrated_max_prob=calibrated_max_prob,
        )

    # Stage A — exact error_hash inherit (discriminant-gated, 2026-07-18 errata).
    inherit = stage_a_inherit(
        inputs.exception_fp,
        inputs.hash_matches,
        query_status_codes=inputs.query_status_codes,
        query_msg_tokens=inputs.query_msg_tokens,
        now=now,
    )
    if inherit is not None:
        return result(
            _base(inherit.issue_type),
            inherit.issue_type,
            STAGE_A_CONFIDENCE,
            METHOD_HASH,
            short_circuit=True,
            relevant_item_id=inherit.item_id,
            relevant_label_source=inherit.label_source,
            relevant_is_auto_analyzed=inherit.is_auto_analyzed,
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

    # GBM decision (spec §6.5/§6.6) — the shipped model decides once Stage A / KB
    # short-circuits have not fired. Absent a model (cold install) this hook is
    # None and control falls through to the rule-based cold fallback.
    if inputs.gbm_predict is not None:
        gbm = inputs.gbm_predict(features)
        if gbm is not None:
            return _gbm_result(
                gbm,
                inputs.stage_c,
                result,
                features=features,
                query_msg_tokens=frozenset(inputs.query_msg_tokens),
                tau_auto=tau_auto,
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


def _pick_relevant(stage_c: Sequence[Candidate], base: str) -> Candidate | None:
    """Best Stage-C candidate whose base group matches the GBM label (for relevantItem)."""
    for c in stage_c:
        if c.item_id is not None and _base(c.issue_type) == base:
            return c
    return None


def _human_confirmed_hash_floor(
    stage_c: Sequence[Candidate],
    features: dict[str, float],
    query_msg_tokens: frozenset[str],
) -> Candidate | None:
    """The human-confirmed exact-hash floor (tech-debt #3, Defect B).

    Stage A already inherits a human-confirmed exact-hash label at 0.95 when ALL its
    §6.1 guards pass. This floor covers the NEAR-MISS: Stage A legitimately declined
    (e.g. newest match age > 180 d, or a non-unanimous crowd, or a single human label
    below the 0.9 confidence guard) yet the top-1 retrieval neighbour is a
    human-confirmed exact-error_hash match with CLEAN un-masked discriminants — and a
    jittery GBM would then abstain (``p* < τ_suggest``), burying that strong evidence
    behind an empty reply. Return that neighbour to promote the decision to the suggest
    FLOOR (never auto); return ``None`` to leave the GBM's abstain intact.

    Fires only when EVERY guard holds — deliberately narrower than Stage A so real traps
    still abstain:

    * ``stage_c[0].same_error_hash`` — an exact error_hash match (so a boilerplate-cosine
      plateau with no hash, the NET-XUN-15 trap, can never reach this floor);
    * ``stage_c[0].label_source in {rp, human}`` — the label was human-confirmed;
    * ``hash_gate_blocked == 0`` — the Stage-A discriminant gate did NOT reject every
      exact-hash match (we never resurrect a hash the S16 gate distrusted);
    * status codes clean — NOT (query has un-masked status codes AND they disagree with
      the exact-hash top-1): a 503-vs-500 near-miss status trap is excluded;
    * identifier gate clean — ``_msg_gate_jaccard(query, top1) >= STAGE_A_MSG_JACCARD``,
      the SAME identifier-token gate Stage A applies, so a ``Session.userId`` vs
      ``Region.rate`` near-miss (disjoint identifiers) stays below threshold and does NOT
      promote. The S16 gate is reused, never weakened;
    * the neighbour's base group is a real GBM class (not ``ti``/unrecognized).
    """
    if not stage_c:
        return None
    top1 = stage_c[0]
    if not top1.same_error_hash or top1.label_source not in ("rp", "human"):
        return None
    if features.get("hash_gate_blocked", 0.0) != 0.0:
        return None
    # Status-code near-miss trap: query carries un-masked codes that disagree with the
    # exact-hash top-1 (status_codes_present=1 but status_codes_match_top1=0).
    if (
        features.get("status_codes_present", 0.0) == 1.0
        and features.get("status_codes_match_top1", 0.0) != 1.0
    ):
        return None
    # Identifier discriminant gate (S16, reused verbatim): disjoint identifier tokens
    # keep a same-hash different-app-area trap below the inherit threshold.
    top1_tokens = frozenset(top1.msg_text.split())
    if _msg_gate_jaccard(query_msg_tokens, top1_tokens) < STAGE_A_MSG_JACCARD:
        return None
    if _base(top1.issue_type) not in BASE_LABELS:
        return None
    return top1


def _boilerplate_only_top1(top1: Candidate, query_msg_tokens: frozenset[str]) -> bool:
    """True when the top-1 neighbour shares NO structural evidence with the query
    (2026-07-18 errata): no exact fingerprint, no exact error_hash, no template
    overlap, and identifier-token similarity below the Stage-A threshold — a
    boilerplate-cosine plateau (e.g. a novel NullReferenceException landing next to an
    unrelated NPE at cosine 0.93 with zero structural overlap). The identifier-token
    comparison reuses the Stage-A gate's :func:`identifier_jaccard` (both-empty
    identifier sets fall back to the all-token Jaccard exactly as the gate does)."""
    if top1.same_exception_fp or top1.same_error_hash or top1.jaccard_templates > 0:
        return False
    top1_tokens = frozenset(top1.msg_text.split())
    return _msg_gate_jaccard(query_msg_tokens, top1_tokens) < STAGE_A_MSG_JACCARD


def _gbm_result(
    gbm: GbmDecision,
    stage_c: Sequence[Candidate],
    result_fn: Callable[..., DecisionResult],
    *,
    features: dict[str, float],
    query_msg_tokens: frozenset[str] = frozenset(),
    tau_auto: float = TAU_AUTO,
) -> DecisionResult:
    """Turn a calibrated GBM prediction into a banded decision (spec §6.6).

    ``p* ≥ τ_auto`` → auto; ``τ_suggest ≤ p* < τ_auto`` → suggest; ``p* < τ_suggest``
    → abstain (``ti``). The concrete locator/relevantItem is taken from the best
    Stage-C candidate that shares the predicted base group, else the RP default
    locator. The full calibrated distribution is carried for audit/suggest ranking.

    Deterministic suggest-band guard (2026-07-18 errata): a *suggest* (not auto)
    prediction whose top-1 neighbour is boilerplate-only — no structural overlap of any
    kind — is demoted to abstain, since the Stage-A discriminant gate never sees the
    Stage-B/GBM path and a boilerplate-cosine plateau is not real support. The auto
    band (``p* ≥ τ_auto``) is untouched.

    Human-confirmed exact-hash floor (tech-debt #3, Defect B): the mirror of the demote
    guard. A pure-abstain (``p* < τ_suggest``) whose top-1 neighbour is a human-confirmed
    exact-error_hash match with clean un-masked discriminants is PROMOTED to the suggest
    floor (never auto), so a jittery GBM cannot bury strong human-confirmed evidence
    behind an empty reply. See :func:`_human_confirmed_hash_floor` for the guard set.
    """
    p = max(0.0, min(1.0, float(gbm.max_prob)))
    label = gbm.label if gbm.label in BASE_LABELS else "ti"
    probs = {b: float(gbm.probs.get(b, 0.0)) for b in BASE_LABELS}
    version = gbm.model_version or None
    if label == "ti" or p < TAU_SUGGEST:
        floor = _human_confirmed_hash_floor(stage_c, features, query_msg_tokens)
        if floor is not None:
            flabel = _base(floor.issue_type)
            locator = floor.issue_type or default_locator(flabel)
            return result_fn(
                flabel,
                locator,
                TAU_SUGGEST,
                METHOD_GBM,
                relevant_item_id=floor.item_id,
                probs=probs,
                model_version=version,
                relevant_label_source=floor.label_source,
                calibrated_max_prob=p,
            )
        return result_fn(
            "ti",
            "ti",
            p,
            METHOD_GBM,
            abstain_reason="gbm_below_suggest",
            probs=probs,
            model_version=version,
            calibrated_max_prob=p,
        )
    if p < tau_auto and stage_c and _boilerplate_only_top1(stage_c[0], query_msg_tokens):
        return result_fn(
            "ti",
            "ti",
            p,
            METHOD_GBM,
            abstain_reason="gbm_boilerplate_only_neighbor",
            probs=probs,
            model_version=version,
            calibrated_max_prob=p,
        )
    cand = _pick_relevant(stage_c, label)
    locator = cand.issue_type if cand is not None and cand.issue_type else default_locator(label)
    rel = cand.item_id if cand is not None else None
    return result_fn(
        label,
        locator,
        p,
        METHOD_GBM,
        relevant_item_id=rel,
        probs=probs,
        model_version=version,
        relevant_label_source=cand.label_source if cand is not None else None,
        calibrated_max_prob=p,
    )


def _with_candidates(
    ctx: FeatureContext, inputs: DecisionInputs, kb_best: KBMatch | None
) -> FeatureContext:
    """Fold the Stage-C candidates / KB best / seed and the discriminant-agreement
    evidence into the feature context (2026-07-18 errata). The top-1 exact-hash
    neighbour (``hash_matches[0]``) carries the un-masked status codes / message
    tokens the retrieval features cannot see; ``hash_gate_blocked`` records whether the
    Stage-A gate rejected every exact-hash match for this item."""
    from dataclasses import replace

    hm = list(inputs.hash_matches)
    top1 = hm[0] if hm else None
    return replace(
        ctx,
        candidates=list(inputs.stage_c),
        candidate_ages_days=list(inputs.stage_c_ages_days),
        kb_best=kb_best,
        seed=inputs.seed,
        query_status_codes=tuple(inputs.query_status_codes),
        query_msg_tokens=frozenset(inputs.query_msg_tokens),
        top1_status_codes=tuple(top1.status_codes) if top1 is not None else (),
        top1_msg_tokens=frozenset(top1.msg_tokens) if top1 is not None else frozenset(),
        has_hash_top1=top1 is not None,
        hash_gate_blocked=discriminant_gate_blocked(
            inputs.exception_fp,
            hm,
            query_status_codes=tuple(inputs.query_status_codes),
            query_msg_tokens=frozenset(inputs.query_msg_tokens),
        ),
    )
