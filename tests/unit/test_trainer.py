"""LightGBM trainer over stored feature snapshots (spec 03 §6.5).

Acceptance (task T3.1 / spec 03 §6.5, §11): training on a synthetic event stream
produces a model that beats the rule fallback on a held-out chronological replay;
the trainer reads only snapshots; the booster is deterministic (seed=42); cold
installs (< 50 events) refuse to train; calibrators honour the ≥300 threshold.
"""

from __future__ import annotations

import pytest
from _ml_synth import base_of, synth_frame

from analyzer_ng.core.decision import DecisionInputs, decide
from analyzer_ng.core.features import to_vector
from analyzer_ng.ml.calibration import CALIB_MIN_EVENTS
from analyzer_ng.ml.trainer import (
    GBM_MIN_EVENTS,
    GbmModel,
    TrainingError,
    base_label,
    build_xy,
    fit_calibrators,
    train_gbm,
)


def test_base_label_folds_locators_and_drops_non_classes():
    assert base_label("pb001") == "pb"
    assert base_label("ab") == "ab"
    assert base_label("ti001") is None  # ti is the abstain outcome, never a class
    assert base_label(None) is None
    assert base_label("zz001") is None


def test_build_xy_skips_rows_without_snapshot_or_class():
    rows = [
        {"project_id": 1, "item_id": 1, "new_label": "pb001", "features": {"top1_cosine": 0.5}},
        {"project_id": 1, "item_id": 2, "new_label": "pb001", "features": None},  # no snapshot
        {"project_id": 1, "item_id": 3, "new_label": "ti001", "features": {"top1_cosine": 0.1}},
    ]
    x, y, projects = build_xy(rows)
    assert y == ["pb"]
    assert projects == [1]
    assert x.shape == (1, 39)


def test_build_xy_dedups_to_latest_event_per_item():
    # fetch_training_frame returns ts DESC; first occurrence is the final label.
    rows = [
        {"project_id": 1, "item_id": 7, "new_label": "ab001", "features": {"top1_cosine": 0.9}},
        {"project_id": 1, "item_id": 7, "new_label": "pb001", "features": {"top1_cosine": 0.1}},
    ]
    _x, y, _p = build_xy(rows)
    assert y == ["ab"]  # newest (first) wins


def test_cold_install_refuses_to_train():
    rows = synth_frame(n=GBM_MIN_EVENTS - 1, seed=1)
    with pytest.raises(TrainingError):
        train_gbm(rows)


def test_single_class_refuses_to_train():
    rows = [
        {"project_id": 1, "item_id": i, "new_label": "pb001", "features": {"hist_pb": 0.9}}
        for i in range(GBM_MIN_EVENTS + 5)
    ]
    with pytest.raises(TrainingError):
        train_gbm(rows)


def test_training_is_deterministic():
    rows = synth_frame(n=400, seed=2)
    m1 = train_gbm(rows)
    m2 = train_gbm(rows)
    assert m1.booster_text == m2.booster_text
    assert m1.to_bytes() == m2.to_bytes()


def test_model_bytes_roundtrip():
    rows = synth_frame(n=200, seed=3)
    model = train_gbm(rows)
    back = GbmModel.from_bytes(model.to_bytes())
    vec = to_vector(rows[0]["features"])
    assert back.predict_label(vec) == model.predict_label(vec)


def test_predict_label_returns_full_distribution_over_base_labels():
    rows = synth_frame(n=200, seed=4)
    model = train_gbm(rows)
    label, p, probs = model.predict_label(to_vector(rows[0]["features"]))
    assert set(probs) == {"pb", "ab", "si", "nd"}
    assert label in probs
    assert probs[label] == pytest.approx(p)
    assert 0.0 <= p <= 1.0


def test_trained_model_beats_rule_fallback_on_held_out_replay():
    rows = synth_frame(n=600, seed=5)
    split = int(len(rows) * 0.8)  # chronological 80/20, no shuffle (spec §10.1)
    train_rows, held_out = rows[:split], rows[split:]
    model = train_gbm(train_rows)

    gbm_correct = 0
    rule_correct = 0
    for r in held_out:
        truth = base_of(r["new_label"])
        gbm_label, _p, _probs = model.predict_label(to_vector(r["features"]))
        if gbm_label == truth:
            gbm_correct += 1
        # Rule fallback with no hash/KB/seed evidence can only abstain (ti).
        rule = decide(DecisionInputs(exception_fp=0))
        if rule.label == truth:
            rule_correct += 1

    n = len(held_out)
    assert gbm_correct / n > 0.8
    assert gbm_correct > rule_correct  # model beats the rule fallback


def test_calibrators_honour_per_project_and_install_thresholds():
    # Project 1 gets ≥300 events; project 2 stays under threshold.
    big = synth_frame(n=CALIB_MIN_EVENTS + 40, seed=6, project_ids=(1,))
    small = [
        {"project_id": 2, "item_id": 9000 + i, "new_label": big[i]["new_label"],
         "features": big[i]["features"]}
        for i in range(50)
    ]
    rows = big + small
    model = train_gbm(rows)
    cals = fit_calibrators(rows, model)
    assert None in cals  # install-wide (≥300 total)
    assert 1 in cals  # project 1 over threshold
    assert 2 not in cals  # project 2 below threshold → falls back to install-wide


def test_calibrators_absent_when_below_install_threshold():
    rows = synth_frame(n=GBM_MIN_EVENTS + 10, seed=7)  # ~60, well under 300
    model = train_gbm(rows)
    cals = fit_calibrators(rows, model)
    assert cals == {}  # no calibrator at all → serving uses raw softmax
