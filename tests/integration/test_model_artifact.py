"""Integration tests for the learning-loop persistence (spec 03 §6.5).

Run against real dockerized ``pgvector/pgvector:pg16`` via testcontainers. Covers:

- migration 0003 creates ``model_artifact`` with the single-active-per-key index;
- :class:`PgModelStore` ships a GBM + calibrators atomically, and a re-ship swaps
  the active set so a reader only ever sees one live GBM (artifacts versioned,
  serving picks the latest atomically);
- model/calibrator blobs survive the PG round-trip and a real
  :class:`GbmPredictor` serves them;
- :meth:`PgLabelStore.count_events_since` powers the retrain counter;
- ``fetch_training_frame`` returns the stored feature snapshot the trainer reads
  (spec §6.4: training reads only snapshots).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from analyzer_ng.core.features import FEATURE_SCHEMA_VER, FEATURES, to_vector
from analyzer_ng.db.repositories import (
    LabelEventIn,
    PgLabelStore,
    PgRetrievalStore,
    SuggestionIn,
    TestItemIn,
)
from analyzer_ng.db.startup import bootstrap_and_migrate
from analyzer_ng.ml.artifacts import KIND_CALIB, KIND_GBM, ArtifactSpec, PgModelStore
from analyzer_ng.ml.retrain import Retrainer
from analyzer_ng.ml.serving import GbmPredictor
from analyzer_ng.ml.trainer import fit_calibrators, train_gbm

TestItemIn.__test__ = False  # type: ignore[attr-defined]

_BASE = ("pb", "ab", "si", "nd")
_LOC = {"pb": "pb001", "ab": "ab001", "si": "si001", "nd": "nd001"}
_HIST = {"pb": "hist_pb", "ab": "hist_ab", "si": "hist_si", "nd": "hist_nd"}


def _features_for(label: str, rng: random.Random) -> dict[str, float]:
    values = {f.name: f.default for f in FEATURES}
    for b in _BASE:
        values[_HIST[b]] = round(rng.uniform(0.0, 0.15), 4)
    values[_HIST[label]] = round(rng.uniform(0.7, 0.95), 4)
    values["top1_cosine"] = round(values[_HIST[label]], 4)
    return values


def _frame(n: int = 300, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    return [
        {
            "project_id": 1,
            "item_id": 1000 + i,
            "new_label": _LOC[_BASE[i % 4]],
            "features": _features_for(_BASE[i % 4], rng),
        }
        for i in range(n)
    ]


class _FrameLabels:
    """Structural LabelStore feeding the retrainer a fixed synthetic frame."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetch_training_frame(self) -> list[dict]:
        return list(self._rows)

    def count_events_since(
        self, since: datetime | None = None, project_id: int | None = None
    ) -> int:
        return len(self._rows)


def _dsn_for_db(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    if params.get("host") in (None, "localhost"):
        params["host"] = "127.0.0.1"
    params["dbname"] = dbname
    return make_conninfo(**params)


@pytest.fixture
def store_dsn(postgres_dsn: str) -> Iterator[str]:
    dbname = f"anz_{uuid4().hex[:12]}"
    dsn = _dsn_for_db(postgres_dsn, dbname)
    bootstrap_and_migrate(dsn, create_db=True, attempts=3, delay=0.0)
    try:
        yield dsn
    finally:
        with psycopg.connect(_dsn_for_db(postgres_dsn, "postgres"), autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


@pytest.fixture
def pool(store_dsn: str) -> Iterator[ConnectionPool]:
    with ConnectionPool(store_dsn, min_size=1, max_size=4, open=True) as p:
        yield p


def _spec(kind: str, project_id, version: str, blob: bytes, n_events: int = 0) -> ArtifactSpec:
    return ArtifactSpec(
        kind=kind,
        project_id=project_id,
        version=version,
        feature_schema_ver=FEATURE_SCHEMA_VER,
        blob=blob,
        n_events=n_events,
        metrics={"scope": "test"},
    )


# --------------------------------------------------------------------------- #
# model_artifact storage + atomic swap
# --------------------------------------------------------------------------- #
def test_model_artifact_table_and_unique_index_exist(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        cols = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='analyzer' AND table_name='model_artifact'"
        ).fetchall()
        names = {c[0] for c in cols}
    assert {"model_id", "kind", "project_id", "version", "feature_schema_ver", "blob",
            "is_active", "metrics", "trained_at"} <= names


def test_ship_persists_and_loads_gbm_plus_calibrators(pool: ConnectionPool) -> None:
    store = PgModelStore(pool)
    rows = _frame(n=300, seed=1)
    model = train_gbm(rows)
    cals = fit_calibrators(rows)
    calib_specs = [
        _spec(KIND_CALIB, pid, "gbm-v1", cal.to_bytes()) for pid, cal in cals.items()
    ]
    gbm_id = store.ship(_spec(KIND_GBM, None, "gbm-v1", model.to_bytes(), n_events=len(rows)),
                        calib_specs)
    assert gbm_id > 0

    rec = store.load_active_gbm()
    assert rec is not None
    assert rec.version == "gbm-v1"
    assert rec.blob == model.to_bytes()  # blob survives PG round-trip byte-identical
    assert store.active_gbm_version() == (rec.model_id, "gbm-v1")
    assert store.last_trained_at() is not None
    loaded = store.load_active_calibrators()
    assert {c.project_id for c in loaded} == {None, 1}  # install-wide + project 1


def test_reship_atomically_swaps_active_set(pool: ConnectionPool) -> None:
    store = PgModelStore(pool)
    m1 = train_gbm(_frame(n=300, seed=2))
    m2 = train_gbm(_frame(n=300, seed=3))
    store.ship(_spec(KIND_GBM, None, "gbm-v1", m1.to_bytes()), [])
    store.ship(_spec(KIND_GBM, None, "gbm-v2", m2.to_bytes()), [])

    assert store.active_gbm_version() == (store.load_active_gbm().model_id, "gbm-v2")
    # Exactly one active install-wide GBM at any time (the partial-unique index).
    with pool.connection() as conn:
        n_active = conn.execute(
            "SELECT count(*) FROM analyzer.model_artifact "
            "WHERE kind='gbm' AND project_id IS NULL AND is_active"
        ).fetchone()[0]
    assert n_active == 1


def test_predictor_serves_model_loaded_from_real_pg(pool: ConnectionPool) -> None:
    store = PgModelStore(pool)
    rows = _frame(n=300, seed=4)
    model = train_gbm(rows)
    cals = fit_calibrators(rows)
    store.ship(
        _spec(KIND_GBM, None, "gbm-v1", model.to_bytes(), n_events=len(rows)),
        [_spec(KIND_CALIB, pid, "gbm-v1", cal.to_bytes()) for pid, cal in cals.items()],
    )
    predictor = GbmPredictor(store, refresh_interval_s=0.0)
    out = predictor.predict(to_vector(rows[0]["features"]), project_id=1)
    assert out is not None
    assert out.label in {"pb", "ab", "si", "nd"}
    assert out.model_version == "gbm-v1"


def test_retrainer_ships_to_pg_and_predictor_picks_it_up(pool: ConnectionPool) -> None:
    store = PgModelStore(pool)
    predictor = GbmPredictor(store, refresh_interval_s=0.0)
    retrainer = Retrainer(_FrameLabels(_frame(n=300, seed=5)), store, predictor)
    assert predictor.has_model() is False
    out = retrainer.maybe_retrain(reason="route")
    assert out.shipped
    assert predictor.has_model() is True
    assert predictor.active_version() == out.version


# --------------------------------------------------------------------------- #
# label counter + training-frame snapshot join
# --------------------------------------------------------------------------- #
def test_count_events_since_powers_retrain_counter(pool: ConnectionPool) -> None:
    labels = PgLabelStore(pool)
    t0 = datetime.now(UTC)
    for i in range(5):
        labels.append_event(
            LabelEventIn(project_id=1, item_id=i, new_label="pb001", source="rp_defect_update")
        )
    assert labels.count_events_since() == 5
    assert labels.count_events_since(since=t0) == 5
    assert labels.count_events_since(project_id=2) == 0


def test_training_frame_returns_stored_snapshot(pool: ConnectionPool) -> None:
    retrieval = PgRetrievalStore(pool)
    labels = PgLabelStore(pool)
    # Seed an item, a suggestion (feature snapshot), then a label_event for it.
    retrieval.upsert_items([
        TestItemIn(item_id=77, project_id=1, launch_id=9, issue_type="ab001", test_case_hash=555)
    ])
    feats = _features_for("ab", random.Random(0))
    retrieval.write_suggestion(
        SuggestionIn(
            project_id=1, item_id=77, launch_id=9, predicted_label="ab001",
            confidence=0.8, features=feats, model_ver="rule_cold;fs=1;emb=none",
        )
    )
    labels.append_event(
        LabelEventIn(project_id=1, item_id=77, new_label="ab001", source="rp_defect_update")
    )
    frame = labels.fetch_training_frame(project_id=1)
    row = next(r for r in frame if r["item_id"] == 77)
    assert row["new_label"] == "ab001"
    assert row["features"]  # the stored snapshot is present (training reads only this)
    assert row["features"]["hist_ab"] == feats["hist_ab"]
