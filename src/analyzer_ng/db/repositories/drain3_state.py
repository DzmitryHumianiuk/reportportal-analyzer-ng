"""Drain3StateStore — per-project miner state (CAS) + queryable template mirror (spec 02 §2.3)."""

from __future__ import annotations

from collections.abc import Sequence

from psycopg.types.json import Jsonb

from analyzer_ng.db.repositories._common import StoreBase


class PgDrain3StateStore(StoreBase):
    """psycopg3 implementation of :class:`~...protocols.Drain3StateStore`."""

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
