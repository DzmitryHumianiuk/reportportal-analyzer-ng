"""Retrain triggers + atomic ship + nightly timer (spec 03 §6.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from _ml_synth import FakeLabels, FakeModelStore, synth_frame

from analyzer_ng.ml.retrain import (
    RETRAIN_EVENT_THRESHOLD,
    NightlyRetrainTimer,
    Retrainer,
)
from analyzer_ng.ml.serving import GbmPredictor

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _retrainer(rows, *, new_events=0, predictor=None, gate=None):
    labels = FakeLabels(rows, new_events=new_events)
    store = FakeModelStore()
    return Retrainer(labels, store, predictor, gate=gate, clock=lambda: NOW), store, labels


def test_first_retrain_trains_when_no_model_yet():
    r, store, _ = _retrainer(synth_frame(n=300, seed=1))
    out = r.maybe_retrain(reason="events", now=NOW)
    assert out.shipped
    assert out.version and out.version.startswith("gbm-")
    assert store.active_gbm_version() is not None
    assert store.ship_calls == 1


def test_cold_install_skips_without_shipping():
    r, store, _ = _retrainer(synth_frame(n=10, seed=2))  # < 50 events
    out = r.maybe_retrain(reason="nightly", now=NOW)
    assert not out.shipped
    assert out.reason == "cold"
    assert store.active_gbm_version() is None


def test_event_trigger_below_threshold_is_skipped():
    rows = synth_frame(n=300, seed=3)
    r, store, _ = _retrainer(rows, new_events=RETRAIN_EVENT_THRESHOLD - 1)
    store.set_last_trained(NOW - timedelta(hours=2))  # debounce satisfied, threshold not
    out = r.maybe_retrain(reason="events", now=NOW)
    assert not out.shipped
    assert out.reason == "below_threshold"


def test_event_trigger_at_threshold_retrains():
    rows = synth_frame(n=300, seed=4)
    r, store, _ = _retrainer(rows, new_events=RETRAIN_EVENT_THRESHOLD)
    store.set_last_trained(NOW - timedelta(hours=2))
    out = r.maybe_retrain(reason="events", now=NOW)
    assert out.shipped


def test_debounce_blocks_retrain_within_the_hour():
    rows = synth_frame(n=300, seed=5)
    r, store, _ = _retrainer(rows, new_events=1000)
    store.set_last_trained(NOW - timedelta(minutes=20))  # < 1h ago
    out = r.maybe_retrain(reason="nightly", now=NOW)
    assert not out.shipped
    assert out.reason == "debounced"


def test_nightly_retrains_regardless_of_event_count_when_debounce_ok():
    rows = synth_frame(n=300, seed=6)
    r, store, _ = _retrainer(rows, new_events=0)  # zero new events
    store.set_last_trained(NOW - timedelta(hours=25))
    out = r.maybe_retrain(reason="nightly", now=NOW)
    assert out.shipped  # nightly does not require the event threshold


def test_force_retrain_ignores_debounce():
    rows = synth_frame(n=300, seed=7)
    r, store, _ = _retrainer(rows)
    store.set_last_trained(NOW - timedelta(minutes=1))
    out = r.retrain(reason="route", now=NOW)
    assert out.shipped


def test_ship_gate_rejection_keeps_active_model():
    rows = synth_frame(n=300, seed=8)
    r, store, _ = _retrainer(rows, gate=lambda _m, _rows: False)
    out = r.maybe_retrain(reason="route", now=NOW)
    assert not out.shipped
    assert out.reason == "gate_rejected"
    assert store.active_gbm_version() is None


def test_retrain_refreshes_predictor_so_new_model_serves():
    rows = synth_frame(n=300, seed=9)
    labels = FakeLabels(rows)
    store = FakeModelStore()
    predictor = GbmPredictor(store, refresh_interval_s=0.0)
    assert predictor.has_model() is False
    r = Retrainer(labels, store, predictor, clock=lambda: NOW)
    out = r.maybe_retrain(reason="route", now=NOW)
    assert out.shipped
    assert predictor.has_model() is True
    assert predictor.active_version() == out.version


def test_ships_install_wide_and_per_project_calibrators():
    # Project 1 gets ≥300 events, project 2 under threshold → only install + p1 calib.
    from analyzer_ng.ml.calibration import CALIB_MIN_EVENTS

    big = synth_frame(n=CALIB_MIN_EVENTS + 40, seed=10, project_ids=(1,))
    small = [
        {"project_id": 2, "item_id": 9000 + i, "new_label": big[i]["new_label"],
         "features": big[i]["features"]}
        for i in range(40)
    ]
    r, store, _ = _retrainer(big + small)
    out = r.maybe_retrain(reason="route", now=NOW)
    assert out.shipped
    calib_projects = {c.project_id for c in store.load_active_calibrators()}
    assert None in calib_projects  # install-wide
    assert 1 in calib_projects
    assert 2 not in calib_projects


# --------------------------------------------------------------------------- #
# Nightly timer scheduling
# --------------------------------------------------------------------------- #
def test_seconds_until_next_run_same_day():
    timer = NightlyRetrainTimer(lambda: None, hour=2)
    now = datetime(2026, 7, 16, 1, 0, tzinfo=UTC)  # 01:00 → 1h to 02:00
    assert timer.seconds_until_next_run(now) == 3600.0


def test_seconds_until_next_run_rolls_to_tomorrow():
    timer = NightlyRetrainTimer(lambda: None, hour=2)
    now = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)  # past 02:00 → 23h to next
    assert timer.seconds_until_next_run(now) == 23 * 3600.0


def test_timer_fires_trigger_and_stops():
    fired = []
    # hour set so the next boundary is ~immediate; use a clock just before it.
    boundary = datetime(2026, 7, 16, 2, 0, tzinfo=UTC)
    clock = lambda: boundary - timedelta(seconds=0.05)  # noqa: E731
    timer = NightlyRetrainTimer(lambda: fired.append(1), hour=2, clock=clock)
    timer.start()
    import time

    time.sleep(0.3)
    timer.stop()
    assert fired  # trigger ran at least once
