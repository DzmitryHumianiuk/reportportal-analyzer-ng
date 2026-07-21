"""Concrete ``suggestion`` row-updates + fact reads for the LLM sidecar (spec 04 §4).

Two small, project-scoped surfaces the async LLM worker uses (never the suggest
RPC path — those replies are already committed when these run, §0):

* :class:`PgSuggestionOps` implements the :class:`~analyzer_ng.llm.apply.SuggestionOps`
  port — the exact ``UPDATE``/``INSERT`` clauses spec 04 §4 makes load-bearing:
  explainer fills ``explanation`` + ``llm_used``; the judge annotates
  ``features['judge']`` + ``llm_used`` without touching ``predicted_label``/
  ``confidence``; cold-start inserts a suggest-band AI suggestion.
* :class:`PgLlmFacts` re-reads the evidence facts by ``(project_id, item_id)`` so
  the worker never trusts a stale enqueue payload (§1.5). Every query filters by
  ``project_id`` — cross-project evidence is never assembled (§5.4).
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from analyzer_ng.db.repositories._common import StoreBase, require_row


class PgSuggestionOps(StoreBase):
    """psycopg3 implementation of the :class:`SuggestionOps` port (spec 04 §4)."""

    def set_explanation(self, project_id: int, suggestion_id: int, explanation: str) -> None:
        """§4.1 row update: ``explanation=$1, llm_used=true`` on the shown suggestion."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE analyzer.suggestion SET explanation=%s, llm_used=true "
                "WHERE project_id=%s AND suggestion_id=%s",
                (explanation, project_id, suggestion_id),
            )

    def annotate_judge(
        self,
        project_id: int,
        item_id: int,
        *,
        chosen_item_id: int | None,
        features_patch: dict[str, Any],
    ) -> None:
        """§4.3 row update: merge ``features['judge']`` (carrying ``chosen_item_id``)
        + ``llm_used`` onto the item's latest suggestion. Never touches
        ``predicted_label``/``confidence``/band.

        The stored verdict is how the suggest read path surfaces the reorder: it
        reads the freshest judge-bearing suggestion for the item and promotes the
        chosen candidate to ``resultPosition`` 0 (suggest-band only). ``chosen_item_id``
        is carried inside ``features_patch``; the explicit arg documents the contract.
        """
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE analyzer.suggestion AS s
                SET features = COALESCE(s.features, '{}'::jsonb) || %s::jsonb,
                    llm_used = true
                WHERE s.project_id = %s AND s.suggestion_id = (
                    SELECT suggestion_id FROM analyzer.suggestion
                    WHERE project_id = %s AND item_id = %s
                    ORDER BY created_at DESC, suggestion_id DESC LIMIT 1
                )
                """,
                (Jsonb(features_patch), project_id, project_id, item_id),
            )

    def insert_coldstart(
        self,
        *,
        project_id: int,
        item_id: int,
        launch_id: int,
        predicted_label: str,
        confidence: float,
        model_ver: str,
        features: dict[str, Any],
    ) -> int:
        """§4.4 row update: insert a suggest-band AI suggestion (``llm_used=true``)."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO analyzer.suggestion
                    (project_id, item_id, launch_id, predicted_label, confidence,
                     features, model_ver, llm_used)
                VALUES (%s,%s,%s,%s,%s,%s,%s, true)
                RETURNING suggestion_id
                """,
                (
                    project_id,
                    item_id,
                    launch_id,
                    predicted_label,
                    confidence,
                    Jsonb(features),
                    model_ver,
                ),
            )
            return int(require_row(cur)[0])


class PgLlmFacts(StoreBase):
    """Reads the DB-derived evidence a role prompt needs (spec 04 §3.1)."""

    def load_signature(self, project_id: int, item_id: int) -> dict | None:
        """The failure_signature + test_item row for an item, or ``None`` if gone."""
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT fs.exception_fp, fs.error_hash, fs.top_frames, fs.template_ids,
                       fs.exc_text, fs.msg_text, fs.frames_text, fs.status_codes,
                       ti.test_case_hash, ti.launch_id
                FROM analyzer.failure_signature fs
                JOIN analyzer.test_item ti USING (project_id, item_id)
                WHERE fs.project_id = %s AND fs.item_id = %s
                """,
                (project_id, item_id),
            )
            return cur.fetchone()

    def hash_pool(
        self, project_id: int, error_hash: int, exclude_item_id: int, limit: int = 10
    ) -> list[dict]:
        """Labeled (non-``ti``) items sharing ``error_hash`` — the conflict pool the
        abstain explainer narrates (extension 2026-07-20).

        Mirrors :meth:`PgRetrievalStore.find_hash_matches` label semantics but is
        available to the async LLM worker (§1.5) and, crucially, is queried
        independently of ``exception_fp``: item 4998's pb-vs-ab conflict lives in this
        pool even though ``exception_fp=0`` makes Stage A itself unavailable. Read-only,
        project-scoped, newest-label first. Returns ``[]`` when nothing labeled shares
        the hash (a genuinely pure-empty abstain — no LLM call is then made)."""
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT ti.issue_type, ti.issue_type_group, le.source AS label_source
                FROM analyzer.failure_signature fs
                JOIN analyzer.test_item ti USING (project_id, item_id)
                LEFT JOIN LATERAL (
                    SELECT source FROM analyzer.label_event le
                    WHERE le.project_id = ti.project_id AND le.item_id = ti.item_id
                    ORDER BY le.ts DESC LIMIT 1
                ) le ON true
                WHERE fs.project_id = %s AND fs.error_hash = %s AND ti.item_id <> %s
                  AND ti.issue_type IS NOT NULL AND ti.issue_type_group <> 'ti'
                ORDER BY le.ts DESC NULLS LAST, ti.item_id DESC
                LIMIT %s
                """,
                (project_id, error_hash, exclude_item_id, limit),
            )
            return cur.fetchall()

    def latest_suggestion(self, project_id: int, item_id: int) -> dict | None:
        """The item's most-recent suggestion (the explainer/judge target row)."""
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT suggestion_id, predicted_label, confidence, matched_mode_id,
                       matched_item_id, features
                FROM analyzer.suggestion
                WHERE project_id = %s AND item_id = %s
                ORDER BY created_at DESC, suggestion_id DESC LIMIT 1
                """,
                (project_id, item_id),
            )
            return cur.fetchone()
