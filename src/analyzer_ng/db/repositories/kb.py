"""KBStore — the failure-mode knowledge base (spec 02 §4.3, §5.2).

Stage-A of retrieval: exact exception-fingerprint overlap + centroid-cosine
match over ``failure_mode`` (hundreds of rows/project — always an exact scan,
never HNSW). Plus the mode lifecycle: spawn / add-members / recompute-purity /
merge / split / set-status / list.
"""

from __future__ import annotations

from collections.abc import Sequence

from psycopg.rows import dict_row

from analyzer_ng.db.repositories._common import (
    StoreBase,
    bigint_array_literal,
    halfvec_literal,
    parse_vector,
    python_jaccard,
    require_row,
)
from analyzer_ng.db.repositories.models import Candidate, MatchedBy, ModeIn, QuerySignature
from analyzer_ng.db.repositories.queries import STAGE_A_MODE_MATCH_SQL, bind_numbered

_DIMS = 384
_ZERO_VEC = [0.0] * _DIMS

# label_source on a mode is already one of the Candidate literals ('seed' etc.).
_MODE_LABEL_SOURCES = {"seed", "human", "ai_suggested", "rp"}


class PgKBStore(StoreBase):
    """psycopg3 implementation of :class:`~...protocols.KBStore`."""

    # --------------------------------------------------------------------- #
    # Stage A retrieval (spec 02 §5.2)
    # --------------------------------------------------------------------- #
    def match_modes(self, project_id: int, q: QuerySignature, k: int = 10) -> list[Candidate]:
        qvec = halfvec_literal(q.emb if q.emb is not None else _ZERO_VEC)
        fps = bigint_array_literal([q.exception_fp])
        sql, params = bind_numbered(
            STAGE_A_MODE_MATCH_SQL, [project_id, q.emb_model_ver, qvec, fps]
        )
        out: list[Candidate] = []
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        for (
            mode_id,
            status,
            label,
            label_source,
            purity,
            support,
            rep_templates,
            _title,
            cosine,
            fp_hit,
        ) in rows[:k]:
            src = label_source if label_source in _MODE_LABEL_SOURCES else None
            out.append(
                Candidate(
                    item_id=None,
                    mode_id=mode_id,
                    cosine=cosine,
                    jaccard_templates=python_jaccard(rep_templates or [], q.template_ids),
                    issue_type=label,
                    label_source=src,  # type: ignore[arg-type]
                    same_exception_fp=bool(fp_hit),
                    mode_status=status,
                    mode_purity=purity,
                    mode_support=support,
                    matched_by="hash" if fp_hit else "vector",
                )
            )
        return out

    # --------------------------------------------------------------------- #
    # Lifecycle
    # --------------------------------------------------------------------- #
    def spawn_candidate_mode(self, mode: ModeIn, seed_item_ids: Sequence[int]) -> int:
        centroid = halfvec_literal(mode.centroid) if mode.centroid is not None else None
        with self._conn() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO analyzer.project (project_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (mode.project_id,),
            )
            mode_id = require_row(
                conn.execute(
                    """
                    INSERT INTO analyzer.failure_mode
                        (project_id, status, label, label_source, title, summary,
                         centroid, emb_model_ver, representative_template_ids, exception_fps)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING mode_id
                    """,
                    (
                        mode.project_id,
                        mode.status,
                        mode.label,
                        mode.label_source,
                        mode.title,
                        mode.summary,
                        centroid,
                        mode.emb_model_ver,
                        mode.representative_template_ids,
                        mode.exception_fps,
                    ),
                )
            )[0]
            if seed_item_ids:
                conn.cursor().executemany(
                    """
                    INSERT INTO analyzer.mode_membership
                        (project_id, mode_id, item_id, match_score, matched_by)
                    VALUES (%s,%s,%s,1.0,'human')
                    ON CONFLICT (project_id, mode_id, item_id) DO NOTHING
                    """,
                    [(mode.project_id, mode_id, iid) for iid in seed_item_ids],
                )
        return int(mode_id)

    def add_members(
        self, project_id: int, mode_id: int, members: Sequence[tuple[int, float, MatchedBy]]
    ) -> int:
        if not members:
            return 0
        with self._conn() as conn:
            conn.cursor().executemany(
                """
                INSERT INTO analyzer.mode_membership
                    (project_id, mode_id, item_id, match_score, matched_by)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (project_id, mode_id, item_id)
                DO UPDATE SET match_score = EXCLUDED.match_score,
                             matched_by  = EXCLUDED.matched_by
                """,
                [(project_id, mode_id, iid, score, by) for (iid, score, by) in members],
            )
        return len(members)

    def update_purity(self, project_id: int, mode_id: int) -> float:
        """Recompute purity/support from members' current labels; EWMA the centroid.

        ``purity = max(label_count) / labeled_members``; ``support`` = labeled
        members. The centroid is nudged toward the mean of current-version member
        embeddings by ``ewma_alpha`` (skipped when no such embeddings exist).
        """
        with self._conn() as conn, conn.transaction():
            max_c, total = require_row(
                conn.execute(
                    """
                    WITH labeled AS (
                        SELECT ti.issue_type AS lbl
                        FROM analyzer.mode_membership mm
                        JOIN analyzer.test_item ti
                          ON ti.project_id = mm.project_id AND ti.item_id = mm.item_id
                        WHERE mm.project_id = %s AND mm.mode_id = %s AND ti.issue_type IS NOT NULL
                    ),
                    counts AS (SELECT lbl, count(*) AS c FROM labeled GROUP BY lbl)
                    SELECT COALESCE(max(c), 0), COALESCE(sum(c), 0) FROM counts
                    """,
                    (project_id, mode_id),
                )
            )
            purity = (max_c / total) if total else 0.0

            old_centroid, alpha, model_ver = require_row(
                conn.execute(
                    "SELECT centroid::text, ewma_alpha, emb_model_ver "
                    "FROM analyzer.failure_mode WHERE project_id=%s AND mode_id=%s",
                    (project_id, mode_id),
                )
            )
            new_centroid = self._ewma_centroid(
                conn, project_id, mode_id, old_centroid, alpha, model_ver
            )

            conn.execute(
                """
                UPDATE analyzer.failure_mode
                SET purity = %s, support = %s, centroid = %s,
                    updated_at = now(), last_seen_at = now()
                WHERE project_id = %s AND mode_id = %s
                """,
                (purity, int(total), new_centroid, project_id, mode_id),
            )
        return float(purity)

    @staticmethod
    def _ewma_centroid(conn, project_id, mode_id, old_text, alpha, model_ver) -> str | None:
        """Blend the old centroid toward current members' mean embedding."""
        if model_ver is None:
            return old_text
        mean_text = conn.execute(
            """
            SELECT avg(fs.emb)::text
            FROM analyzer.mode_membership mm
            JOIN analyzer.failure_signature fs
              ON fs.project_id = mm.project_id AND fs.item_id = mm.item_id
            WHERE mm.project_id = %s AND mm.mode_id = %s
              AND fs.emb IS NOT NULL AND fs.emb_model_ver = %s
            """,
            (project_id, mode_id, model_ver),
        ).fetchone()[0]
        mean = parse_vector(mean_text)
        if mean is None:
            return old_text
        old = parse_vector(old_text)
        if old is None:
            return halfvec_literal(mean)
        a = float(alpha)
        return halfvec_literal([(1 - a) * o + a * m for o, m in zip(old, mean, strict=True)])

    def merge_modes(self, project_id: int, src_mode_id: int, dst_mode_id: int) -> None:
        with self._conn() as conn, conn.transaction():
            dst_c, dst_sup, dst_ver = require_row(
                conn.execute(
                    "SELECT centroid::text, support, emb_model_ver "
                    "FROM analyzer.failure_mode WHERE project_id=%s AND mode_id=%s",
                    (project_id, dst_mode_id),
                )
            )
            src_c, src_sup, src_ver = require_row(
                conn.execute(
                    "SELECT centroid::text, support, emb_model_ver "
                    "FROM analyzer.failure_mode WHERE project_id=%s AND mode_id=%s",
                    (project_id, src_mode_id),
                )
            )
            new_centroid, new_ver = self._merged_centroid(
                dst_c, dst_sup, dst_ver, src_c, src_sup, src_ver
            )

            conn.execute(
                """
                INSERT INTO analyzer.mode_membership
                    (project_id, mode_id, item_id, match_score, matched_by, created_at)
                SELECT project_id, %s, item_id, match_score, matched_by, created_at
                FROM analyzer.mode_membership
                WHERE project_id = %s AND mode_id = %s
                ON CONFLICT (project_id, mode_id, item_id)
                DO UPDATE SET match_score = GREATEST(
                    analyzer.mode_membership.match_score, EXCLUDED.match_score)
                """,
                (dst_mode_id, project_id, src_mode_id),
            )
            conn.execute(
                "DELETE FROM analyzer.mode_membership WHERE project_id=%s AND mode_id=%s",
                (project_id, src_mode_id),
            )
            conn.execute(
                """
                UPDATE analyzer.failure_mode dst SET
                    exception_fps = ARRAY(SELECT DISTINCT unnest(
                        dst.exception_fps || src.exception_fps)),
                    representative_template_ids = ARRAY(SELECT DISTINCT unnest(
                        dst.representative_template_ids || src.representative_template_ids)),
                    support = dst.support + src.support,
                    centroid = %s::halfvec(384),
                    emb_model_ver = %s,
                    updated_at = now()
                FROM analyzer.failure_mode src
                WHERE dst.project_id = %s AND dst.mode_id = %s
                  AND src.project_id = %s AND src.mode_id = %s
                """,
                (new_centroid, new_ver, project_id, dst_mode_id, project_id, src_mode_id),
            )
            self._retire(conn, project_id, src_mode_id)

    @staticmethod
    def _merged_centroid(
        dst_text: str | None,
        dst_support: int,
        dst_ver: int | None,
        src_text: str | None,
        src_support: int,
        src_ver: int | None,
    ) -> tuple[str | None, int | None]:
        """Support-weighted centroid average for a mode merge (spec 02 §4.3).

        ``centroid = (dst*dst_support + src*src_support) / (dst_support+src_support)``.
        Versions are never mixed (CONTEXT §3): if the two centroids carry different
        ``emb_model_ver`` the destination's centroid/version is kept. A missing
        centroid contributes nothing; zero total support falls back to equal weight.
        """
        dst_vec, src_vec = parse_vector(dst_text), parse_vector(src_text)
        if dst_vec is None and src_vec is None:
            return None, dst_ver
        if dst_vec is None:
            return halfvec_literal(src_vec), src_ver  # type: ignore[arg-type]
        if src_vec is None:
            return halfvec_literal(dst_vec), dst_ver
        if dst_ver != src_ver:
            return halfvec_literal(dst_vec), dst_ver  # do not blend across versions
        wd, ws = max(dst_support, 0), max(src_support, 0)
        if wd + ws == 0:
            wd = ws = 1
        total = wd + ws
        blended = [(d * wd + s * ws) / total for d, s in zip(dst_vec, src_vec, strict=True)]
        return halfvec_literal(blended), dst_ver

    def split_mode(
        self, project_id: int, mode_id: int, partition: dict[int, list[int]]
    ) -> list[int]:
        new_ids: list[int] = []
        with self._conn() as conn, conn.transaction():
            row = require_row(
                conn.execute(
                    "SELECT label, label_source, centroid::text, emb_model_ver, "
                    "representative_template_ids, exception_fps "
                    "FROM analyzer.failure_mode WHERE project_id=%s AND mode_id=%s",
                    (project_id, mode_id),
                )
            )
            label, label_source, centroid_text, model_ver, rep_templates, exc_fps = row
            for _ordinal in sorted(partition):
                item_ids = partition[_ordinal]
                new_id = require_row(
                    conn.execute(
                        """
                        INSERT INTO analyzer.failure_mode
                            (project_id, status, label, label_source, centroid, emb_model_ver,
                             representative_template_ids, exception_fps)
                        VALUES (%s,'candidate',%s,%s,%s,%s,%s,%s)
                        RETURNING mode_id
                        """,
                        (
                            project_id,
                            label,
                            label_source,
                            centroid_text,
                            model_ver,
                            rep_templates,
                            exc_fps,
                        ),
                    )
                )[0]
                if item_ids:
                    conn.cursor().executemany(
                        """
                        INSERT INTO analyzer.mode_membership
                            (project_id, mode_id, item_id, match_score, matched_by)
                        VALUES (%s,%s,%s,1.0,'human')
                        ON CONFLICT (project_id, mode_id, item_id) DO NOTHING
                        """,
                        [(project_id, new_id, iid) for iid in item_ids],
                    )
                new_ids.append(int(new_id))
            self._retire(conn, project_id, mode_id)
        return new_ids

    def set_status(
        self,
        project_id: int,
        mode_id: int,
        status: str,
        label: str | None = None,
        label_source: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE analyzer.failure_mode
                SET status = %s,
                    label = COALESCE(%s, label),
                    label_source = COALESCE(%s, label_source),
                    updated_at = now()
                WHERE project_id = %s AND mode_id = %s
                """,
                (status, label, label_source, project_id, mode_id),
            )

    def list_modes(self, project_id: int, statuses: Sequence[str] | None = None) -> list[dict]:
        clause = ""
        params: list[object] = [project_id]
        if statuses:
            clause = " AND status = ANY(%s)"
            params.append(list(statuses))
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                "SELECT project_id, mode_id, status, label, label_source, title, summary, "
                "purity, support, emb_model_ver, representative_template_ids, exception_fps, "
                "created_at, updated_at, last_seen_at "
                "FROM analyzer.failure_mode "
                f"WHERE project_id = %s{clause} ORDER BY mode_id",
                params,
            )
            return cur.fetchall()

    @staticmethod
    def _retire(conn, project_id: int, mode_id: int) -> None:
        conn.execute(
            "UPDATE analyzer.failure_mode SET status='retired', updated_at=now() "
            "WHERE project_id=%s AND mode_id=%s",
            (project_id, mode_id),
        )
