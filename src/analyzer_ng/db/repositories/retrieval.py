"""RetrievalStore — ingest writes, deletes, and hybrid candidate retrieval.

Owns ``test_item`` + ``failure_signature`` (partitioned hot tables) plus the
two-stage retrieval of spec 02 §4.2: stage A = KB modes (delegated to
:class:`PgKBStore`), stage B = the verbatim item-history hybrid SQL (§5.1)
fused with RRF k=60 in one round-trip. Derived booleans and ``filters.exclude_*``
are applied in Python; time-decay/label-confidence weighting is the decision
layer's job, not this store's.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from psycopg_pool import ConnectionPool

from analyzer_ng.db.repositories._common import (
    StoreBase,
    bigint_array_literal,
    halfvec_literal,
    require_row,
)
from analyzer_ng.db.repositories.kb import PgKBStore
from analyzer_ng.db.repositories.models import (
    Candidate,
    CandidateFilters,
    QuerySignature,
    SignatureIn,
    TestItemIn,
)
from analyzer_ng.db.repositories.protocols import KBStore
from analyzer_ng.db.repositories.queries import (
    SESSION_TUNING,
    STAGE_B_HYBRID_SQL,
    bind_numbered,
)

_DIMS = 384
_ZERO_VEC = [0.0] * _DIMS

# label_event.source -> Candidate.label_source (spec 02 §2.7 vs §4.1 vocabularies).
_LABEL_SOURCE_MAP = {
    "rp_defect_update": "rp",
    "human_ui": "human",
    "analyzer_suggestion_accepted": "ai_suggested",
}


class PgRetrievalStore(StoreBase):
    """psycopg3 implementation of :class:`~...protocols.RetrievalStore`."""

    def __init__(self, pool: ConnectionPool, kb: KBStore | None = None) -> None:
        super().__init__(pool)
        self._kb: KBStore = kb if kb is not None else PgKBStore(pool)

    # --------------------------------------------------------------------- #
    # Writes
    # --------------------------------------------------------------------- #
    def upsert_items(self, items: Sequence[TestItemIn], *, conn: object | None = None) -> int:
        """Upsert test items. Pass ``conn`` to enlist in a caller's transaction
        (spec 02 §2.10 atomic index write); otherwise runs in its own."""
        if not items:
            return 0
        if conn is not None:
            self._upsert_items(conn, items)
        else:
            with self._conn() as own, own.transaction():
                self._upsert_items(own, items)
        return len(items)

    @staticmethod
    def _upsert_items(conn: Any, items: Sequence[TestItemIn]) -> None:
        cur = conn.cursor()
        cur.executemany(
            "INSERT INTO analyzer.project (project_id) VALUES (%s) ON CONFLICT DO NOTHING",
            [(pid,) for pid in {it.project_id for it in items}],
        )
        cur.executemany(
            """
            INSERT INTO analyzer.test_item
                (project_id, item_id, launch_id, launch_name, launch_number,
                 test_case_hash, unique_id, item_name, start_time, is_auto_analyzed,
                 issue_type, log_count, log_time_max, indexed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (project_id, item_id) DO UPDATE SET
                launch_id        = EXCLUDED.launch_id,
                launch_name      = EXCLUDED.launch_name,
                launch_number    = EXCLUDED.launch_number,
                test_case_hash   = EXCLUDED.test_case_hash,
                unique_id        = EXCLUDED.unique_id,
                item_name        = EXCLUDED.item_name,
                start_time       = EXCLUDED.start_time,
                is_auto_analyzed = EXCLUDED.is_auto_analyzed,
                issue_type       = EXCLUDED.issue_type,
                log_count        = EXCLUDED.log_count,
                log_time_max     = EXCLUDED.log_time_max,
                indexed_at       = now()
            """,
            [
                (
                    it.project_id,
                    it.item_id,
                    it.launch_id,
                    it.launch_name,
                    it.launch_number,
                    it.test_case_hash,
                    it.unique_id,
                    it.item_name,
                    it.start_time,
                    it.is_auto_analyzed,
                    it.issue_type,
                    it.log_count,
                    it.log_time_max,
                )
                for it in items
            ],
        )

    def upsert_signatures(self, sigs: Sequence[SignatureIn], *, conn: object | None = None) -> int:
        """Upsert failure signatures. Pass ``conn`` to enlist in a caller's
        transaction (spec 02 §2.10 atomic index write)."""
        if not sigs:
            return 0
        if conn is not None:
            self._upsert_signatures(conn, sigs)
        else:
            with self._conn() as own, own.transaction():
                self._upsert_signatures(own, sigs)
        return len(sigs)

    @staticmethod
    def _upsert_signatures(conn: Any, sigs: Sequence[SignatureIn]) -> None:
        conn.cursor().executemany(
            """
            INSERT INTO analyzer.failure_signature
                (project_id, item_id, exception_fp, error_hash, top_frames, template_ids,
                 exc_text, msg_text, frames_text, tmpl_text, only_numbers, status_codes,
                 urls, paths, emb, emb_model_ver)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (project_id, item_id) DO UPDATE SET
                exception_fp = EXCLUDED.exception_fp,
                error_hash   = EXCLUDED.error_hash,
                top_frames   = EXCLUDED.top_frames,
                template_ids = EXCLUDED.template_ids,
                exc_text     = EXCLUDED.exc_text,
                msg_text     = EXCLUDED.msg_text,
                frames_text  = EXCLUDED.frames_text,
                tmpl_text    = EXCLUDED.tmpl_text,
                only_numbers = EXCLUDED.only_numbers,
                status_codes = EXCLUDED.status_codes,
                urls         = EXCLUDED.urls,
                paths        = EXCLUDED.paths,
                emb          = EXCLUDED.emb,
                emb_model_ver = EXCLUDED.emb_model_ver
            """,
            [
                (
                    s.project_id,
                    s.item_id,
                    s.exception_fp,
                    s.error_hash,
                    s.top_frames,
                    s.template_ids,
                    s.exc_text,
                    s.msg_text,
                    s.frames_text,
                    s.tmpl_text,
                    s.only_numbers,
                    s.status_codes,
                    s.urls,
                    s.paths,
                    halfvec_literal(s.emb) if s.emb is not None else None,
                    s.emb_model_ver,
                )
                for s in sigs
            ],
        )

    def update_issue_type(
        self, project_id: int, item_id: int, issue_type: str | None, is_auto: bool
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE analyzer.test_item SET issue_type=%s, is_auto_analyzed=%s "
                "WHERE project_id=%s AND item_id=%s",
                (issue_type, is_auto, project_id, item_id),
            )
            return cur.rowcount > 0

    # --------------------------------------------------------------------- #
    # Deletes
    # --------------------------------------------------------------------- #
    def delete_items(self, project_id: int, item_ids: Sequence[int]) -> int:
        if not item_ids:
            return 0
        ids = list(item_ids)
        with self._conn() as conn, conn.transaction():
            self._purge_items(conn, project_id, "item_id = ANY(%s)", (project_id, ids))
            cur = conn.execute(
                "DELETE FROM analyzer.test_item WHERE project_id=%s AND item_id = ANY(%s)",
                (project_id, ids),
            )
            return cur.rowcount

    def delete_launches(self, project_id: int, launch_ids: Sequence[int]) -> int:
        if not launch_ids:
            return 0
        ids = list(launch_ids)
        subq = (
            "item_id IN (SELECT item_id FROM analyzer.test_item "
            "WHERE project_id=%s AND launch_id = ANY(%s))"
        )
        with self._conn() as conn, conn.transaction():
            self._purge_items(conn, project_id, subq, (project_id, project_id, ids))
            conn.execute(
                "DELETE FROM analyzer.suggestion WHERE project_id=%s AND launch_id = ANY(%s)",
                (project_id, ids),
            )
            cur = conn.execute(
                "DELETE FROM analyzer.test_item WHERE project_id=%s AND launch_id = ANY(%s)",
                (project_id, ids),
            )
            return cur.rowcount

    def delete_by_time_range(
        self,
        project_id: int,
        field: Literal["start_time", "log_time"],
        before: datetime,
        after: datetime | None = None,
    ) -> int:
        """Delete items whose ``field`` is ``< before`` (and ``>= after`` if given).

        ``after`` implements the ``remove_by_*`` route's half-open window
        ``[interval_start_date, interval_end_date)`` (spec 01 §4.4); omitting it
        keeps the legacy retention semantics (everything before ``before``).
        """
        column = "start_time" if field == "start_time" else "log_time_max"
        bound = f"{column} IS NOT NULL AND {column} < %s"
        del_params: tuple = (project_id, before)
        if after is not None:
            bound += f" AND {column} >= %s"
            del_params = (project_id, before, after)
        subq = (
            "item_id IN (SELECT item_id FROM analyzer.test_item "
            f"WHERE project_id=%s AND {bound})"
        )
        with self._conn() as conn, conn.transaction():
            # _purge_items prefixes the predicate with its own `project_id=%s`, so the
            # subquery's own project_id (+ the bound params) follow it.
            self._purge_items(conn, project_id, subq, (project_id, *del_params))
            cur = conn.execute(
                f"DELETE FROM analyzer.test_item WHERE project_id=%s AND {bound}",
                del_params,
            )
            return cur.rowcount

    def get_items_labels(self, project_id: int, item_ids: Sequence[int]) -> dict[int, str | None]:
        """Current ``issue_type`` for each *existing* item (defect_update / feedback).

        Absence from the returned mapping means the item is unknown to
        analyzer-ng — the caller reports it as not-updated (spec 01 §4.5 step 3).
        """
        if not item_ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT item_id, issue_type FROM analyzer.test_item "
                "WHERE project_id=%s AND item_id = ANY(%s)",
                (project_id, list(item_ids)),
            ).fetchall()
        return {int(r[0]): r[1] for r in rows}

    def record_feedback_outcome(self, project_id: int, item_id: int, new_label: str) -> int:
        """Resolve pending suggestions for an item after human feedback (spec §4.5).

        A pending suggestion is ``accepted`` when its predicted base issue-type
        group matches the human label, else ``corrected``. Returns rows updated.
        """
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE analyzer.suggestion SET
                    outcome = CASE
                        WHEN substring(predicted_label from '^[a-z]+')
                             = substring(%s from '^[a-z]+') THEN 'accepted'
                        ELSE 'corrected' END,
                    outcome_ts = now()
                WHERE project_id=%s AND item_id=%s AND outcome='pending'
                """,
                (new_label, project_id, item_id),
            )
            return cur.rowcount

    def delete_project(self, project_id: int) -> int:
        # Children first; the project row (with its FK cascades) last.
        tables = (
            "mode_membership",
            "failure_mode",
            "log_template",
            "drain3_state",
            "failure_signature",
            "test_item",
            "suggestion",
            "launch_group",
            "label_event",
            "test_history_stats",
            "llm_cache",
            "metrics_daily",
        )
        with self._conn() as conn, conn.transaction():
            count = require_row(
                conn.execute(
                    "SELECT count(*) FROM analyzer.test_item WHERE project_id=%s", (project_id,)
                )
            )[0]
            for table in tables:
                conn.execute(f"DELETE FROM analyzer.{table} WHERE project_id=%s", (project_id,))
            conn.execute("DELETE FROM analyzer.project WHERE project_id=%s", (project_id,))
        return int(count)

    @staticmethod
    def _purge_items(conn, project_id: int, predicate: str, params: tuple) -> None:
        """Delete a set of items' membership/signature/suggestion rows.

        ``predicate`` selects items within the project; ``label_event`` is left
        intact (it is the append-only training log, spec 02 §2.7).
        """
        for table in ("mode_membership", "failure_signature", "suggestion"):
            conn.execute(
                f"DELETE FROM analyzer.{table} WHERE project_id=%s AND {predicate}", params
            )

    # --------------------------------------------------------------------- #
    # Retrieval
    # --------------------------------------------------------------------- #
    def find_by_error_hash(
        self, project_id: int, error_hash: int, limit: int = 5
    ) -> list[Candidate]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT fs.item_id, fs.error_hash, fs.exception_fp, fs.template_ids,
                       ti.issue_type, ti.test_case_hash, ti.launch_id, ti.launch_number
                FROM analyzer.failure_signature fs
                JOIN analyzer.test_item ti USING (project_id, item_id)
                WHERE fs.project_id=%s AND fs.error_hash=%s
                  AND ti.issue_type IS NOT NULL AND ti.issue_type_group <> 'ti'
                ORDER BY ti.indexed_at DESC, fs.item_id DESC
                LIMIT %s
                """,
                (project_id, error_hash, limit),
            ).fetchall()
        return [
            Candidate(
                item_id=r[0],
                mode_id=None,
                issue_type=r[4],
                same_error_hash=True,
                matched_by="hash",
            )
            for r in rows
        ]

    def find_candidates(
        self,
        project_id: int,
        q: QuerySignature,
        k: int = 20,
        filters: CandidateFilters | None = None,
    ) -> list[Candidate]:
        filters = filters or CandidateFilters()
        stage_a = self._kb.match_modes(project_id, q, k=10)
        stage_b = self._stage_b(project_id, q, k, filters)
        return (stage_a + stage_b)[: k + 10]

    def _stage_b(
        self, project_id: int, q: QuerySignature, k: int, filters: CandidateFilters
    ) -> list[Candidate]:
        qvec = halfvec_literal(q.emb if q.emb is not None else _ZERO_VEC)
        sql, params = bind_numbered(
            STAGE_B_HYBRID_SQL,
            [
                project_id,
                q.emb_model_ver,
                " ".join(q.salient_terms),
                qvec,
                " ".join(q.exception_names),
                bigint_array_literal(q.template_ids),
                k,
            ],
        )
        with self._conn() as conn, conn.transaction():
            for stmt in SESSION_TUNING:
                conn.execute(stmt)
            rows = conn.execute(sql, params).fetchall()

        exclude_items = set(filters.exclude_item_ids)
        exclude_launches = set(filters.exclude_launch_ids)
        groups = set(filters.issue_type_groups) if filters.issue_type_groups else None
        out: list[Candidate] = []
        for r in rows:
            item_id, launch_id, issue_type, label_ts = r[0], r[12], r[10], r[16]
            if item_id in exclude_items or launch_id in exclude_launches:
                continue
            if filters.min_label_ts is not None and (
                label_ts is None or label_ts < filters.min_label_ts
            ):
                continue
            if groups is not None and (issue_type or "")[:2] not in groups:
                continue
            out.append(self._row_to_candidate(r, q))
        return out

    @staticmethod
    def _row_to_candidate(r: tuple, q: QuerySignature) -> Candidate:
        (
            item_id,
            sparse_rank,
            dense_rank,
            lex_score,
            cosine,
            rrf_score,
            jaccard,
            error_hash,
            exception_fp,
            _template_ids,
            issue_type,
            test_case_hash,
            _launch_id,
            launch_number,
            _is_auto,
            label_source,
            label_ts,
            mode_id,
        ) = r
        launch_distance = (
            abs(q.launch_number - launch_number)
            if q.launch_number is not None and launch_number is not None
            else None
        )
        return Candidate(
            item_id=item_id,
            mode_id=mode_id,
            dense_rank=dense_rank,
            sparse_rank=sparse_rank,
            rrf_score=float(rrf_score),
            cosine=cosine,
            lex_score=lex_score,
            jaccard_templates=float(jaccard),
            issue_type=issue_type,
            label_source=_LABEL_SOURCE_MAP.get(label_source),  # type: ignore[arg-type]
            label_ts=label_ts,
            same_test_case=q.test_case_hash is not None and q.test_case_hash == test_case_hash,
            same_error_hash=error_hash == q.error_hash,
            same_exception_fp=exception_fp == q.exception_fp,
            launch_distance=launch_distance,
        )
