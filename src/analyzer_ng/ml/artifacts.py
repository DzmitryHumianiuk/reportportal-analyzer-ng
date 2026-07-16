"""Model-artifact storage in PostgreSQL ``bytea`` (spec 03 §6.5).

The learning loop keeps every trained artifact — the install-wide GBM and the
per-project isotonic calibrators — as opaque blobs in ``model_artifact``. There is
no filesystem: containers are stateless and PG is the only required store (§6.5).

**Atomic swap.** :meth:`ship` installs a freshly trained GBM plus its matching
calibrator set inside a *single transaction*: it deactivates the previously active
GBM and all previously active calibrators, then inserts the new rows already
active. A reader therefore only ever observes the complete old set or the complete
new set — never a mix — so serving picks the latest shipped model
concurrent-swap-safely. The partial unique index on ``(kind, project)`` guarantees
at most one active artifact per key even under racing shippers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from analyzer_ng.db.repositories._common import StoreBase, require_row

KIND_GBM = "gbm"
KIND_CALIB = "calib"


@dataclass(frozen=True)
class ArtifactSpec:
    """A serialized artifact ready to persist."""

    kind: str
    project_id: int | None
    version: str
    feature_schema_ver: int
    blob: bytes
    n_events: int = 0
    metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRecord:
    """A stored artifact loaded back (with its blob)."""

    model_id: int
    kind: str
    project_id: int | None
    version: str
    feature_schema_ver: int
    n_events: int
    metrics: dict
    is_active: bool
    trained_at: datetime
    blob: bytes


class ModelStore(Protocol):
    """Structural contract for artifact storage (in-memory fakes in tests)."""

    def ship(self, gbm: ArtifactSpec, calibrators: Sequence[ArtifactSpec]) -> int: ...
    def active_gbm_version(self) -> tuple[int, str] | None: ...
    def load_active_gbm(self) -> ArtifactRecord | None: ...
    def load_active_calibrators(self) -> list[ArtifactRecord]: ...
    def last_trained_at(self, kind: str = KIND_GBM) -> datetime | None: ...


class PgModelStore(StoreBase):
    """psycopg3 implementation of :class:`ModelStore` over ``model_artifact``."""

    def ship(self, gbm: ArtifactSpec, calibrators: Sequence[ArtifactSpec]) -> int:
        """Atomically replace the active GBM + calibrator set (single tx, §6.5)."""
        with self.transaction() as conn:
            # Retire the previously active model + its calibrators together, so a
            # reader never sees the new GBM paired with a stale calibrator set.
            conn.execute(
                "UPDATE analyzer.model_artifact SET is_active = false "
                "WHERE is_active AND kind IN (%s, %s)",
                (KIND_GBM, KIND_CALIB),
            )
            gbm_id = self._insert(conn, gbm, is_active=True)
            for spec in calibrators:
                self._insert(conn, spec, is_active=True)
            return gbm_id

    def save(self, spec: ArtifactSpec, *, activate: bool = False) -> int:
        """Persist one artifact; optionally activate it (atomic per-key swap)."""
        with self.transaction() as conn:
            if activate:
                conn.execute(
                    "UPDATE analyzer.model_artifact SET is_active = false "
                    "WHERE is_active AND kind = %s "
                    "AND COALESCE(project_id, -1) = COALESCE(%s::bigint, -1)",
                    (spec.kind, spec.project_id),
                )
            return self._insert(conn, spec, is_active=activate)

    @staticmethod
    def _insert(conn: Any, spec: ArtifactSpec, *, is_active: bool) -> int:
        cur = conn.execute(
            """
            INSERT INTO analyzer.model_artifact
                (kind, project_id, version, feature_schema_ver, n_events,
                 metrics, blob, is_active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING model_id
            """,
            (
                spec.kind,
                spec.project_id,
                spec.version,
                spec.feature_schema_ver,
                spec.n_events,
                Jsonb(spec.metrics),
                spec.blob,
                is_active,
            ),
        )
        return int(require_row(cur)[0])

    def active_gbm_version(self) -> tuple[int, str] | None:
        """Cheap (blob-free) lookup of the live GBM's (model_id, version)."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT model_id, version FROM analyzer.model_artifact "
                "WHERE kind = %s AND project_id IS NULL AND is_active LIMIT 1",
                (KIND_GBM,),
            )
            row = cur.fetchone()
            return (int(row[0]), str(row[1])) if row else None

    def load_active_gbm(self) -> ArtifactRecord | None:
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                f"{_SELECT_COLS} WHERE kind = %s AND project_id IS NULL AND is_active LIMIT 1",
                (KIND_GBM,),
            )
            row = cur.fetchone()
            return _to_record(row) if row else None

    def load_active_calibrators(self) -> list[ArtifactRecord]:
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(f"{_SELECT_COLS} WHERE kind = %s AND is_active", (KIND_CALIB,))
            return [_to_record(r) for r in cur.fetchall()]

    def last_trained_at(self, kind: str = KIND_GBM) -> datetime | None:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT max(trained_at) FROM analyzer.model_artifact WHERE kind = %s",
                (kind,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else None


_SELECT_COLS = (
    "SELECT model_id, kind, project_id, version, feature_schema_ver, n_events, "
    "metrics, is_active, trained_at, blob FROM analyzer.model_artifact"
)


def _to_record(row: dict) -> ArtifactRecord:
    return ArtifactRecord(
        model_id=int(row["model_id"]),
        kind=str(row["kind"]),
        project_id=int(row["project_id"]) if row["project_id"] is not None else None,
        version=str(row["version"]),
        feature_schema_ver=int(row["feature_schema_ver"]),
        n_events=int(row["n_events"]),
        metrics=dict(row["metrics"]) if row["metrics"] else {},
        is_active=bool(row["is_active"]),
        trained_at=row["trained_at"],
        blob=bytes(row["blob"]),
    )
