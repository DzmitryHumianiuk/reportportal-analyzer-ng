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
from analyzer_ng.ml.trainer import GbmModel

logger = logging.getLogger(__name__)

# spec §10.2 exact gate thresholds.
GATE_MACRO_F1_SLACK = 0.005
AUTO_BAND_TARGET = 0.95
AUTO_BAND_FLOOR = 0.9 * AUTO_BAND_TARGET  # 0.855
GATE_ABSTAIN_SLACK = 0.05

# Minimum train-slice events before the gate fits its own calibrator for scoring;
# below this both models are scored on raw max-prob (identity), symmetrically.
GATE_CALIB_MIN = 50


@dataclass(frozen=True)
class GateResult:
    """The keep/replace verdict and the reports that produced it."""

    ship: bool
    reasons: list[str]  # failed condition names ('' when shipping)
    candidate: EvalReport | None = None
    active: EvalReport | None = None
    extra: dict = field(default_factory=dict)


def passes_gate(candidate: EvalReport, active: EvalReport | None) -> GateResult:
    """Apply the three §10.2 conditions; ``active=None`` bootstraps (always ships)."""
    if active is None:
        return GateResult(ship=True, reasons=[], candidate=candidate, active=None)

    reasons: list[str] = []
    if candidate.macro_f1 < active.macro_f1 - GATE_MACRO_F1_SLACK:
        reasons.append("macro_f1")
    if candidate.auto_band_precision < max(active.auto_band_precision, AUTO_BAND_FLOOR):
        reasons.append("auto_band_precision")
    if candidate.abstain_rate > active.abstain_rate + GATE_ABSTAIN_SLACK:
        reasons.append("abstain_rate")
    return GateResult(ship=not reasons, reasons=reasons, candidate=candidate, active=active)


def _fit_scoring_calibrator(
    model: GbmModel, train_events: list[dict]
) -> IsotonicCalibrator | None:
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


def run_gate(
    candidate: GbmModel,
    active: GbmModel | None,
    rows: list[dict],
    *,
    train_frac: float = 0.8,
) -> GateResult:
    """Full replay gate: split 80/20, score both models on the eval slice, decide.

    Both models are scored on the **same held-out eval slice** with a calibrator
    each fitted on the train slice (§10.1). ``active=None`` (cold store) ships the
    candidate unconditionally — the first model has no baseline.
    """
    events = usable_events(rows)
    train_events, eval_events = chronological_split(events, train_frac)
    if not eval_events:
        return GateResult(ship=active is None, reasons=[] if active is None else ["no_eval_data"])

    cand_cal = _fit_scoring_calibrator(candidate, train_events)
    cand_report = evaluate(candidate, eval_events, cand_cal)
    if active is None:
        return GateResult(ship=True, reasons=[], candidate=cand_report, active=None)

    act_cal = _fit_scoring_calibrator(active, train_events)
    act_report = evaluate(active, eval_events, act_cal)
    return passes_gate(cand_report, act_report)


def _load_active_model(store: ModelStore) -> GbmModel | None:
    """Load the active GBM as a model, or ``None`` when cold / schema-mismatched."""
    rec = store.load_active_gbm()
    if rec is None or rec.feature_schema_ver != FEATURE_SCHEMA_VER:
        return None
    return GbmModel.from_bytes(rec.blob)


ShipGate = Callable[[GbmModel, list[dict]], bool]


def make_ship_gate(
    store: ModelStore,
    *,
    train_frac: float = 0.8,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ShipGate:
    """Build a :data:`~analyzer_ng.ml.retrain.ShipGate` bound to ``store`` (spec §10.2).

    On rejection it logs a WARN and, when the store supports it, persists the
    candidate as an inactive audit row carrying its eval metrics.
    """

    def gate(candidate: GbmModel, rows: list[dict]) -> bool:
        active = _load_active_model(store)
        result = run_gate(candidate, active, rows, train_frac=train_frac)
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
