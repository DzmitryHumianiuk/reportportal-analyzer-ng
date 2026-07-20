"""Retrain triggers + atomic ship + nightly timer (spec 03 §6.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from _ml_synth import FakeLabels, FakeModelStore, synth_frame

from analyzer_ng.ml.retrain import (
    RETRAIN_EVENT_THRESHOLD,
    NightlyRetrainTimer,
    Retrainer,
    RetrainScheduler,
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


def test_debounce_interval_is_tunable():
    # The primary window is operator-tunable (ANALYZER_RETRAIN_DEBOUNCE_S wired through
    # handlers.bind → Retrainer(min_interval=…)). A 30-min knob lets a ship 40 min old
    # retrain even though the 1 h library default would still block it.
    rows = synth_frame(n=300, seed=40)
    labels = FakeLabels(rows, new_events=1000)
    blocked = FakeModelStore()
    r_block = Retrainer(labels, blocked, min_interval=timedelta(minutes=30), clock=lambda: NOW)
    blocked.set_last_trained(NOW - timedelta(minutes=20))  # inside 30-min window
    assert r_block.maybe_retrain(reason="nightly", now=NOW).reason == "debounced"

    allowed = FakeModelStore()
    r_allow = Retrainer(labels, allowed, min_interval=timedelta(minutes=30), clock=lambda: NOW)
    allowed.set_last_trained(NOW - timedelta(minutes=40))  # outside 30-min window
    assert r_allow.maybe_retrain(reason="nightly", now=NOW).shipped


def test_rejected_candidate_does_not_advance_debounce_but_shipped_does():
    rows = synth_frame(n=300, seed=41)
    labels = FakeLabels(rows, new_events=1000)
    # A ship-gate REJECTION persisted 1 min ago must NOT hold the debounce window: the
    # anchor counts only shipped artifacts, so an operator can retry immediately.
    rej = FakeModelStore()
    r_rej = Retrainer(labels, rej, clock=lambda: NOW)
    rej.set_last_rejected(NOW - timedelta(minutes=1))
    assert r_rej.maybe_retrain(reason="route", now=NOW).shipped, (
        "a rejected candidate must not lock out retraining"
    )
    # A genuine SHIP 1 min ago DOES hold the window.
    shipped = FakeModelStore()
    r_ship = Retrainer(labels, shipped, clock=lambda: NOW)
    shipped.set_last_trained(NOW - timedelta(minutes=1))
    assert r_ship.maybe_retrain(reason="route", now=NOW).reason == "debounced"


def test_failed_attempt_cooldown_throttles_hotloop_then_allows_retry():
    # After a gate rejection the shipped-window is untouched (so a retry is possible),
    # but the short failed-attempt cooldown stops a failing trigger from hot-looping
    # fetch+train on every message.
    rows = synth_frame(n=300, seed=42)
    labels = FakeLabels(rows, new_events=1000)
    store = FakeModelStore()
    r = Retrainer(
        labels, store, gate=lambda _m, _r: False,
        failed_cooldown=timedelta(minutes=5), clock=lambda: NOW,
    )
    first = r.maybe_retrain(reason="route", now=NOW)
    assert first.reason == "gate_rejected"
    assert labels.fetch_calls == 1
    # Immediate retry within the cooldown → debounced, no re-fetch/train.
    second = r.maybe_retrain(reason="route", now=NOW + timedelta(minutes=1))
    assert second.reason == "debounced"
    assert labels.fetch_calls == 1
    # After the cooldown the retry runs again (no shipped model advanced the window).
    third = r.maybe_retrain(reason="route", now=NOW + timedelta(minutes=6))
    assert third.reason == "gate_rejected"
    assert labels.fetch_calls == 2


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


# --------------------------------------------------------------------------- #
# Cold-phase debounce (review Important #2)
# --------------------------------------------------------------------------- #
def test_cold_phase_debounces_repeated_train_attempts():
    # Before any model ships, store.last_trained_at() is None. A second trigger
    # within the hour must NOT re-run the full fetch+train (which would thrash an
    # AMQP worker on every feedback message during the cold phase).
    labels = FakeLabels(synth_frame(n=10, seed=20))  # cold (<50) → train raises
    store = FakeModelStore()
    r = Retrainer(labels, store, clock=lambda: NOW)

    first = r.maybe_retrain(reason="events", now=NOW)
    assert first.reason == "cold"
    assert labels.fetch_calls == 1  # first attempt fetched + tried to train

    # Within the short failed-attempt cooldown (5 min) a retry is debounced without a
    # second fetch/train — a cold trigger cannot thrash an AMQP worker on every message.
    second = r.maybe_retrain(reason="events", now=NOW + timedelta(minutes=2))
    assert second.reason == "debounced"
    assert labels.fetch_calls == 1  # debounced: no second fetch/train

    # Once the (short) cooldown elapses, the cold retry is allowed again — no longer a
    # full hour, since a never-shipped attempt no longer holds the primary window.
    third = r.maybe_retrain(reason="events", now=NOW + timedelta(minutes=6))
    assert third.reason == "cold"
    assert labels.fetch_calls == 2


def test_route_retrain_not_swallowed_by_cold_phase_attempt_debounce():
    # live-fix Bug 2: an early feedback (events) attempt while cold sets the
    # cold-phase attempt marker. A LATER explicit train_models (route) publish —
    # now with plenty of data — must still ship; it must NOT be silently debounced
    # by the cold-phase throttle (which exists only to spare feedback thrash).
    labels = FakeLabels(synth_frame(n=10, seed=30))  # phase 1: cold (<50 events)
    store = FakeModelStore()
    r = Retrainer(labels, store, clock=lambda: NOW)

    cold = r.maybe_retrain(reason="events", now=NOW)
    assert cold.reason == "cold"  # first attempt tried and found the install cold

    # Data has since accumulated well past the floor.
    labels.set_rows(synth_frame(n=300, seed=30))
    out = r.maybe_retrain(reason="route", now=NOW + timedelta(minutes=5))
    assert out.shipped, "explicit train_models must ship once data exists, not debounce"
    assert store.active_gbm_version() is not None


def test_nightly_retrain_not_swallowed_by_cold_phase_attempt_debounce():
    # Same guarantee for the nightly job: a cold events attempt must not block the
    # scheduled 02:00 retrain within the hour.
    labels = FakeLabels(synth_frame(n=10, seed=31))
    store = FakeModelStore()
    r = Retrainer(labels, store, clock=lambda: NOW)
    assert r.maybe_retrain(reason="events", now=NOW).reason == "cold"
    labels.set_rows(synth_frame(n=300, seed=31))
    out = r.maybe_retrain(reason="nightly", now=NOW + timedelta(minutes=5))
    assert out.shipped


# --------------------------------------------------------------------------- #
# Single-flight background scheduler (review Important #2)
# --------------------------------------------------------------------------- #
def test_scheduler_runs_retrain_off_the_request_thread():
    labels = FakeLabels(synth_frame(n=300, seed=21))
    store = FakeModelStore()
    predictor = GbmPredictor(store, refresh_interval_s=0.0)
    retrainer = Retrainer(labels, store, predictor)
    sched = RetrainScheduler(retrainer)
    sched.start()
    try:
        sched.request("route")  # returns immediately
        assert sched.wait_idle(timeout=30.0)
    finally:
        sched.stop()
    assert store.ship_calls == 1
    assert predictor.has_model() is True
    assert sched.outcomes and sched.outcomes[-1].shipped


def test_scheduler_single_flight_coalesces_a_burst():
    # A burst of feedback requests must collapse to a single retrain (debounce +
    # single-flight), never one train per request.
    labels = FakeLabels(synth_frame(n=300, seed=22))
    store = FakeModelStore()
    retrainer = Retrainer(labels, store)
    sched = RetrainScheduler(retrainer)
    sched.start()
    try:
        for _ in range(10):
            sched.request("events")
        assert sched.wait_idle(timeout=30.0)
    finally:
        sched.stop()
    assert store.ship_calls == 1  # first ships; the rest are debounced/coalesced
