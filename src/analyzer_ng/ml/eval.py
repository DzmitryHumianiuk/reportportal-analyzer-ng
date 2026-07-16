"""Offline replay evaluation harness (spec 03 §10.1).

The learning loop is closed by replaying accumulated ``label_event`` rows — each
carrying the ``suggestion.features`` snapshot recorded when the suggestion was made
(spec §6.4) — against a model and scoring the result. Because predictions are
recomputed **from the stored feature vectors**, the eval is serving-identical by
construction: no features are recomputed from raw logs.

Split policy (spec §10.1 step 2): the usable events are ordered chronologically
(ascending ``ts``) and split by position — the first 80 % is the train slice, the
last 20 % is the eval slice. There is **no shuffling**: an event never influences a
prediction that is scored earlier in time, so the metrics cannot leak the future.

Reported metrics (spec §10.1 step 4), all deterministic for a fixed model + slice:
per-label precision/recall/F1 over ``pb/ab/si/nd``, macro-F1, abstain rate
(``p* < τ_suggest``), acceptance-simulated rate (accuracy among non-abstained),
auto-band precision (accuracy within ``p* ≥ τ_auto`` — the metric that guards
auto-labeling), and calibration ECE over 10 equal-width bins.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from analyzer_ng.core.decision import TAU_AUTO, TAU_SUGGEST
from analyzer_ng.core.features import BASE_LABELS, to_vector
from analyzer_ng.ml.calibration import IsotonicCalibrator
from analyzer_ng.ml.trainer import GbmModel, base_label

# Number of equal-width probability bins for the ECE estimate (spec §10.1).
ECE_BINS = 10


@dataclass(frozen=True)
class LabelPRF:
    """Precision / recall / F1 for one base label."""

    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class Prediction:
    """One scored eval row: ground truth, argmax label, calibrated max-prob."""

    true: str
    pred: str
    p_star: float

    @property
    def correct(self) -> bool:
        return self.true == self.pred


@dataclass(frozen=True)
class EvalReport:
    """The full metric set for one model on one eval slice (spec §10.1 step 4)."""

    n_eval: int
    per_label: dict[str, LabelPRF]
    macro_f1: float
    abstain_rate: float
    acceptance_rate: float
    auto_band_precision: float
    ece: float

    def to_dict(self) -> dict:
        """Flat JSON-able view (stamped into ``model_artifact.metrics``, §10.2)."""
        return {
            "n_eval": self.n_eval,
            "macro_f1": self.macro_f1,
            "abstain_rate": self.abstain_rate,
            "acceptance_rate": self.acceptance_rate,
            "auto_band_precision": self.auto_band_precision,
            "ece": self.ece,
            "per_label": {
                lbl: {
                    "precision": p.precision,
                    "recall": p.recall,
                    "f1": p.f1,
                    "support": p.support,
                }
                for lbl, p in self.per_label.items()
            },
        }


# --------------------------------------------------------------------------- #
# Event selection & chronological split (spec §10.1 steps 1-2)
# --------------------------------------------------------------------------- #
def usable_events(rows: list[dict]) -> list[dict]:
    """Replay-usable events: a feature snapshot, a GBM-class label, latest-per-item.

    Rows without a stored ``features`` snapshot or whose label is not one of the
    four base classes (``ti``/unknown) are dropped (§6.4). For each
    ``(project_id, item_id)`` only the newest event survives — later corrections
    supersede earlier ones (§6.5). The result is ordered ascending by ``ts`` so the
    positional split is chronological.
    """
    latest: dict[tuple[int, int], dict] = {}
    for r in rows:
        if not r.get("features"):
            continue
        if base_label(r.get("new_label")) is None:
            continue
        key = (int(r.get("project_id", 0)), int(r.get("item_id", 0)))
        cur = latest.get(key)
        if cur is None or _ts_key(r) >= _ts_key(cur):
            latest[key] = r
    return sorted(latest.values(), key=lambda r: (_ts_key(r), int(r.get("item_id", 0))))


def chronological_split(
    events: list[dict], train_frac: float = 0.8
) -> tuple[list[dict], list[dict]]:
    """Split already-ordered events by position into (train 80 %, eval 20 %)."""
    split = int(len(events) * train_frac)
    return events[:split], events[split:]


def _ts_key(row: dict):  # noqa: ANN202 — sortable ts proxy
    ts = row.get("ts")
    return ts if ts is not None else 0


# --------------------------------------------------------------------------- #
# Scoring & metrics (spec §10.1 step 4)
# --------------------------------------------------------------------------- #
def score_events(
    model: GbmModel, events: list[dict], calibrator: IsotonicCalibrator | None = None
) -> list[Prediction]:
    """Recompute (argmax, calibrated max-prob) for each event from its snapshot."""
    preds: list[Prediction] = []
    for e in events:
        label, raw, _probs = model.predict_label(to_vector(e["features"]))
        p_star = calibrator.predict(raw) if calibrator is not None else raw
        true = base_label(e.get("new_label")) or ""
        preds.append(Prediction(true=true, pred=label, p_star=float(p_star)))
    return preds


def _prf(preds: list[Prediction], label: str) -> LabelPRF:
    tp = sum(1 for p in preds if p.pred == label and p.true == label)
    fp = sum(1 for p in preds if p.pred == label and p.true != label)
    fn = sum(1 for p in preds if p.pred != label and p.true == label)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return LabelPRF(precision=prec, recall=rec, f1=f1, support=tp + fn)


def _ece(preds: list[Prediction], bins: int = ECE_BINS) -> float:
    n = len(preds)
    if n == 0:
        return 0.0
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        in_bin = [
            p for p in preds if (lo <= p.p_star < hi) or (b == bins - 1 and p.p_star == hi)
        ]
        if not in_bin:
            continue
        conf = sum(p.p_star for p in in_bin) / len(in_bin)
        acc = sum(1 for p in in_bin if p.correct) / len(in_bin)
        total += (len(in_bin) / n) * abs(acc - conf)
    return total


def report_from_predictions(preds: list[Prediction]) -> EvalReport:
    """Assemble an :class:`EvalReport` from scored predictions (pure)."""
    n = len(preds)
    per_label = {lbl: _prf(preds, lbl) for lbl in BASE_LABELS}
    macro_f1 = float(np.mean([per_label[lbl].f1 for lbl in BASE_LABELS])) if n else 0.0

    abstained = [p for p in preds if p.p_star < TAU_SUGGEST]
    non_abstained = [p for p in preds if p.p_star >= TAU_SUGGEST]
    auto_band = [p for p in preds if p.p_star >= TAU_AUTO]

    abstain_rate = len(abstained) / n if n else 0.0
    acceptance_rate = (
        sum(1 for p in non_abstained if p.correct) / len(non_abstained) if non_abstained else 0.0
    )
    # Vacuous precision (1.0) when nothing enters the auto band — a model that never
    # auto-labels wrongly-labels nothing; the abstain/macro-F1 conditions catch it.
    auto_band_precision = (
        sum(1 for p in auto_band if p.correct) / len(auto_band) if auto_band else 1.0
    )
    return EvalReport(
        n_eval=n,
        per_label=per_label,
        macro_f1=macro_f1,
        abstain_rate=abstain_rate,
        acceptance_rate=acceptance_rate,
        auto_band_precision=auto_band_precision,
        ece=_ece(preds),
    )


def evaluate(
    model: GbmModel, events: list[dict], calibrator: IsotonicCalibrator | None = None
) -> EvalReport:
    """Score ``model`` on the eval ``events`` and report the full metric set."""
    return report_from_predictions(score_events(model, events, calibrator))
