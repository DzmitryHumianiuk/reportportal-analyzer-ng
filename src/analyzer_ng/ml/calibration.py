"""Per-project isotonic calibration of the GBM max-probability (spec 03 §6.5).

The multiclass softmax max-prob is not a reliable probability; §6.5 calibrates it
with a monotone isotonic fit of ``raw max-prob → P(argmax == ground truth)``. The
fit is **per project** when the project has ≥ 300 label_events, else install-wide
(≥ 300 install-wide), else no calibrator (raw softmax is served — the serving
layer treats a missing calibrator as identity).

The fitted calibrator is serialized to its knot arrays (thresholds) as compact
JSON so the stored bytes never depend on the scikit-learn pickle format — loading
reconstructs a pure ``numpy.interp`` step function, which is deterministic and
version-independent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression

# spec §6.5: per-project isotonic needs ≥ 300 events, else install-wide, else raw.
CALIB_MIN_EVENTS = 300


@dataclass(frozen=True)
class IsotonicCalibrator:
    """A monotone step function mapping raw max-prob → calibrated probability.

    Stored as its knot arrays; evaluation is a clipped linear interpolation
    (``numpy.interp``), identical to a fitted isotonic regressor's ``predict`` on
    monotone-increasing thresholds.
    """

    x: tuple[float, ...]  # ascending raw max-prob knots
    y: tuple[float, ...]  # calibrated probabilities at each knot (monotone non-decreasing)

    @classmethod
    def fit(cls, raw: list[float], correct: list[int]) -> IsotonicCalibrator:
        """Fit ``raw max-prob → P(correct)`` with clipped isotonic regression."""
        if len(raw) != len(correct):
            raise ValueError("raw and correct must be the same length")
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(np.asarray(raw, dtype=np.float64), np.asarray(correct, dtype=np.float64))
        xs = np.asarray(iso.X_thresholds_, dtype=np.float64)
        ys = np.asarray(iso.y_thresholds_, dtype=np.float64)
        return cls(x=tuple(float(v) for v in xs), y=tuple(float(v) for v in ys))

    def predict(self, raw: float) -> float:
        """Calibrated probability for a raw max-prob (clipped at the knot range)."""
        if not self.x:
            return float(raw)
        value = float(np.interp(raw, np.asarray(self.x), np.asarray(self.y)))
        return max(0.0, min(1.0, value))

    def to_bytes(self) -> bytes:
        """Serialize to compact, version-independent JSON bytes."""
        return json.dumps({"x": list(self.x), "y": list(self.y)}).encode("utf-8")

    @classmethod
    def from_bytes(cls, blob: bytes) -> IsotonicCalibrator:
        data = json.loads(blob.decode("utf-8"))
        return cls(x=tuple(data["x"]), y=tuple(data["y"]))
