"""LlmCacheStore — per-project LLM output cache (spec 02 §2.11).

``project_id`` is part of the primary key, so a cache row is *never* served
cross-project (CONTEXT §6: no cross-project leakage, including LLM prompt caches).
"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from analyzer_ng.db.repositories._common import StoreBase


class PgLlmCacheStore(StoreBase):
    """psycopg3 implementation of :class:`~...protocols.LlmCacheStore`."""

    def get(self, project_id: int, cache_key: str) -> dict | None:
        """Return the cached output for ``(project_id, cache_key)``, bumping hits."""
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE analyzer.llm_cache
                SET hits = hits + 1, last_hit_at = now()
                WHERE project_id = %s AND cache_key = %s
                RETURNING output
                """,
                (project_id, cache_key),
            ).fetchone()
        return row[0] if row is not None else None

    def get_fresh(self, project_id: int, cache_key: str, ttl_days: int) -> dict | None:
        """Read-time freshness (spec 04 §3.0): return the entry only if it is
        younger than ``ttl_days`` on ``created_at``; bump hits on a fresh hit.

        A stale row is left in place for the spec 02 §6 retention sweep to reap; it
        simply misses here so the role recomputes."""
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE analyzer.llm_cache
                SET hits = hits + 1, last_hit_at = now()
                WHERE project_id = %s AND cache_key = %s
                  AND created_at >= now() - make_interval(days => %s)
                RETURNING output
                """,
                (project_id, cache_key, ttl_days),
            ).fetchone()
        return row[0] if row is not None else None

    def get_extractor_by_template(
        self, project_id: int, template_hash: int, ttl_days: int
    ) -> dict | None:
        """Feature-time extractor lookup by ``(project_id, 'extractor', template_hash)``.

        Spec 04 §4.2: the GBM feature extractor reads the cached extractor output for
        a template-set (index ``llmc_tmpl_idx``). Read-only (no ``hits`` bump — this
        is a serving read, not a role call) and freshness-gated on ``created_at``.
        Always filters by ``project_id`` — never served cross-project (§5.4).
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT output FROM analyzer.llm_cache
                WHERE project_id = %s AND role = 'extractor' AND template_hash = %s
                  AND created_at >= now() - make_interval(days => %s)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (project_id, template_hash, ttl_days),
            ).fetchone()
        return row[0] if row is not None else None

    def put(
        self,
        project_id: int,
        cache_key: str,
        role: str,
        model: str,
        output: dict,
        template_hash: int | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO analyzer.llm_cache
                    (project_id, cache_key, role, template_hash, output, model)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (project_id, cache_key) DO UPDATE SET
                    role          = EXCLUDED.role,
                    template_hash = EXCLUDED.template_hash,
                    output        = EXCLUDED.output,
                    model         = EXCLUDED.model,
                    -- The row was just regenerated, so its freshness clock restarts.
                    -- Without this the old created_at survives the rewrite and the
                    -- entry can never be fresh again: every read misses on TTL,
                    -- rewrites the same stale timestamp, and the role regenerates
                    -- forever. It bites hardest on the short negative TTL (7 days,
                    -- issue #7), which would stop suppressing calls after one week.
                    created_at    = now(),
                    last_hit_at   = now()
                """,
                (project_id, cache_key, role, template_hash, Jsonb(output), model),
            )
