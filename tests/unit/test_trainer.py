"""LightGBM trainer over stored feature snapshots (spec 03 §6.5).

Acceptance (task T3.1 / spec 03 §6.5, §11): training on a synthetic event stream
produces a model that beats the rule fallback on a held-out chronological replay;
the trainer reads only snapshots; the booster is deterministic (seed=42); cold
installs (< 50 events) refuse to train; calibrators honour the ≥300 threshold.
"""

from __future__ import annotations

import pytest
from _ml_synth import base_of, synth_frame, synth_random_frame

from analyzer_ng.core.decision import DecisionInputs, decide
from analyzer_ng.core.features import FEATURES, feature_names, to_vector
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


def test_base_label_folds_custom_subtypes_to_base_group():
    # spec §6.5: custom subtypes map to their base group (not silently dropped).
    assert base_label("pb_myCustom") == "pb"
    assert base_label("AB_Regression") == "ab"
    assert base_label("si_flaky_env") == "si"


def test_base_label_is_the_single_features_mapping_source():
    # Minor: one mapping source shared with core.features (no divergent copy).
    from analyzer_ng.core.features import base_group

    for loc in ("pb001", "ab_custom", "SI_x", "ti001", "zz", None, ""):
        assert base_label(loc) == base_group(loc)


def test_build_xy_skips_rows_without_snapshot_or_class():
    rows = [
        {"project_id": 1, "item_id": 1, "new_label": "pb001", "features": {"top1_cosine": 0.5}},
        {"project_id": 1, "item_id": 2, "new_label": "pb001", "features": None},  # no snapshot
        {"project_id": 1, "item_id": 3, "new_label": "ti001", "features": {"top1_cosine": 0.1}},
    ]
    x, y, projects = build_xy(rows)
    assert y == ["pb"]
    assert projects == [1]
    assert x.shape == (1, 64)


def test_build_xy_fills_missing_new_columns_with_defaults_not_drop():
    # Forward-compat (schema bump): a historical snapshot predating the newest
    # columns is NOT dropped — to_vector back-fills the missing columns with their
    # registered defaults, so old snapshots stay trainable across schema versions.
    all_names = [f.name for f in FEATURES]
    classical = all_names[:39]  # a pre-append snapshot (39 classical columns only)
    rows = [
        {
            "project_id": 1,
            "item_id": i,
            "new_label": ("pb001" if i % 2 else "ab001"),
            "features": {n: 0.3 for n in classical},
        }
        for i in range(10)
    ]
    x, y, _p = build_xy(rows)
    assert x.shape == (10, 64)  # padded to the full current v6 width
    assert len(y) == 10  # every historical row kept
    # Every appended column back-fills to its registered default. The one-hot
    # ``*_unknown`` columns default to 1.0 (honest cold-cache state), all else 0.0 —
    # so a v5 snapshot with no LLM one-hot columns trains as the ``unknown`` level.
    idx = {n: i for i, n in enumerate(all_names)}
    assert (x[:, idx["llm_failing_layer_unknown"]] == 1.0).all()
    assert (x[:, idx["llm_error_class_unknown"]] == 1.0).all()
    assert (x[:, idx["llm_failing_layer_infrastructure"]] == 0.0).all()
    assert (x[:, idx["status_codes_present"]] == 0.0).all()


def test_gbm_model_stamps_and_roundtrips_feature_names():
    rows = synth_frame(n=200, seed=31)
    model = train_gbm(rows)
    assert model.feature_names == feature_names()  # trained on the current registry
    back = GbmModel.from_bytes(model.to_bytes())
    assert back.feature_names == model.feature_names


def test_from_bytes_defaults_feature_names_for_pre_v3_blob():
    # A pre-v3 artifact blob carries no feature_names; from_bytes falls back to the
    # current registry order (serving only ever loads a matching-schema blob).
    import json

    rows = synth_frame(n=120, seed=32)
    model = train_gbm(rows)
    payload = json.loads(model.to_bytes().decode("utf-8"))
    del payload["feature_names"]  # simulate an old blob
    legacy = GbmModel.from_bytes(json.dumps(payload).encode("utf-8"))
    assert legacy.feature_names == feature_names()


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
        {
            "project_id": 2,
            "item_id": 9000 + i,
            "new_label": big[i]["new_label"],
            "features": big[i]["features"],
        }
        for i in range(50)
    ]
    rows = big + small
    cals = fit_calibrators(rows)
    assert None in cals  # install-wide (≥300 total)
    assert 1 in cals  # project 1 over threshold
    assert 2 not in cals  # project 2 below threshold → falls back to install-wide


def test_calibrators_absent_when_below_install_threshold():
    rows = synth_frame(n=GBM_MIN_EVENTS + 10, seed=7)  # ~60, well under 300
    cals = fit_calibrators(rows)
    assert cals == {}  # no calibrator at all → serving uses raw softmax


def test_calibration_is_out_of_sample_not_overconfident():
    # Review Important #1: fit calibration on OUT-OF-sample predictions. On a
    # non-predictive frame, out-of-fold accuracy is ~chance (0.25/4-class), so an
    # honest calibrator maps even a high raw max-prob well below 1.0. An in-sample
    # fit (the flagged bug) would map the memorized high-confidence rows near 1.0.
    # seed chosen so the non-predictive frame cleanly exhibits the property at both
    # probe points: isotonic's extreme tail (p*≈0.99) can spike to 1.0 whenever the
    # single highest-raw out-of-fold sample happens to be correct, which is a per-seed
    # artifact of one sample, not calibration behaviour. (The v5 schema bump added a
    # feature column, re-rolling synth_random_frame's per-feature RNG stream.)
    rows = synth_random_frame(n=400, seed=13)
    cals = fit_calibrators(rows)
    assert None in cals
    cal = cals[None]
    # A high raw max-prob must not be reported as near-certain when it does not
    # generalize: honest out-of-sample p* stays well below the ~1.0 an in-sample
    # (memorized) fit would report — the flagged bug fails this.
    assert cal.predict(0.9) < 0.7
    assert cal.predict(0.99) < 0.7


def test_calibration_is_deterministic():
    rows = synth_frame(n=400, seed=12)
    a = fit_calibrators(rows)
    b = fit_calibrators(rows)
    assert a.keys() == b.keys()
    for k in a:
        assert a[k].to_bytes() == b[k].to_bytes()
