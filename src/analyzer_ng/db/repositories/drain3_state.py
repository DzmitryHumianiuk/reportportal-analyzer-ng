"""Drain3StateStore — per-project miner state (CAS) + queryable template mirror (spec 02 §2.3)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from analyzer_ng.db.repositories._common import StoreBase

# Advisory-lock class id for per-project Drain3 index serialization (spec 02 §2.3
# CAS). We use the *two-key* ``pg_advisory_xact_lock(classid, project_id)`` space,
# which is disjoint from the single-bigint startup/migration locks (db/startup.py,
# db/migrate.py), so a project id can never collide with them. project_id fits
# int4 (RP project ids are small).
DRAIN_INDEX_LOCK_CLASSID = 0x414E5A49  # "ANZI"


class PgDrain3StateStore(StoreBase):
    """psycopg3 implementation of :class:`~...protocols.Drain3StateStore`."""

    @contextmanager
    def project_lock(self, project_id: int) -> Iterator[None]:
        """Serialize the per-project Drain3 mine+CAS critical section, fleet-wide.

        Takes a transaction-scoped Postgres advisory lock keyed by ``project_id``
        on a dedicated pooled connection and holds it for the ``with`` body; the
        lock releases automatically when the transaction commits on exit. Concurrent
        ``index`` batches for the *same* project (including across future analyzer
        replicas) therefore run the load->mine->CAS-write section one at a time,
        so they never collide on the optimistic-concurrency ``state_version`` —
        the burst-reindex defect. Different projects use distinct keys and never
        contend. Single lock per critical section ⇒ deadlock-free.

        Hold time equals mining time (seconds for large batches); acceptable on the
        async ``index`` route (spec 01 §4.4). A waiter is bounded by the task
        watchdog's ``statement_timeout`` (``_conn``), so a pathologically long hold
        surfaces as that batch timing out rather than a hung worker.
        """
        with self._conn() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                (DRAIN_INDEX_LOCK_CLASSID, int(project_id)),
            )
            yield

    def load(self, project_id: int) -> tuple[bytes, int] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT state, state_version FROM analyzer.drain3_state WHERE project_id=%s",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return bytes(row[0]), int(row[1])

    def save(self, project_id: int, state: bytes, expected_version: int, config: dict) -> bool:
        """Optimistic-concurrency save (spec 02 §2.3).

        Returns ``False`` when another worker persisted first (the stored version
        no longer equals ``expected_version``), signalling the caller to reload,
        replay, and retry.
        """
        with self._conn() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO analyzer.project (project_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (project_id,),
            )
            cur = conn.execute(
                """
                INSERT INTO analyzer.drain3_state
                    (project_id, state, state_version, drain3_config)
                VALUES (%s, %s, 1, %s)
                ON CONFLICT (project_id) DO UPDATE
                SET state = EXCLUDED.state,
                    state_version = analyzer.drain3_state.state_version + 1,
                    drain3_config = EXCLUDED.drain3_config,
                    updated_at = now()
                WHERE analyzer.drain3_state.state_version = %s
                """,
                (project_id, state, Jsonb(config), expected_version),
            )
            return cur.rowcount > 0

    def load_template_texts(self, project_id: int) -> list[str]:
        """Mirror template patterns, most-frequent first (Drain rebuild, spec §2.3)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT pattern FROM analyzer.log_template "
                "WHERE project_id=%s ORDER BY match_count DESC, template_id",
                (project_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def upsert_templates(self, project_id: int, templates: Sequence[dict]) -> int:
        """Mirror Drain3 clusters into ``log_template`` (spec 02 §2.3).

        Each dict carries ``template_id``, ``pattern``, ``token_count`` and
        optionally ``example`` and ``match_count`` (default 0). The newest pattern
        wins; ``match_count`` accumulates.
        """
        if not templates:
            return 0
        with self._conn() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO analyzer.project (project_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (project_id,),
            )
            conn.cursor().executemany(
                """
                INSERT INTO analyzer.log_template
                    (project_id, template_id, pattern, token_count, example, match_count,
                     last_seen)
                VALUES (%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (project_id, template_id) DO UPDATE SET
                    pattern     = EXCLUDED.pattern,
                    token_count = EXCLUDED.token_count,
                    example     = COALESCE(EXCLUDED.example, analyzer.log_template.example),
                    match_count = analyzer.log_template.match_count + EXCLUDED.match_count,
                    last_seen   = now()
                """,
                [
                    (
                        project_id,
                        t["template_id"],
                        t["pattern"],
                        t.get("token_count", 0),
                        t.get("example"),
                        t.get("match_count", 0),
                    )
                    for t in templates
                ],
            )
        return len(templates)
