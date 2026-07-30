"""Ship gate for a retrained model (spec 03 §10.2).

A freshly trained candidate replaces the active model **only** if it holds up on
the same frozen chronological eval slice (spec §10.1). Three conditions, all
required (spec §10.2):

* ``macro-F1 ≥ active − 0.005`` — no material accuracy regression;
* ``auto-band precision ≥ max(active, 0.9 · 0.95)`` — the auto-labeling safety
  metric never drops below the active model nor below the ``0.855`` absolute floor;
* ``abstain rate ≤ active + 0.05`` — the model may not buy accuracy by quietly
  abstaining on far more items.

When any condition fails the active model is kept, the candidate's metrics are
logged (WARN) and persisted as an inactive ``model_artifact`` row for audit
(``is_active=false``) — every activation, and every rejection, is an auditable row.

The gate is wired into T3.1's :class:`~analyzer_ng.ml.retrain.Retrainer` ``gate``
seam via :func:`make_ship_gate`: ``retrain → eval → ship/keep`` becomes the full
loop without the trigger machinery knowing anything about evaluation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

from analyzer_ng.core.features import FEATURE_SCHEMA_VER
from analyzer_ng.ml.artifacts import KIND_GBM, ArtifactSpec, ModelStore
from analyzer_ng.ml.calibration import IsotonicCalibrator
from analyzer_ng.ml.eval import (
    EvalReport,
    chronological_split,
    evaluate,
    score_events,
    usable_events,
)
from analyzer_ng.ml.trainer import GBM_MIN_EVENTS, GbmModel, TrainingError, train_gbm

logger = logging.getLogger(__name__)

# spec §10.2 exact gate thresholds.
GATE_MACRO_F1_SLACK = 0.005
AUTO_BAND_TARGET = 0.95
AUTO_BAND_FLOOR = 0.9 * AUTO_BAND_TARGET  # 0.855
GATE_ABSTAIN_SLACK = 0.05

# Minimum train-slice events before the gate fits its own calibrator for scoring;
# below this both models are scored on raw max-prob (identity), symmetrically.
GATE_CALIB_MIN = 50

# Calibration-health guardrail (tech-debt #1). The active model is scored on the eval
# slice with its *shipped* calibrator (not a fresh re-fit — see run_gate), so its ECE
# reflects production calibration. An active whose shipped calibrator is degenerate on
# the eval slice (ECE above this) must NOT masquerade as a protected baseline: a model
# that mis-calibrates every prediction (e.g. an accidental partial-data isotonic fit that
# maps raw 0.99→0.71) abstains/auto-labels wrongly in production even though its argmax
# accuracy (macro-F1, calibrator-independent) looks fine. When the active is degenerate
# and the candidate is meaningfully better calibrated, the marginal macro-F1 protection is
# relaxed so a clean candidate can replace it (the accidental degenerate ship that became
# an un-improvable baseline).
GATE_ECE_DEGENERATE = 0.15
# The candidate must be at least this much better calibrated (lower ECE) than a degenerate
# active before the relaxation kicks in — a candidate no better calibrated earns nothing.
GATE_ECE_IMPROVEMENT = 0.05
# Relaxed macro-F1 slack applied *only* when replacing a degenerate-calibrated active with
# a meaningfully better-calibrated candidate: the candidate may trade up to this much
# accuracy for the large calibration win, but a genuine accuracy collapse is still rejected.
GATE_MACRO_F1_SLACK_DEGRADED_ACTIVE = 0.05


@dataclass(frozen=True)
class GateResult:
    """The keep/replace verdict and the reports that produced it."""

    ship: bool
    reasons: list[str]  # failed condition names ('' when shipping)
    candidate: EvalReport | None = None
    active: EvalReport | None = None
    extra: dict = field(default_factory=dict)


def _active_calibration_degraded(candidate: EvalReport, active: EvalReport) -> bool:
    """True when the active's shipped calibrator is degenerate and the candidate is
    meaningfully better calibrated (tech-debt #1).

    ``active.ece`` here is measured with the active model's *shipped* calibrator (run_gate
    scores the active as-shipped), so a degenerate install-wide calibrator surfaces as a
    high ECE. When that is the case and the candidate is at least ``GATE_ECE_IMPROVEMENT``
    better calibrated, the active forfeits the tight macro-F1 protection — it should never
    have shipped, and it must not block a well-calibrated replacement.
    """
    return active.ece > GATE_ECE_DEGENERATE and candidate.ece <= active.ece - GATE_ECE_IMPROVEMENT


def passes_gate(candidate: EvalReport, active: EvalReport | None) -> GateResult:
    """Apply the three §10.2 conditions; ``active=None`` bootstraps (always ships).

    Auto-band precision comparison with empty-band (``None``) semantics: the target
    is ``max(active, 0.855)``, treating an active with no auto-band samples as the
    floor. A *candidate* with no auto-band samples makes no auto-labels, so there is
    nothing to guard on that axis — the condition is not failed (the macro-F1 and
    abstain conditions still bind, and a model that stopped auto-labeling shows up as
    a higher abstain rate there).

    Calibration guardrail (tech-debt #1): when the active model's *shipped* calibrator is
    degenerate on the eval slice and the candidate is meaningfully better calibrated, the
    macro-F1 slack is widened to :data:`GATE_MACRO_F1_SLACK_DEGRADED_ACTIVE` so a marginally
    higher (calibrator-independent) macro-F1 cannot let a mis-calibrated active block a
    clean candidate. The absolute auto-band safety floor (0.855) still binds, so a
    genuinely worse candidate is still rejected.
    """
    if active is None:
        return GateResult(ship=True, reasons=[], candidate=candidate, active=None)

    degraded = _active_calibration_degraded(candidate, active)
    f1_slack = GATE_MACRO_F1_SLACK_DEGRADED_ACTIVE if degraded else GATE_MACRO_F1_SLACK

    reasons: list[str] = []
    if candidate.macro_f1 < active.macro_f1 - f1_slack:
        reasons.append("macro_f1")
    if candidate.auto_band_precision is not None:
        active_ap = active.auto_band_precision
        target = AUTO_BAND_FLOOR if active_ap is None else max(active_ap, AUTO_BAND_FLOOR)
        if candidate.auto_band_precision < target:
            reasons.append("auto_band_precision")
    if candidate.abstain_rate > active.abstain_rate + GATE_ABSTAIN_SLACK:
        reasons.append("abstain_rate")
    return GateResult(
        ship=not reasons,
        reasons=reasons,
        candidate=candidate,
        active=active,
        extra={"active_calibration_degraded": degraded},
    )


def _fit_scoring_calibrator(model: GbmModel, train_events: list[dict]) -> IsotonicCalibrator | None:
    """Fit an install-wide isotonic calibrator on the train slice for honest p*.

    The candidate's production calibrators are only fitted *after* it ships, so the
    gate calibrates both models identically on the train slice — a fair, leakage-
    free basis for the abstain/auto-band metrics. Too few rows → raw max-prob.
    """
    if len(train_events) < GATE_CALIB_MIN:
        return None
    preds = score_events(model, train_events, calibrator=None)
    raw = [p.p_star for p in preds]
    correct = [1 if p.correct else 0 for p in preds]
    if len(set(correct)) < 2:  # isotonic needs both outcomes present
        return None
    return IsotonicCalibrator.fit(raw, correct)


TrainFn = Callable[[list[dict]], GbmModel]

# Sentinel for ``run_gate(active_calibrator=…)``: fit the active's scoring calibrator on
# the train slice (the historical leakage-free proxy). Distinct from ``None``, which means
# "score the active with no calibrator (raw max-prob), because that is how it ships".
_REFIT_ACTIVE_CALIBRATOR = object()


def run_gate(
    rows: list[dict],
    active: GbmModel | None,
    *,
    train_frac: float = 0.8,
    train_fn: TrainFn = train_gbm,
    active_calibrator: IsotonicCalibrator | None | object = _REFIT_ACTIVE_CALIBRATOR,
) -> GateResult:
    """Full leakage-free replay gate (spec §10.1 step 2 + §10.2).

    Splits the usable events chronologically, **fits the evaluated candidate on the
    first 80 % train slice only** (``train_fn``) — never on the eval slice — and scores
    it on the held-out last 20 % with a calibrator fitted on the train slice (a
    leakage-free proxy for the candidate's future shipped calibrator, which is only fit
    after it ships). ``active=None`` (cold store) ships unconditionally.

    The active model is scored on the same held-out slice **as shipped**: when
    ``active_calibrator`` is supplied (its persisted install-wide calibrator, or ``None``
    when it shipped none and serves raw) the active is scored with exactly that, so a
    degenerate shipped calibrator shows up in the active's ECE/abstain/auto-band and cannot
    masquerade as a strong baseline (tech-debt #1). When ``active_calibrator`` is left at
    the sentinel the active's scoring calibrator is re-fit on the train slice — the
    historical behaviour, kept for direct callers that pass a never-shipped model.

    Returns ``ship=False, reasons=['no_eval_data']`` when there is not enough data to
    train a proxy and hold out an eval slice (and no active baseline → bootstrap).
    """
    events = usable_events(rows)
    train_events, eval_events = chronological_split(events, train_frac)
    if not eval_events or len(train_events) < GBM_MIN_EVENTS:
        return GateResult(ship=active is None, reasons=[] if active is None else ["no_eval_data"])

    try:
        candidate = train_fn(train_events)  # leakage-free: never sees the eval slice
    except TrainingError:
        return GateResult(ship=active is None, reasons=[] if active is None else ["candidate_cold"])

    cand_cal = _fit_scoring_calibrator(candidate, train_events)
    cand_report = evaluate(candidate, eval_events, cand_cal)
    if active is None:
        return GateResult(ship=True, reasons=[], candidate=cand_report, active=None)

    if active_calibrator is _REFIT_ACTIVE_CALIBRATOR:
        act_cal = _fit_scoring_calibrator(active, train_events)
    else:
        # Not the sentinel: a real calibrator or None passed by the caller.
        act_cal = cast("IsotonicCalibrator | None", active_calibrator)  # score AS SHIPPED
    act_report = evaluate(active, eval_events, act_cal)
    return passes_gate(cand_report, act_report)


def _load_active_model(store: ModelStore) -> GbmModel | None:
    """Load the active GBM as a model, or ``None`` when cold / schema-mismatched."""
    rec = store.load_active_gbm()
    if rec is None or rec.feature_schema_ver != FEATURE_SCHEMA_VER:
        return None
    return GbmModel.from_bytes(rec.blob)


def _load_active_install_calibrator(store: ModelStore) -> IsotonicCalibrator | None:
    """The active install-wide (``project_id is None``) shipped calibrator, or ``None``.

    This is the calibrator serving actually applies to install-wide predictions (spec
    §6.5), so scoring the active model with it in the gate evaluates the active exactly
    as shipped. ``None`` means the active shipped no install-wide calibrator (it serves
    raw max-prob), and the gate scores it raw to match.
    """
    for rec in store.load_active_calibrators():
        if rec.project_id is None and rec.feature_schema_ver == FEATURE_SCHEMA_VER:
            return IsotonicCalibrator.from_bytes(rec.blob)
    return None


ShipGate = Callable[[GbmModel, list[dict]], bool]


def make_ship_gate(
    store: ModelStore,
    *,
    train_frac: float = 0.8,
    train_fn: TrainFn = train_gbm,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ShipGate:
    """Build a :data:`~analyzer_ng.ml.retrain.ShipGate` bound to ``store`` (spec §10.2).

    The gate is **leakage-free**: it ignores the full-data ``candidate`` the retrainer
    passes for evaluation (that model was fit on 100 % of ``rows``, including the eval
    slice) and instead has :func:`run_gate` fit a proxy candidate on the 80 % train
    slice and score it against the active model on the held-out 20 % (§10.1 step 2).
    The retrainer still ships its full-data ``candidate`` when the gate returns True.

    On rejection it logs a WARN and, when the store supports it, persists the retrain's
    candidate as an inactive audit row carrying the proxy's eval metrics.
    """

    def gate(candidate: GbmModel, rows: list[dict]) -> bool:
        active = _load_active_model(store)
        # Score the active AS SHIPPED with its persisted install-wide calibrator so a
        # degenerate shipped calibrator is part of the comparison (tech-debt #1). An active
        # that shipped no install-wide calibrator (serves raw) has none to load; fall back
        # to the train-slice re-fit proxy the gate has always used for a calibrator-less
        # baseline rather than scoring it on raw over-confidence.
        active_cal: IsotonicCalibrator | None | object = _REFIT_ACTIVE_CALIBRATOR
        if active is not None:
            shipped = _load_active_install_calibrator(store)
            if shipped is not None:
                active_cal = shipped
        result = run_gate(
            rows, active, train_frac=train_frac, train_fn=train_fn, active_calibrator=active_cal
        )
        if result.ship:
            logger.info(
                "ship gate: candidate accepted (%s)",
                result.candidate.to_dict() if result.candidate else "bootstrap",
            )
            return True
        logger.warning(
            "ship gate: candidate rejected (%s); keeping active. candidate=%s active=%s",
            ",".join(result.reasons),
            result.candidate.to_dict() if result.candidate else None,
            result.active.to_dict() if result.active else None,
        )
        _persist_rejected(store, candidate, result, clock())
        return False

    return gate


def _persist_rejected(
    store: ModelStore, candidate: GbmModel, result: GateResult, now: datetime
) -> None:
    """Store the rejected candidate as an inactive audit row (best effort, §10.2)."""
    save = getattr(store, "save", None)
    if not callable(save):
        return
    metrics = {
        "rejected": True,
        "reasons": result.reasons,
        "eval": result.candidate.to_dict() if result.candidate else {},
        "active_eval": result.active.to_dict() if result.active else {},
        "evaluated_at": now.astimezone(UTC).isoformat(),
    }
    try:
        save(
            ArtifactSpec(
                kind=KIND_GBM,
                project_id=None,
                version="gbm-rejected-" + now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
                feature_schema_ver=FEATURE_SCHEMA_VER,
                blob=candidate.to_bytes(),
                metrics=metrics,
            ),
            activate=False,
        )
    except Exception:  # noqa: BLE001 — audit persistence must never block the loop
        logger.exception("failed to persist rejected-candidate audit row")
