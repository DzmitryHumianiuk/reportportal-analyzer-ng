"""Retrain orchestration + triggers (spec 03 §6.5).

A retrain fires on either of two conditions, whichever comes first, and is
debounced to at most one retrain per hour (spec §6.5):

* **counter** — every ``N=100`` new ``label_event`` rows per install, checked on
  each ``defect_update``/feedback ingestion;
* **nightly** — an internal timer at 02:00 (delivered through the real
  ``train_models`` route or the in-process :class:`NightlyRetrainTimer`).

A retrain reads the stored feature snapshots (never raw features — spec §6.4),
trains the install-wide multiclass GBM (:func:`train_gbm`), fits the per-project
isotonic calibrators (:func:`fit_calibrators`), and **ships** them atomically into
``model_artifact`` (:meth:`ModelStore.ship`). The live :class:`GbmPredictor`, if
wired, is refreshed so the new model is served immediately.

The eval/ship gate (spec §10) is intentionally **not** implemented here — that is
task T3.2. :meth:`Retrainer.retrain` exposes a ``gate`` seam so T3.2 can reject a
candidate before it is shipped without touching the trigger machinery.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from analyzer_ng.core.features import FEATURE_SCHEMA_VER
from analyzer_ng.ml.artifacts import KIND_CALIB, KIND_GBM, ArtifactSpec, ModelStore
from analyzer_ng.ml.serving import GbmPredictor
from analyzer_ng.ml.trainer import GbmModel, TrainingError, build_xy, fit_calibrators, train_gbm

logger = logging.getLogger(__name__)

# spec §6.5: retrain every 100 new events; debounce to ≥ 1 retrain/hour; nightly 02:00.
RETRAIN_EVENT_THRESHOLD = 100
MIN_RETRAIN_INTERVAL = timedelta(hours=1)
NIGHTLY_HOUR_UTC = 2

REASON_EVENTS = "events"
REASON_NIGHTLY = "nightly"
REASON_ROUTE = "route"

STATUS_SHIPPED = "shipped"
STATUS_SKIPPED = "skipped"


class _LabelSource(Protocol):
    """Structural subset of LabelStore the retrainer needs (kept local for fakes)."""

    def count_events_since(
        self, since: datetime | None = None, project_id: int | None = None
    ) -> int: ...
    def fetch_training_frame(self) -> list[dict]: ...


@dataclass(frozen=True)
class RetrainOutcome:
    """The result of a (possibly skipped) retrain attempt."""

    status: str  # STATUS_SHIPPED | STATUS_SKIPPED
    reason: str  # trigger reason, or the skip cause
    version: str | None = None
    n_events: int = 0
    class_counts: dict[str, int] = field(default_factory=dict)

    @property
    def shipped(self) -> bool:
        return self.status == STATUS_SHIPPED


# A ship gate (T3.2): (candidate_model, rows) -> True to ship. Default: always ship.
ShipGate = Callable[[GbmModel, list[dict]], bool]


class Retrainer:
    """Owns the retrain decision, training, and atomic ship (spec §6.5)."""

    def __init__(
        self,
        labels: _LabelSource,
        store: ModelStore,
        predictor: GbmPredictor | None = None,
        *,
        threshold: int = RETRAIN_EVENT_THRESHOLD,
        min_interval: timedelta = MIN_RETRAIN_INTERVAL,
        gate: ShipGate | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._labels = labels
        self._store = store
        self._predictor = predictor
        self._threshold = threshold
        self._min_interval = min_interval
        self._gate = gate
        self._clock = clock
        self._lock = threading.Lock()  # serialize concurrent triggers in one process
        # Cold-phase debounce: before any model ships, store.last_trained_at() is
        # None, so nothing else bounds how often a trigger attempts a full train.
        # Record every attempt time and debounce off it too (spec §6.5 ≥1/hour).
        self._last_attempt_at: datetime | None = None

    def maybe_retrain(
        self, *, reason: str = REASON_EVENTS, now: datetime | None = None
    ) -> RetrainOutcome:
        """Retrain iff the trigger fires and the debounce window has elapsed.

        ``reason='events'`` additionally requires ``≥ threshold`` new events since
        the last trained model; ``'nightly'``/``'route'`` always pass the trigger
        check (still subject to the debounce and to there being enough data).
        """
        now = now or self._clock()
        with self._lock:
            shipped_at = self._last_trained_at()
            # Debounce off whichever is most recent: a shipped model, or (in the
            # cold phase, when nothing has shipped) the last attempt we made.
            debounce_ref = max(
                (t for t in (shipped_at, self._last_attempt_at) if t is not None),
                default=None,
            )
            if debounce_ref is not None and (now - debounce_ref) < self._min_interval:
                return RetrainOutcome(STATUS_SKIPPED, "debounced")
            if reason == REASON_EVENTS and shipped_at is not None:
                new_events = self._labels.count_events_since(shipped_at)
                if new_events < self._threshold:
                    return RetrainOutcome(STATUS_SKIPPED, "below_threshold")
            self._last_attempt_at = now  # record before training so retries debounce
            try:
                return self._retrain(reason, now)
            except TrainingError as exc:
                logger.info("retrain skipped (cold): %s", exc)
                return RetrainOutcome(STATUS_SKIPPED, "cold")

    def retrain(self, *, reason: str = REASON_ROUTE, now: datetime | None = None) -> RetrainOutcome:
        """Force a retrain regardless of debounce/threshold (still needs data)."""
        now = now or self._clock()
        with self._lock:
            try:
                return self._retrain(reason, now)
            except TrainingError as exc:
                logger.info("forced retrain skipped (cold): %s", exc)
                return RetrainOutcome(STATUS_SKIPPED, "cold")

    # -- internals -------------------------------------------------------- #
    def _retrain(self, reason: str, now: datetime) -> RetrainOutcome:
        rows = self._labels.fetch_training_frame()
        model = train_gbm(rows)  # raises TrainingError on a cold install
        _x, y, _p = build_xy(rows)
        class_counts = dict(Counter(y))
        n_events = len(y)

        if self._gate is not None and not self._gate(model, rows):
            logger.warning("candidate model rejected by ship gate; keeping active model")
            return RetrainOutcome(STATUS_SKIPPED, "gate_rejected", n_events=n_events)

        version = self._version(now)
        calibrators = fit_calibrators(rows)
        base_metrics = {
            "reason": reason,
            "trained_at": now.astimezone(UTC).isoformat(),
            "classes": class_counts,
        }
        gbm_spec = ArtifactSpec(
            kind=KIND_GBM,
            project_id=None,
            version=version,
            feature_schema_ver=FEATURE_SCHEMA_VER,
            blob=model.to_bytes(),
            n_events=n_events,
            metrics=base_metrics,
        )
        calib_specs = [
            ArtifactSpec(
                kind=KIND_CALIB,
                project_id=pid,
                version=version,
                feature_schema_ver=FEATURE_SCHEMA_VER,
                blob=cal.to_bytes(),
                n_events=n_events,
                metrics={"scope": "install" if pid is None else "project"},
            )
            for pid, cal in calibrators.items()
        ]
        self._store.ship(gbm_spec, calib_specs)
        if self._predictor is not None:
            self._predictor.refresh()
        logger.info(
            "shipped model %s (%d events, %d calibrators, reason=%s)",
            version,
            n_events,
            len(calib_specs),
            reason,
        )
        return RetrainOutcome(
            STATUS_SHIPPED, reason, version=version, n_events=n_events, class_counts=class_counts
        )

    def _last_trained_at(self) -> datetime | None:
        last = self._store.last_trained_at(KIND_GBM)
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return last

    @staticmethod
    def _version(now: datetime) -> str:
        return "gbm-" + now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


# Reason precedence when coalescing queued requests (higher = kept). A forcing
# trigger (nightly/route) must not be demoted to a plain event-count check.
_REASON_RANK = {REASON_EVENTS: 0, REASON_NIGHTLY: 1, REASON_ROUTE: 2}


class RetrainScheduler:
    """Single-flight background runner so triggers never block the caller (§6.5).

    :meth:`request` returns immediately; a daemon worker coalesces requests and
    runs :meth:`Retrainer.maybe_retrain` off the request thread. At most one
    retrain runs at a time with at most one more queued — a burst of feedback
    events collapses to a single follow-up train (single-flight). This keeps the
    heavy fetch+train+calibrate+ship off the AMQP worker handling ``defect_update``.
    """

    def __init__(self, retrainer: Retrainer, *, name: str = "retrain-scheduler") -> None:
        self._retrainer = retrainer
        self._cv = threading.Condition()
        self._pending: str | None = None
        self._running = False
        self._stopping = False
        self._started = False
        # Completed runs (audit/tests); bounded so a long-lived scheduler cannot grow
        # this unboundedly (one retrain/hour ⇒ 100 keeps ~4 days of history).
        self.outcomes: deque[RetrainOutcome] = deque(maxlen=100)
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def request(self, reason: str = REASON_EVENTS) -> None:
        """Queue a retrain (non-blocking); coalesces with any pending request."""
        with self._cv:
            if self._pending is None or _REASON_RANK.get(reason, 0) > _REASON_RANK.get(
                self._pending, 0
            ):
                self._pending = reason
            self._cv.notify_all()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while self._pending is None and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    return
                reason = self._pending
                self._pending = None
                self._running = True
            try:
                outcome = self._retrainer.maybe_retrain(reason=reason or REASON_EVENTS)
                with self._cv:
                    self.outcomes.append(outcome)
            except Exception:  # noqa: BLE001 — a failed retrain must not kill the worker
                logger.exception("background retrain failed")
            finally:
                with self._cv:
                    self._running = False
                    self._cv.notify_all()

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Block until no request is pending or running (for tests/shutdown)."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._pending is not None or self._running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(remaining)
            return True

    def stop(self, timeout: float = 5.0) -> None:
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
        if self._started:
            self._thread.join(timeout)


class NightlyRetrainTimer(threading.Thread):
    """Fires ``trigger`` once per day at ``hour:00`` UTC until stopped (spec §6.5)."""

    def __init__(
        self,
        trigger: Callable[[], object],
        *,
        hour: int = NIGHTLY_HOUR_UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(name="nightly-retrain", daemon=True)
        self._trigger = trigger
        self._hour = hour
        self._clock = clock
        self._stopping = threading.Event()

    def seconds_until_next_run(self, now: datetime) -> float:
        """Seconds from ``now`` to the next ``hour:00:00`` UTC boundary (strictly future)."""
        target = now.replace(hour=self._hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def run(self) -> None:
        while not self._stopping.is_set():
            wait_s = self.seconds_until_next_run(self._clock())
            if self._stopping.wait(wait_s):
                return
            try:
                self._trigger()
            except Exception:  # noqa: BLE001 — a failed retrain must not kill the timer
                logger.exception("nightly retrain trigger failed")

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        if self.is_alive():
            self.join(timeout=timeout)
