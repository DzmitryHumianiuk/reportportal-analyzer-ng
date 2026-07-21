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

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
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
    StoredSignature,
    SuggestionIn,
    TestItemIn,
)
from analyzer_ng.db.repositories.protocols import KBStore
from analyzer_ng.db.repositories.queries import (
    SEARCH_TI_HYBRID_SQL,
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
                 urls, paths, emb, emb_model_ver, error_log_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                emb_model_ver = EXCLUDED.emb_model_ver,
                -- keep a real log id if a re-index arrives without one; bare column =
                -- the existing target row (ON CONFLICT), EXCLUDED = the proposed row.
                error_log_id = COALESCE(EXCLUDED.error_log_id, failure_signature.error_log_id)
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
                    s.error_log_id,
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
            f"item_id IN (SELECT item_id FROM analyzer.test_item WHERE project_id=%s AND {bound})"
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
        """Purge a project's DERIVED data, preserving the ``label_event`` log.

        RP's project-wide "Generate index" is a delete->rebuild: it drives this
        same ``delete`` route before re-publishing every launch. So this must clear
        only data that reindexing regenerates and must KEEP ``label_event`` — the
        append-only learning log (spec 02 §2.7) that feeds GBM training and KB
        purity. Wiping it every reindex would silently destroy the project's
        learning history; the re-indexed items come back labeled via the index
        payload, but the training stream would be gone. ``label_event`` has no FK
        to ``project``, so events survive even the project-row delete below and
        re-attach naturally to the rebuilt items via their stable ``item_id`` (the
        item/launch/time-range deletes already preserve it — see ``_purge_items``).

        ``test_history_stats`` IS wiped: it is derived from indexing (window run/
        failure counters) and is rebuilt incrementally as the launches re-index,
        so keeping it would double-count. All other tables here are derived
        (signatures, templates, drain state, modes, suggestions, groups, caches,
        daily metrics) and regenerate on rebuild.

        Trade-off (deliberate): a genuine RP *project deletion* also uses this
        route, so it leaves orphaned ``label_event`` rows behind. That is harmless
        — the rows are project-scoped and queryable, never resurfaced for a
        deleted project — and is the accepted cost of not destroying learning
        history on the far more common reindex.
        """
        # Children first; the project row last. label_event is intentionally
        # absent (preserved); it has no FK to project so the project delete leaves
        # it in place.
        tables = (
            "mode_membership",
            "failure_mode",
            "log_template",
            "drain3_state",
            "failure_signature",
            "test_item",
            "suggestion",
            "launch_group",
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

    def search_ti_candidates(
        self,
        project_id: int,
        q: QuerySignature,
        k: int,
        filtered_launch_ids: Sequence[int],
        self_item_id: int,
    ) -> list[Candidate]:
        """Hybrid retrieval of similar still-uninvestigated (TI) items (spec §8.2).

        Serves the ``search`` route ("Similar 'To Investigate' in the launch"). Same
        lexical+dense RRF fusion as :meth:`find_candidates`' stage B, but the target
        set is TI-only, scoped to ``filtered_launch_ids`` (RP's ``filteredLaunchIds``;
        empty means no launch restriction) and with the query item excluded. Each
        returned :class:`Candidate` carries ``relevant_log_id`` — the matched item's
        real RP log id — so the reply's ``logId`` is one RP can load (a NULL becomes
        ``None`` here; the caller coalesces to 0).
        """
        qvec = halfvec_literal(q.emb if q.emb is not None else _ZERO_VEC)
        sql, params = bind_numbered(
            SEARCH_TI_HYBRID_SQL,
            [
                project_id,
                q.emb_model_ver,
                " ".join(q.salient_terms),
                qvec,
                " ".join(q.exception_names),
                bigint_array_literal(q.template_ids),
                k,
                bigint_array_literal(filtered_launch_ids),
                self_item_id,
            ],
        )
        with self._conn() as conn, conn.transaction():
            for stmt in SESSION_TUNING:
                conn.execute(stmt)
            rows = conn.execute(sql, params).fetchall()

        out: list[Candidate] = []
        for r in rows:
            (
                item_id,
                sparse_rank,
                dense_rank,
                lex_score,
                cosine,
                rrf_score,
                launch_id,
                launch_number,
                error_log_id,
            ) = r
            launch_distance = (
                abs(q.launch_number - launch_number)
                if q.launch_number is not None and launch_number is not None
                else None
            )
            out.append(
                Candidate(
                    item_id=item_id,
                    mode_id=None,
                    dense_rank=dense_rank,
                    sparse_rank=sparse_rank,
                    rrf_score=float(rrf_score),
                    cosine=cosine,
                    lex_score=lex_score,
                    launch_distance=launch_distance,
                    launch_id=launch_id,
                    relevant_log_id=error_log_id,
                )
            )
        return out

    def find_hash_matches(self, project_id: int, error_hash: int, limit: int = 10) -> list[dict]:
        """Stage-A exact-error_hash matches enriched with label provenance (spec §6.1).

        Returns labeled, non-``ti`` items sharing ``error_hash``, each carrying the
        source and timestamp of its most-recent ``label_event`` so the Stage-A
        guards (human-sourced / unanimous / age ≤ 180d / not auto-nd) can be
        evaluated. Newest first.
        """
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT ti.item_id, ti.issue_type, ti.issue_type_group, ti.is_auto_analyzed,
                       ti.launch_id, ti.launch_name,
                       fs.exception_fp, fs.status_codes, fs.msg_text,
                       le.source AS label_source, le.ts AS label_ts
                FROM analyzer.failure_signature fs
                JOIN analyzer.test_item ti USING (project_id, item_id)
                LEFT JOIN LATERAL (
                    SELECT source, ts FROM analyzer.label_event le
                    WHERE le.project_id = ti.project_id AND le.item_id = ti.item_id
                    ORDER BY le.ts DESC LIMIT 1
                ) le ON true
                WHERE fs.project_id = %s AND fs.error_hash = %s
                  AND ti.issue_type IS NOT NULL AND ti.issue_type_group <> 'ti'
                ORDER BY le.ts DESC NULLS LAST, ti.indexed_at DESC, ti.item_id DESC
                LIMIT %s
                """,
                (project_id, error_hash, limit),
            )
            rows = cur.fetchall()
        for r in rows:
            r["label_source"] = _LABEL_SOURCE_MAP.get(r["label_source"])
        return rows

    def get_signatures(
        self, project_id: int, item_ids: Sequence[int]
    ) -> dict[int, StoredSignature]:
        """Read persisted ``failure_signature`` identities by item id (spec §6.1).

        The canonical identity of an already-indexed item — its ``error_hash`` /
        ``exception_fp`` / ``template_ids`` / ``top_frames`` / ``status_codes`` /
        ``msg_text`` as written at index time (in that index's Drain3 state). The
        read path uses these instead of recomputing against the drifted read-only
        Drain clone, so every hash-identity comparison stays stored-vs-stored.
        Missing item ids are simply absent from the returned mapping (never indexed).
        """
        if not item_ids:
            return {}
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT project_id, item_id, exception_fp, error_hash, top_frames,
                       template_ids, exc_text, msg_text, status_codes, emb_model_ver
                FROM analyzer.failure_signature
                WHERE project_id = %s AND item_id = ANY(%s)
                """,
                (project_id, list(item_ids)),
            )
            rows = cur.fetchall()
        return {int(r["item_id"]): StoredSignature(**r) for r in rows}

    def test_case_first_seen(self, project_id: int, test_case_hash: int) -> datetime | None:
        """Earliest observation of a test case (feeds ``test_age_days``, §6.4).

        Uses ``start_time`` (the actual run time, stable across re-index) with
        ``indexed_at`` as a fallback; ``None`` when the test case is unseen.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT min(COALESCE(start_time, indexed_at)) FROM analyzer.test_item "
                "WHERE project_id=%s AND test_case_hash=%s",
                (project_id, test_case_hash),
            ).fetchone()
        return row[0] if row is not None else None

    def item_launch_names(self, project_id: int, item_ids: Sequence[int]) -> dict[int, str]:
        """launch_name per item — feeds analyzerMode name-based scope (spec §6.0)."""
        if not item_ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT item_id, launch_name FROM analyzer.test_item "
                "WHERE project_id=%s AND item_id = ANY(%s)",
                (project_id, list(item_ids)),
            ).fetchall()
        return {int(r[0]): r[1] or "" for r in rows}

    def error_hash_seen(self, project_id: int, error_hash: int, exclude_launch_id: int) -> bool:
        """True if this ``error_hash`` was recorded before, outside the given launch.

        Gates the burst ``si_prior`` (spec §5): a group whose representative failure
        already exists in history is *not* a new system issue.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM analyzer.failure_signature fs "
                "JOIN analyzer.test_item ti USING (project_id, item_id) "
                "WHERE fs.project_id=%s AND fs.error_hash=%s AND ti.launch_id <> %s LIMIT 1",
                (project_id, error_hash, exclude_launch_id),
            ).fetchone()
        return row is not None

    def write_suggestion(self, sug: SuggestionIn) -> int:
        """Persist a ``suggestion`` row (every decision writes one — spec §6.6)."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO analyzer.suggestion
                    (project_id, item_id, launch_id, group_id, predicted_label, confidence,
                     matched_mode_id, matched_item_id, features, model_ver, llm_used,
                     explanation, method, abstain_reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING suggestion_id
                """,
                (
                    sug.project_id,
                    sug.item_id,
                    sug.launch_id,
                    sug.group_id,
                    sug.predicted_label,
                    sug.confidence,
                    sug.matched_mode_id,
                    sug.matched_item_id,
                    Jsonb(sug.features),
                    sug.model_ver,
                    sug.llm_used,
                    sug.explanation,
                    sug.method,
                    sug.abstain_reason,
                ),
            )
            return int(require_row(cur)[0])

    def upsert_launch_group(
        self,
        project_id: int,
        launch_id: int,
        fingerprint: int,
        member_count: int,
        si_prior: float,
        dominant: bool,
    ) -> int:
        """Insert/refresh a ``launch_group`` row, keyed by (launch, fingerprint)."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO analyzer.launch_group
                    (project_id, launch_id, fingerprint, member_count, si_prior, dominant)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (project_id, launch_id, fingerprint) DO UPDATE SET
                    member_count = EXCLUDED.member_count,
                    si_prior     = EXCLUDED.si_prior,
                    dominant     = EXCLUDED.dominant
                RETURNING group_id
                """,
                (project_id, launch_id, fingerprint, member_count, si_prior, dominant),
            )
            return int(require_row(cur)[0])

    def latest_suggestions(self, project_id: int, item_ids: Sequence[int]) -> dict[int, dict]:
        """Most-recent ``suggestion`` row per item (the ``suggest`` read path).

        Returns a mapping item_id → row. Precomputed suggestions written by a prior
        ``analyze`` are served here without recomputation when present.
        """
        if not item_ids:
            return {}
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT DISTINCT ON (item_id)
                       item_id, group_id, predicted_label, confidence, matched_mode_id,
                       matched_item_id, features, model_ver
                FROM analyzer.suggestion
                WHERE project_id = %s AND item_id = ANY(%s)
                ORDER BY item_id, created_at DESC
                """,
                (project_id, list(item_ids)),
            )
            return {int(r["item_id"]): r for r in cur.fetchall()}

    def latest_rubric_provisional(self, project_id: int, item_id: int) -> dict | None:
        """The item's outstanding LLM cold-start rubric hypothesis, or ``None``
        (product ext 2026-07-20). Read-path source for the Make Decision rubric row.

        Returns a rubric provisional — ``model_ver`` ``rubric+…`` AND a non-empty
        ``explanation`` — only when it is the item's most recent *meaningful* decision.
        Classical **abstain** rows (``predicted_label='ti'`` written by a non-rubric
        model) are ignored: every ``suggest`` call persists one such abstain row for
        the very item it is answering *before* this read runs, so counting it would
        always mask the rubric. A genuine confident classical/auto answer
        (``predicted_label <> 'ti'``) that is newer than the rubric DOES win — it
        becomes the top row and, not being a ``rubric+`` row, yields ``None`` (the
        rubric hypothesis never displaces a real evidence-backed label).
        """
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT predicted_label, confidence, explanation, model_ver
                FROM analyzer.suggestion
                WHERE project_id = %s AND item_id = %s
                  AND NOT (predicted_label = 'ti' AND model_ver NOT LIKE 'rubric+%%')
                ORDER BY created_at DESC, suggestion_id DESC
                LIMIT 1
                """,
                (project_id, item_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        model_ver = row.get("model_ver") or ""
        if not model_ver.startswith("rubric+") or not (row.get("explanation") or "").strip():
            return None
        return row

    def latest_judge(self, project_id: int, item_id: int) -> dict | None:
        """The freshest judge verdict for an item (spec 04 §4.3 read-path surfacing).

        Returns ``features['judge']`` from the most recent judge-bearing suggestion
        within the 14-day judge TTL, so the suggest path can promote the chosen
        candidate even after newer (non-judge) suggestion rows were written. Scoped
        to the project; ``None`` when no fresh verdict exists.
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT features -> 'judge'
                FROM analyzer.suggestion
                WHERE project_id = %s AND item_id = %s AND features ? 'judge'
                  AND created_at >= now() - interval '14 days'
                ORDER BY created_at DESC, suggestion_id DESC
                LIMIT 1
                """,
                (project_id, item_id),
            ).fetchone()
        return row[0] if row is not None and row[0] is not None else None

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
            launch_id,
            launch_number,
            _is_auto,
            label_source,
            label_ts,
            mode_id,
            msg_text,
            exc_text,
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
            launch_id=launch_id,
            msg_text=msg_text or "",
            exc_text=exc_text or "",
        )
