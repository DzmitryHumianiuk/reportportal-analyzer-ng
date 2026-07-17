"""LLM audit-log and per-project role kill-switch stores (spec 04 §6.1).

``PgLlmEventStore`` appends one ``llm_event`` per call (every LLM suggestion is
logged with its prompt_hash + model tag). ``PgLlmRoleStateStore`` reads/writes the
``llm_role_state`` runtime switch the nightly eval job (T4.2) flips; T4.1 only
reads it (a role is on unless a row says otherwise) and provides the write path.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from analyzer_ng.db.repositories._common import StoreBase, require_row


class PgLlmEventStore(StoreBase):
    def record(
        self,
        *,
        project_id: int,
        item_id: int,
        role: str,
        model: str,
        prompt_hash: str,
        cache_hit: bool,
        outcome: str,
        output: dict | None = None,
        latency_ms: int | None = None,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO analyzer.llm_event
                    (project_id, item_id, role, model, prompt_hash, cache_hit,
                     outcome, output, latency_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING event_id
                """,
                (
                    project_id,
                    item_id,
                    role,
                    model,
                    prompt_hash,
                    cache_hit,
                    outcome,
                    Jsonb(output) if output is not None else None,
                    latency_ms,
                ),
            )
            return int(require_row(cur)[0])

    def recent(self, project_id: int, role: str, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT event_id, item_id, role, model, prompt_hash, cache_hit,
                       outcome, output, latency_ms, created_at
                FROM analyzer.llm_event
                WHERE project_id = %s AND role = %s
                ORDER BY created_at DESC, event_id DESC
                LIMIT %s
                """,
                (project_id, role, limit),
            )
            return list(cur.fetchall())


class PgLlmRoleStateStore(StoreBase):
    def is_enabled(self, project_id: int, role: str) -> bool:
        """A role is enabled unless an ``llm_role_state`` row disables it (§6)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT enabled FROM analyzer.llm_role_state WHERE project_id = %s AND role = %s",
                (project_id, role),
            ).fetchone()
        return True if row is None else bool(row[0])

    def set_state(
        self,
        project_id: int,
        role: str,
        *,
        enabled: bool,
        reason: str | None = None,
        stats: dict | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO analyzer.llm_role_state
                    (project_id, role, enabled, reason, stats, decided_at)
                VALUES (%s,%s,%s,%s,%s, now())
                ON CONFLICT (project_id, role) DO UPDATE SET
                    enabled    = EXCLUDED.enabled,
                    reason     = EXCLUDED.reason,
                    stats      = EXCLUDED.stats,
                    decided_at = now()
                """,
                (project_id, role, enabled, reason, Jsonb(stats or {})),
            )

    def list_states(self, project_id: int) -> list[dict]:
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                "SELECT project_id, role, enabled, reason, stats, decided_at "
                "FROM analyzer.llm_role_state WHERE project_id = %s ORDER BY role",
                (project_id,),
            )
            return list(cur.fetchall())
