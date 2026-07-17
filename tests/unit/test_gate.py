"""Ship gate — keep/replace the active model on the frozen eval slice (spec 03 §10.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from _ml_synth import FakeLabels, FakeModelStore, degraded_frame, synth_frame

from analyzer_ng.core.features import FEATURE_SCHEMA_VER
from analyzer_ng.ml.artifacts import KIND_GBM, ArtifactSpec
from analyzer_ng.ml.eval import EvalReport, LabelPRF, chronological_split, evaluate, usable_events
from analyzer_ng.ml.gate import (
    AUTO_BAND_FLOOR,
    GATE_ABSTAIN_SLACK,
    GATE_MACRO_F1_SLACK,
    make_ship_gate,
    passes_gate,
    run_gate,
)
from analyzer_ng.ml.retrain import Retrainer
from analyzer_ng.ml.trainer import train_gbm

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _report(macro_f1, auto_prec, abstain, *, n=200):
    prf = LabelPRF(precision=macro_f1, recall=macro_f1, f1=macro_f1, support=n // 4)
    return EvalReport(
        n_eval=n,
        per_label={lbl: prf for lbl in ("pb", "ab", "si", "nd")},
        macro_f1=macro_f1,
        abstain_rate=abstain,
        acceptance_rate=macro_f1,
        auto_band_precision=auto_prec,
        auto_band_support=0 if auto_prec is None else n,
        ece=0.0,
    )


def _shuffle_labels(events, seed):
    """Crippling train_fn helper: same feature rows, labels permuted (destroys signal)."""
    import random

    labels = [e["new_label"] for e in events]
    random.Random(seed).shuffle(labels)
    return [{**e, "new_label": lbl} for e, lbl in zip(events, labels, strict=True)]


def _crippled_train_fn(seed):
    def _fn(train_events):
        return train_gbm(_shuffle_labels(train_events, seed))

    return _fn


# --------------------------------------------------------------------------- #
# Gate conditions (spec §10.2) — exact thresholds
# --------------------------------------------------------------------------- #
def test_gate_constants_match_spec():
    assert GATE_MACRO_F1_SLACK == 0.005
    assert AUTO_BAND_FLOOR == 0.9 * 0.95  # 0.9 · target 0.95
    assert GATE_ABSTAIN_SLACK == 0.05


def test_bootstrap_ships_when_no_active_model():
    # First ever model: nothing to compare against → ship.
    res = passes_gate(_report(0.5, 0.9, 0.1), None)
    assert res.ship


def test_equal_candidate_ships():
    active = _report(0.80, 0.90, 0.20)
    cand = _report(0.80, 0.90, 0.20)
    assert passes_gate(cand, active).ship


def test_macro_f1_within_slack_ships_but_below_is_rejected():
    active = _report(0.80, 0.90, 0.20)
    ok = _report(0.80 - 0.004, 0.90, 0.20)  # within 0.005 slack
    bad = _report(0.80 - 0.02, 0.90, 0.20)  # macro-F1 collapse
    assert passes_gate(ok, active).ship
    rej = passes_gate(bad, active)
    assert not rej.ship
    assert "macro_f1" in rej.reasons


def test_auto_band_precision_must_meet_floor_and_active():
    # Active already exceeds the floor → candidate must be ≥ active.
    active = _report(0.80, 0.92, 0.20)
    bad = _report(0.80, 0.88, 0.20)  # below active's 0.92
    rej = passes_gate(bad, active)
    assert not rej.ship
    assert "auto_band_precision" in rej.reasons
    # Even if active is weak, the candidate must clear the 0.855 floor.
    weak_active = _report(0.80, 0.60, 0.20)
    below_floor = _report(0.80, 0.80, 0.20)  # ≥ active but < 0.855 floor
    assert not passes_gate(below_floor, weak_active).ship
    assert passes_gate(_report(0.80, 0.86, 0.20), weak_active).ship


def test_empty_candidate_auto_band_is_not_a_vacuous_pass_or_fail():
    # Candidate makes no auto-labels (auto band empty → precision None). There is no
    # auto-labeling to guard, so the auto-band axis is not failed; the verdict rests on
    # macro-F1 / abstain (spec §10.2). Guards the old vacuous-1.0 bug.
    active = _report(0.80, 0.95, 0.20)
    cand_empty = _report(0.80, None, 0.22)  # None precision, abstain within slack
    res = passes_gate(cand_empty, active)
    assert "auto_band_precision" not in res.reasons
    assert res.ship  # macro-F1 equal, abstain within slack → ships on the other axes
    # But an empty auto band does NOT rescue a macro-F1 collapse.
    assert not passes_gate(_report(0.50, None, 0.22), active).ship


def test_abstain_rate_may_rise_at_most_slack():
    active = _report(0.80, 0.90, 0.20)
    ok = _report(0.80, 0.90, 0.25)  # +0.05 exactly
    bad = _report(0.80, 0.90, 0.2501)  # over the slack
    assert passes_gate(ok, active).ship
    rej = passes_gate(bad, active)
    assert not rej.ship
    assert "abstain_rate" in rej.reasons


# --------------------------------------------------------------------------- #
# Full replay gate (spec §10.1 + §10.2) — real models, leakage-free
# --------------------------------------------------------------------------- #
def test_run_gate_fits_candidate_leakage_free_not_on_the_eval_slice():
    # RED→GREEN for the leakage fix. On a signal-free frame an honestly-trained
    # candidate (fit on the 80% train slice only) CANNOT predict the held-out 20%, so
    # its macro-F1 stays near chance. The pre-fix wiring scored a candidate fit on
    # 100% of the same rows on the last 20% of those rows and got ~1.0 (memorisation),
    # inflating every retrain toward shipping. run_gate must fit on the train slice.
    rows = degraded_frame(n=1000, seed=31)
    res = run_gate(rows, None)  # bootstrap; candidate fit on 80% only
    assert res.candidate.macro_f1 < 0.45  # honest — no signal to learn

    # Contrast: the leaky path the fix removes (fit on ALL rows, score last 20%).
    leaky = train_gbm(rows)
    eval_slice = chronological_split(usable_events(rows))[1]
    assert evaluate(leaky, eval_slice).macro_f1 > 0.9  # inflated by leakage
    assert res.candidate.macro_f1 < evaluate(leaky, eval_slice).macro_f1 - 0.4


def test_run_gate_ships_a_good_candidate_over_active():
    rows = synth_frame(n=1000, seed=11)
    active = train_gbm(rows)
    res = run_gate(rows, active)  # candidate fit internally on the 80% slice
    assert res.ship
    assert res.candidate.macro_f1 > 0.8


def test_run_gate_rejects_a_crippled_candidate_trained_on_the_same_frame():
    # Overlap path (per review): the crippled candidate is trained on the SAME frame's
    # train slice (labels shuffled) and evaluated on that frame's held-out eval slice —
    # no separate frame. It cannot predict the good held-out data → rejected vs a good
    # active model trained on the same train slice.
    rows = synth_frame(n=1000, seed=13)
    train = chronological_split(usable_events(rows))[0]
    active = train_gbm(train)  # good baseline on the train slice
    res = run_gate(rows, active, train_fn=_crippled_train_fn(seed=99))
    assert not res.ship
    assert "macro_f1" in res.reasons


def test_run_gate_ships_first_model_when_active_is_none():
    rows = synth_frame(n=1000, seed=14)
    res = run_gate(rows, None)
    assert res.ship


# --------------------------------------------------------------------------- #
# Seam wiring (spec §10.2 into T3.1's Retrainer.gate)
# --------------------------------------------------------------------------- #
def _ship_active(store, rows):
    model = train_gbm(rows)
    store.save(
        ArtifactSpec(
            kind=KIND_GBM,
            project_id=None,
            version="active-v1",
            feature_schema_ver=FEATURE_SCHEMA_VER,
            blob=model.to_bytes(),
            n_events=len(rows),
        ),
        activate=True,
    )


def test_make_ship_gate_returns_true_for_good_candidate():
    store = FakeModelStore()
    rows = synth_frame(n=1000, seed=15)
    _ship_active(store, rows)
    gate = make_ship_gate(store)
    assert gate(train_gbm(synth_frame(n=1000, seed=16)), rows) is True


def test_make_ship_gate_rejects_degraded_candidate_and_persists_metrics():
    store = FakeModelStore()
    rows = synth_frame(n=1000, seed=17)
    _ship_active(store, rows)  # good active model in the store
    # The gate fits its proxy candidate with a crippling trainer → rejected vs active.
    gate = make_ship_gate(store, train_fn=_crippled_train_fn(seed=17))
    assert gate(train_gbm(rows), rows) is False
    # The active model is untouched; a rejected-candidate audit row is stored inactive.
    assert store.active_gbm_version()[1] == "active-v1"
    inactive = [r for r in store._rows if r.kind == KIND_GBM and not r.is_active]
    assert any("macro_f1" in r.metrics.get("eval", {}) for r in inactive)


def test_make_ship_gate_ships_first_model_on_cold_store():
    store = FakeModelStore()  # no active model yet
    rows = synth_frame(n=1000, seed=18)
    gate = make_ship_gate(store)
    assert gate(train_gbm(rows), rows) is True


# --------------------------------------------------------------------------- #
# G3 full loop: retrain → eval → ship/keep through the real Retrainer seam
# --------------------------------------------------------------------------- #
def test_full_loop_ships_first_then_keeps_active_when_candidate_is_crippled():
    rows = synth_frame(n=1000, seed=19)
    store = FakeModelStore()
    # Simulate a training regression: the gate's proxy trainer is crippled, so every
    # post-bootstrap candidate is honestly (leakage-free) scored below the active model
    # on the held-out slice and rejected — the active model is kept.
    retrainer = Retrainer(
        FakeLabels(rows),
        store,
        gate=make_ship_gate(store, train_fn=_crippled_train_fn(seed=19)),
        clock=lambda: NOW,
    )
    # 1. First retrain: no active baseline → gate bootstraps → good full-data model ships.
    out1 = retrainer.retrain(reason="route", now=NOW)
    assert out1.shipped
    ships_after_first = store.ship_calls

    # 2. Next retrain: the crippled proxy loses to the shipped good model → kept.
    out2 = retrainer.retrain(reason="route", now=NOW)
    assert not out2.shipped
    assert out2.reason == "gate_rejected"
    assert store.ship_calls == ships_after_first  # active model untouched


def test_full_loop_replaces_active_with_an_equally_good_candidate():
    rows = synth_frame(n=1000, seed=20)
    store = FakeModelStore()
    retrainer = Retrainer(
        FakeLabels(rows), store, gate=make_ship_gate(store), clock=lambda: NOW
    )
    assert retrainer.retrain(reason="route", now=NOW).shipped
    # A fresh retrain on the same good data matches the active model → gate ships it.
    assert retrainer.retrain(reason="route", now=NOW).shipped
    assert store.ship_calls == 2
