"""Per-project isotonic calibration (spec 03 §6.5)."""

from __future__ import annotations

from analyzer_ng.ml.calibration import CALIB_MIN_EVENTS, IsotonicCalibrator


def test_threshold_constant_matches_spec():
    # spec §6.5: per-project isotonic needs ≥ 300 events.
    assert CALIB_MIN_EVENTS == 300


def test_isotonic_is_monotone_nondecreasing():
    # Higher raw max-prob → higher (or equal) calibrated probability.
    raw = [i / 100 for i in range(100)]
    correct = [1 if r > 0.5 else 0 for r in raw]
    cal = IsotonicCalibrator.fit(raw, correct)
    outs = [cal.predict(r) for r in raw]
    assert all(b >= a - 1e-9 for a, b in zip(outs, outs[1:], strict=False))
    assert all(0.0 <= o <= 1.0 for o in outs)


def test_calibrator_bytes_roundtrip_is_stable():
    raw = [i / 50 for i in range(50)]
    correct = [i % 2 for i in range(50)]
    cal = IsotonicCalibrator.fit(raw, correct)
    blob = cal.to_bytes()
    back = IsotonicCalibrator.from_bytes(blob)
    assert back == cal
    assert back.to_bytes() == blob
    for r in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert back.predict(r) == cal.predict(r)


def test_empty_calibrator_is_identity():
    cal = IsotonicCalibrator(x=(), y=())
    assert cal.predict(0.42) == 0.42


def test_length_mismatch_raises():
    import pytest

    with pytest.raises(ValueError):
        IsotonicCalibrator.fit([0.1, 0.2], [1])


def test_out_of_range_inputs_are_clipped():
    cal = IsotonicCalibrator.fit([0.2, 0.4, 0.6, 0.8], [0, 0, 1, 1])
    # Below/above the knot range clamps to the end values, still in [0,1].
    assert 0.0 <= cal.predict(-5.0) <= 1.0
    assert 0.0 <= cal.predict(5.0) <= 1.0
