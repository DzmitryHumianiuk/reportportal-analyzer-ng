"""Synthetic training-frame + label-event generators for the ML unit tests.

The trainer reads only stored feature snapshots (spec 03 §6.4/§6.5), so a
training frame is just a list of ``{project_id, item_id, new_label, features, ts}``
dicts — exactly the shape ``PgLabelStore.fetch_training_frame`` returns. We build a
*learnable* frame: for each row the true base label is encoded into its matching
``hist_<label>`` feature (plus small deterministic noise), so a GBM can separate the
classes while the rule fallback (no hash/KB/seed) can only abstain.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from analyzer_ng.core.features import FEATURES
from analyzer_ng.ml.artifacts import KIND_CALIB, KIND_GBM, ArtifactRecord, ArtifactSpec

BASE = ("pb", "ab", "si", "nd")
_LOCATOR = {"pb": "pb001", "ab": "ab001", "si": "si001", "nd": "nd001"}
_HIST = {"pb": "hist_pb", "ab": "hist_ab", "si": "hist_si", "nd": "hist_nd"}
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _features_for(label: str, rng: random.Random) -> dict[str, float]:
    """A 39-feature snapshot whose dominant ``hist_*`` mass encodes ``label``."""
    values = {f.name: f.default for f in FEATURES}
    # Dominant history mass for the true label, weak mass elsewhere.
    for b in BASE:
        values[_HIST[b]] = round(rng.uniform(0.0, 0.15), 4)
    values[_HIST[label]] = round(rng.uniform(0.65, 0.95), 4)
    # A few correlated, non-leaking signals so calibration has spread.
    values["top1_cosine"] = round(min(1.0, values[_HIST[label]] + rng.uniform(-0.1, 0.1)), 4)
    values["top1_label_frac"] = round(values[_HIST[label]], 4)
    values["mean_top5_cosine"] = round(values["top1_cosine"] * 0.9, 4)
    values["n_candidates"] = round(rng.uniform(0.2, 1.0), 4)
    values["has_stacktrace"] = float(rng.random() > 0.3)
    return values


def synth_frame(n: int = 600, *, seed: int = 0, project_ids: tuple[int, ...] = (1,)) -> list[dict]:
    """A time-ordered synthetic training frame (newest last), ``n`` unique items."""
    rng = random.Random(seed)
    rows: list[dict] = []
    for i in range(n):
        label = BASE[i % 4]
        pid = project_ids[i % len(project_ids)]
        rows.append(
            {
                "project_id": pid,
                "item_id": 1000 + i,
                "new_label": _LOCATOR[label],
                "features": _features_for(label, rng),
                "ts": _T0 + timedelta(minutes=i),
            }
        )
    return rows


def base_of(locator: str) -> str:
    return locator[:2]


def degraded_frame(
    n: int = 600, *, seed: int = 0, project_ids: tuple[int, ...] = (1,)
) -> list[dict]:
    """A frame whose feature snapshots carry **no** signal for their label.

    Labels are assigned round-robin but the ``hist_*`` mass is decoupled from the
    label (a second, independent RNG stream), so a model trained on it cannot
    separate the classes — the canonical "deliberately crippled" candidate (§10.2).
    """
    rng = random.Random(seed)
    noise = random.Random(seed * 7919 + 1)
    rows: list[dict] = []
    for i in range(n):
        label = BASE[i % 4]
        # Feature mass encodes a *random* label, unrelated to the true one.
        decoy = BASE[noise.randrange(4)]
        rows.append(
            {
                "project_id": project_ids[i % len(project_ids)],
                "item_id": 5000 + i,
                "new_label": _LOCATOR[label],
                "features": _features_for(decoy, rng),
                "ts": _T0 + timedelta(minutes=i),
            }
        )
    return rows


def synth_suggestions(
    specs: Sequence[tuple],  # (project_id, day_offset, predicted_locator, confidence, outcome)
    *,
    model_ver: str = "gbm-2026.07.16",
) -> list[dict]:
    """Build suggestion-row dicts (the shape the daily aggregator consumes)."""
    rows: list[dict] = []
    for i, (pid, day_off, locator, conf, outcome) in enumerate(specs):
        rows.append(
            {
                "project_id": pid,
                "item_id": 7000 + i,
                "created_at": _T0 + timedelta(days=day_off, minutes=i),
                "predicted_label": locator,
                "confidence": conf,
                "outcome": outcome,
                "model_ver": model_ver,
            }
        )
    return rows


class FakeModelStore:
    """In-memory ModelStore honouring the single-active-per-key atomic contract."""

    def __init__(self) -> None:
        self._rows: list[ArtifactRecord] = []
        self._next_id = 1
        self.ship_calls = 0

    def ship(self, gbm: ArtifactSpec, calibrators: Sequence[ArtifactSpec]) -> int:
        self.ship_calls += 1
        self._rows = [
            r if r.kind not in (KIND_GBM, KIND_CALIB) else _deactivate(r) for r in self._rows
        ]
        gbm_id = self._insert(gbm, is_active=True)
        for spec in calibrators:
            self._insert(spec, is_active=True)
        return gbm_id

    def _insert(self, spec: ArtifactSpec, *, is_active: bool) -> int:
        mid = self._next_id
        self._next_id += 1
        self._rows.append(
            ArtifactRecord(
                model_id=mid,
                kind=spec.kind,
                project_id=spec.project_id,
                version=spec.version,
                feature_schema_ver=spec.feature_schema_ver,
                n_events=spec.n_events,
                metrics=spec.metrics,
                is_active=is_active,
                trained_at=datetime.now(UTC),
                blob=spec.blob,
            )
        )
        return mid

    def save(self, spec: ArtifactSpec, *, activate: bool = False) -> int:
        if activate:
            self._rows = [
                _deactivate(r)
                if (r.kind == spec.kind and r.project_id == spec.project_id and r.is_active)
                else r
                for r in self._rows
            ]
        return self._insert(spec, is_active=activate)

    def active_gbm_version(self) -> tuple[int, str] | None:
        for r in self._rows:
            if r.kind == KIND_GBM and r.project_id is None and r.is_active:
                return (r.model_id, r.version)
        return None

    def load_active_gbm(self) -> ArtifactRecord | None:
        for r in self._rows:
            if r.kind == KIND_GBM and r.project_id is None and r.is_active:
                return r
        return None

    def load_active_calibrators(self) -> list[ArtifactRecord]:
        return [r for r in self._rows if r.kind == KIND_CALIB and r.is_active]

    def last_trained_at(self, kind: str = KIND_GBM) -> datetime | None:
        times = [r.trained_at for r in self._rows if r.kind == kind]
        return max(times) if times else None

    def set_last_trained(self, when: datetime) -> None:
        """Test hook: stamp an active GBM row so debounce/threshold logic sees it."""
        self._rows.append(
            ArtifactRecord(
                model_id=self._next_id,
                kind=KIND_GBM,
                project_id=None,
                version="preexisting",
                feature_schema_ver=1,
                n_events=0,
                metrics={},
                is_active=True,
                trained_at=when,
                blob=b"{}",
            )
        )
        self._next_id += 1


def _deactivate(r: ArtifactRecord) -> ArtifactRecord:
    return ArtifactRecord(**{**r.__dict__, "is_active": False})


class FakeLabels:
    """Structural LabelStore for the retrainer: a frame + a new-event counter."""

    def __init__(self, rows: list[dict] | None = None, *, new_events: int = 0) -> None:
        self._rows = rows or []
        self.new_events = new_events

    def fetch_training_frame(self) -> list[dict]:
        return list(self._rows)

    def count_events_since(
        self, since: datetime | None = None, project_id: int | None = None
    ) -> int:
        return self.new_events
