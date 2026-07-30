"""GBM serving with atomic hot-swap of the shipped model (spec 03 §6.5/§6.6).

:class:`GbmPredictor` is the decision layer's window onto the trained model. It
holds the active install-wide GBM and the per-project isotonic calibrators in a
single immutable :class:`_ServingState` object; a refresh builds a fresh state and
swaps the reference in one assignment. Because a Python attribute rebind is atomic
under the GIL, a prediction thread reads a *consistent* snapshot without locking —
it can never observe the new booster paired with the old calibrators, and a model
shipped by the trainer thread is picked up on the next refresh
(concurrent-swap-safe, spec acceptance "serving picks latest shipped model
atomically").

When no GBM is shipped (cold install, < 50 events — the artifact is simply absent),
:meth:`predict` returns ``None`` and the caller falls back to the rule-based cold
decision (spec §6.5 cold model).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from analyzer_ng.core.features import FEATURE_SCHEMA_VER, to_vector_for
from analyzer_ng.ml.artifacts import ModelStore
from analyzer_ng.ml.calibration import IsotonicCalibrator
from analyzer_ng.ml.trainer import GbmModel

logger = logging.getLogger(__name__)

# How often serving re-checks the DB for a newly shipped model (seconds). The
# trainer also calls refresh() directly after shipping, so in-process pickup is
# immediate; this bounds cross-process (other worker) staleness.
DEFAULT_REFRESH_INTERVAL_S = 30.0


@dataclass(frozen=True)
class GbmPrediction:
    """A calibrated GBM decision for one feature vector."""

    label: str  # argmax base group 'pb'|'ab'|'si'|'nd'
    max_prob: float  # calibrated max-probability (the abstain-band input, §6.6)
    raw_max_prob: float  # pre-calibration softmax max-prob
    probs: dict[str, float]  # raw distribution over base groups
    model_version: str
    calibrated: bool


@dataclass(frozen=True)
class _ServingState:
    """An immutable snapshot swapped atomically on refresh."""

    model: GbmModel | None
    version: str | None
    model_id: int | None
    calibrators: dict[int | None, IsotonicCalibrator]


_EMPTY = _ServingState(model=None, version=None, model_id=None, calibrators={})


class GbmPredictor:
    """Serves the active GBM; hot-swaps atomically when a new model ships."""

    def __init__(
        self, store: ModelStore, *, refresh_interval_s: float = DEFAULT_REFRESH_INTERVAL_S
    ):
        self._store = store
        self._interval = refresh_interval_s
        self._state: _ServingState = _EMPTY
        self._lock = threading.Lock()
        self._last_check = 0.0
        self._checked = False

    # -- serving ---------------------------------------------------------- #
    def predict(self, features: dict[str, float], project_id: int) -> GbmPrediction | None:
        """Calibrated prediction, or ``None`` when no model is shipped (cold).

        ``features`` is the name→value snapshot; the serving vector is assembled from
        the active model's *own* stored ``feature_names`` (schema-robust invariant) so
        a model is always fed columns in the order it was trained on.
        """
        self._ensure_current()
        state = self._state  # atomic snapshot (single reference read)
        if state.model is None:
            return None
        vector = to_vector_for(features, state.model.feature_names)
        label, raw, probs = state.model.predict_label(vector)
        calibrator = state.calibrators.get(project_id) or state.calibrators.get(None)
        p_star = calibrator.predict(raw) if calibrator is not None else raw
        return GbmPrediction(
            label=label,
            max_prob=p_star,
            raw_max_prob=raw,
            probs=probs,
            model_version=state.version or "",
            calibrated=calibrator is not None,
        )

    def active_version(self) -> str | None:
        """Version string of the model currently served (None when cold)."""
        self._ensure_current()
        return self._state.version

    def has_model(self) -> bool:
        self._ensure_current()
        return self._state.model is not None

    def refresh(self) -> None:
        """Force an immediate reload (called by the trainer right after shipping)."""
        with self._lock:
            self._last_check = 0.0
            self._checked = False
        self._ensure_current()

    # -- internals -------------------------------------------------------- #
    def _ensure_current(self) -> None:
        now = time.monotonic()
        if self._checked and (now - self._last_check) < self._interval:
            return
        with self._lock:
            now = time.monotonic()
            if self._checked and (now - self._last_check) < self._interval:
                return
            self._last_check = now
            self._checked = True
            active = self._store.active_gbm_version()
            if active is None:
                if self._state.model is not None:
                    self._state = _EMPTY
                return
            model_id, _version = active
            if self._state.model_id == model_id:
                return  # unchanged — no reload
            self._state = self._build_state()

    def _build_state(self) -> _ServingState:
        rec = self._store.load_active_gbm()
        if rec is None:
            return _EMPTY
        if rec.feature_schema_ver != FEATURE_SCHEMA_VER:
            logger.warning(
                "active GBM %s has feature_schema_ver=%d but serving is %d; serving rules",
                rec.version,
                rec.feature_schema_ver,
                FEATURE_SCHEMA_VER,
            )
            return _EMPTY
        model = GbmModel.from_bytes(rec.blob)
        calibrators: dict[int | None, IsotonicCalibrator] = {}
        for c in self._store.load_active_calibrators():
            if c.feature_schema_ver != FEATURE_SCHEMA_VER:
                continue
            calibrators[c.project_id] = IsotonicCalibrator.from_bytes(c.blob)
        return _ServingState(
            model=model, version=rec.version, model_id=rec.model_id, calibrators=calibrators
        )
