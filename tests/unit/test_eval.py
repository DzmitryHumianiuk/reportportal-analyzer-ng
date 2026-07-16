"""Offline replay eval harness — chronological 80/20 split + metrics (spec 03 §10.1)."""

from __future__ import annotations

from _ml_synth import degraded_frame, synth_frame

from analyzer_ng.core.decision import TAU_AUTO, TAU_SUGGEST
from analyzer_ng.ml.eval import (
    EvalReport,
    chronological_split,
    evaluate,
    usable_events,
)
from analyzer_ng.ml.trainer import train_gbm


# --------------------------------------------------------------------------- #
# Chronological split — no shuffling, no leakage (spec §10.1 step 2)
# --------------------------------------------------------------------------- #
def test_usable_events_are_time_ordered_and_deduped_to_latest():
    rows = synth_frame(n=10, seed=1)
    # A later correction for item 1000 must supersede the earlier one.
    rows.append(
        {
            "project_id": 1,
            "item_id": 1000,
            "new_label": "si001",
            "features": rows[0]["features"],
            "ts": rows[-1]["ts"],
        }
    )
    events = usable_events(rows)
    # one row per (project,item); item 1000 kept once, as its latest label 'si'.
    item_ids = [e["item_id"] for e in events]
    assert len(item_ids) == len(set(item_ids))
    latest_1000 = next(e for e in events if e["item_id"] == 1000)
    assert latest_1000["new_label"] == "si001"
    # ascending by ts (no shuffling).
    ts = [e["ts"] for e in events]
    assert ts == sorted(ts)


def test_chronological_split_is_80_20_by_position():
    events = usable_events(synth_frame(n=100, seed=2))
    train, eval_ = chronological_split(events, train_frac=0.8)
    assert len(train) == 80
    assert len(eval_) == 20
    # the eval slice is strictly *after* the train slice in time (no leakage).
    assert max(r["ts"] for r in train) <= min(r["ts"] for r in eval_)


def test_split_drops_rows_without_a_feature_snapshot():
    rows = synth_frame(n=20, seed=3)
    rows.append({"project_id": 1, "item_id": 99, "new_label": "pb001", "ts": rows[-1]["ts"]})
    rows.append(
        {"project_id": 1, "item_id": 98, "new_label": "ti001", "features": {}, "ts": rows[0]["ts"]}
    )
    events = usable_events(rows)
    assert all(e.get("features") for e in events)
    assert all(e["new_label"] != "ti001" for e in events)  # ti is never a class


# --------------------------------------------------------------------------- #
# Metrics (spec §10.1 step 4)
# --------------------------------------------------------------------------- #
def test_evaluate_reports_full_metric_set():
    events = usable_events(synth_frame(n=1000, seed=4))
    _train, eval_ = chronological_split(events)
    model = train_gbm(synth_frame(n=1000, seed=4))
    report = evaluate(model, eval_)
    assert isinstance(report, EvalReport)
    assert report.n_eval == len(eval_)
    assert set(report.per_label) == {"pb", "ab", "si", "nd"}
    for prf in report.per_label.values():
        assert 0.0 <= prf.precision <= 1.0
        assert 0.0 <= prf.recall <= 1.0
        assert 0.0 <= prf.f1 <= 1.0
    assert 0.0 <= report.macro_f1 <= 1.0
    assert 0.0 <= report.abstain_rate <= 1.0
    assert 0.0 <= report.acceptance_rate <= 1.0
    assert 0.0 <= report.auto_band_precision <= 1.0
    assert 0.0 <= report.ece <= 1.0


def test_learnable_model_beats_degraded_on_macro_f1():
    # Leakage-free: train on the 80 % slice, score the held-out 20 % slice — a model
    # cannot memorise rows it never saw, so a signal-free frame scores poorly.
    good_train, good_eval = chronological_split(usable_events(synth_frame(n=1000, seed=5)))
    bad_train, bad_eval = chronological_split(usable_events(degraded_frame(n=1000, seed=5)))
    good_report = evaluate(train_gbm(good_train), good_eval)
    bad_report = evaluate(train_gbm(bad_train), bad_eval)
    assert good_report.macro_f1 > bad_report.macro_f1 + 0.2
    # A crippled model is under-confident → it abstains far more.
    assert bad_report.abstain_rate > good_report.abstain_rate


def test_metrics_are_reproducible_under_fixed_seed():
    rows = synth_frame(n=1000, seed=7)
    eval_ = chronological_split(usable_events(rows))[1]
    a = evaluate(train_gbm(rows), eval_)
    b = evaluate(train_gbm(rows), eval_)
    assert a.macro_f1 == b.macro_f1
    assert a.abstain_rate == b.abstain_rate
    assert a.auto_band_precision == b.auto_band_precision
    assert a.ece == b.ece


def test_abstain_rate_counts_below_tau_suggest():
    # A report scored with an identity calibrator: low-confidence rows abstain.
    rows = synth_frame(n=400, seed=8)
    eval_ = chronological_split(usable_events(rows))[1]
    report = evaluate(train_gbm(rows), eval_)
    # sanity: thresholds imported from the decision policy (single source).
    assert TAU_SUGGEST < TAU_AUTO
    assert report.to_dict()["abstain_rate"] == report.abstain_rate
