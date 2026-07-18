"""GBM serving with atomic hot-swap (spec 03 §6.5/§6.6).

Acceptance (task T3.1): serving picks the latest shipped model atomically —
a prediction thread never observes a new booster paired with a stale calibrator
set, and a cold install (no shipped model) returns None so the caller falls back
to rules.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import UTC, datetime

from _ml_synth import synth_frame

from analyzer_ng.core.features import FEATURE_SCHEMA_VER
from analyzer_ng.ml.artifacts import KIND_CALIB, KIND_GBM, ArtifactRecord, ArtifactSpec
from analyzer_ng.ml.serving import GbmPredictor
from analyzer_ng.ml.trainer import fit_calibrators, train_gbm


class FakeModelStore:
    """In-memory ModelStore honouring the atomic single-active-per-key contract."""

    def __init__(self) -> None:
        self._rows: list[ArtifactRecord] = []
        self._next_id = 1

    def ship(self, gbm: ArtifactSpec, calibrators: Sequence[ArtifactSpec]) -> int:
        # Atomic: retire all active gbm+calib, then insert the new set active.
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


def _deactivate(r: ArtifactRecord) -> ArtifactRecord:
    return ArtifactRecord(**{**r.__dict__, "is_active": False})


def _ship(store: FakeModelStore, rows: list[dict], version: str) -> None:
    model = train_gbm(rows)
    cals = fit_calibrators(rows)
    calib_specs = [
        ArtifactSpec(
            kind=KIND_CALIB,
            project_id=pid,
            version=version,
            feature_schema_ver=FEATURE_SCHEMA_VER,
            blob=cal.to_bytes(),
        )
        for pid, cal in cals.items()
    ]
    store.ship(
        ArtifactSpec(
            kind=KIND_GBM,
            project_id=None,
            version=version,
            feature_schema_ver=FEATURE_SCHEMA_VER,
            blob=model.to_bytes(),
        ),
        calib_specs,
    )


def test_cold_predictor_returns_none():
    pred = GbmPredictor(FakeModelStore())
    assert pred.predict({}, project_id=1) is None
    assert pred.has_model() is False
    assert pred.active_version() is None


def test_predictor_serves_after_ship_and_refresh():
    store = FakeModelStore()
    rows = synth_frame(n=300, seed=10)
    _ship(store, rows, "gbm-v1")
    pred = GbmPredictor(store, refresh_interval_s=0.0)
    out = pred.predict(rows[0]["features"], project_id=1)
    assert out is not None
    assert out.label in {"pb", "ab", "si", "nd"}
    assert 0.0 <= out.max_prob <= 1.0
    assert out.model_version == "gbm-v1"
    assert pred.has_model() is True


def test_refresh_picks_up_newest_shipped_model():
    store = FakeModelStore()
    _ship(store, synth_frame(n=300, seed=11), "gbm-v1")
    pred = GbmPredictor(store, refresh_interval_s=0.0)
    assert pred.active_version() == "gbm-v1"
    _ship(store, synth_frame(n=300, seed=12), "gbm-v2")
    pred.refresh()
    assert pred.active_version() == "gbm-v2"


def test_feature_schema_mismatch_serves_rules():
    store = FakeModelStore()
    rows = synth_frame(n=300, seed=13)
    model = train_gbm(rows)
    store.ship(
        ArtifactSpec(
            kind=KIND_GBM,
            project_id=None,
            version="gbm-bad",
            feature_schema_ver=FEATURE_SCHEMA_VER + 99,
            blob=model.to_bytes(),
        ),
        [],
    )
    pred = GbmPredictor(store, refresh_interval_s=0.0)
    # Stale schema → treated as no model (caller falls back to rules).
    assert pred.predict(rows[0]["features"], project_id=1) is None


def test_predict_assembles_vector_from_models_own_feature_list():
    # Serving assembles the vector from the active model's stored feature_names (not
    # the ambient registry): a snapshot that OMITS the newest columns and carries an
    # unknown extra key still yields a valid prediction — missing columns are
    # back-filled with defaults, unknown keys are dropped. This is the schema-robust
    # invariant that lets an artifact keep serving across snapshot/registry drift.
    store = FakeModelStore()
    rows = synth_frame(n=300, seed=41)
    _ship(store, rows, "gbm-v1")
    pred = GbmPredictor(store, refresh_interval_s=0.0)

    full = dict(rows[0]["features"])
    # Drop the 4 errata columns (an "old"-shaped snapshot) and add a stray key.
    errata = [
        "status_codes_present",
        "status_codes_match_top1",
        "identifier_jaccard_top1",
        "hash_gate_blocked",
    ]
    old_shaped = {k: v for k, v in full.items() if k not in errata}
    old_shaped["some_future_feature"] = 0.99  # unknown → ignored by assembly

    out = pred.predict(old_shaped, project_id=1)
    assert out is not None
    assert out.label in {"pb", "ab", "si", "nd"}
    assert set(out.probs) == {"pb", "ab", "si", "nd"}


def test_concurrent_predict_during_swap_never_sees_torn_state():
    store = FakeModelStore()
    _ship(store, synth_frame(n=300, seed=14), "gbm-v1")
    pred = GbmPredictor(store, refresh_interval_s=0.0)
    feats = synth_frame(n=1, seed=99)[0]["features"]
    stop = threading.Event()
    errors: list[Exception] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                out = pred.predict(feats, project_id=1)
                if out is not None:
                    # A consistent snapshot: a shipped version + a valid prob.
                    assert out.model_version.startswith("gbm-v")
                    assert 0.0 <= out.max_prob <= 1.0
                    assert set(out.probs) == {"pb", "ab", "si", "nd"}
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                return

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for i in range(15):
        _ship(store, synth_frame(n=300, seed=20 + i), f"gbm-v{2 + i}")
        pred.refresh()
    stop.set()
    for t in threads:
        t.join()
    assert not errors
