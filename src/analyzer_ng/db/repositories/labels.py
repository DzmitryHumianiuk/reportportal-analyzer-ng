"""LabelStore — the append-only label-event log + GBM training frame (spec 02 §2.7, §4.4)."""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from analyzer_ng.db.repositories._common import StoreBase, require_row
from analyzer_ng.db.repositories.models import LabelEventIn


class PgLabelStore(StoreBase):
    """psycopg3 implementation of :class:`~...protocols.LabelStore`."""

    def append_event(self, ev: LabelEventIn) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO analyzer.label_event
                    (project_id, item_id, old_label, new_label, source, suggestion_id)
                VALUES (%s,%s,%s,%s,%s,%s)
                RETURNING event_id
                """,
                (
                    ev.project_id,
                    ev.item_id,
                    ev.old_label,
                    ev.new_label,
                    ev.source,
                    ev.suggestion_id,
                ),
            )
            return int(require_row(cur)[0])

    def count_events_since(
        self, since: datetime | None = None, project_id: int | None = None
    ) -> int:
        """Number of label_events after ``since`` (the retrain counter, spec §6.5).

        ``since=None`` counts every event (used before the first model exists).
        The retrain trigger fires at ``N=100`` new events per install; this is the
        cheap check run on every ``defect_update`` ingestion.
        """
        clauses: list[str] = []
        params: list[object] = []
        if since is not None:
            clauses.append("ts > %s")
            params.append(since)
        if project_id is not None:
            clauses.append("project_id = %s")
            params.append(project_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as conn:
            cur = conn.execute(
                f"SELECT count(*) FROM analyzer.label_event{where}", params
            )
            return int(require_row(cur)[0])

    def fetch_training_frame(
        self,
        project_id: int | None = None,
        since: datetime | None = None,
        limit: int = 500_000,
    ) -> list[dict]:
        """Joined rows for GBM training (spec 02 §4.4).

        Each ``label_event`` is enriched with the item's most-recent suggestion
        features, its test-history stats, and the purity/support of the mode it
        belongs to. ``project_id=None`` returns install-wide rows (the model is
        install-wide, calibration per-project — CONTEXT §2.6).
        """
        clauses: list[str] = []
        params: list[object] = []
        if project_id is not None:
            clauses.append("le.project_id = %s")
            params.append(project_id)
        if since is not None:
            clauses.append("le.ts >= %s")
            params.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                f"""
                SELECT le.event_id, le.project_id, le.item_id, le.old_label, le.new_label,
                       le.source, le.ts,
                       ti.test_case_hash, ti.launch_id, ti.issue_type_group,
                       sg.confidence, sg.predicted_label, sg.features, sg.model_ver,
                       ths.window_runs, ths.window_failures, ths.flakiness_score,
                       fm.purity AS mode_purity, fm.support AS mode_support
                FROM analyzer.label_event le
                LEFT JOIN analyzer.test_item ti
                       ON ti.project_id = le.project_id AND ti.item_id = le.item_id
                LEFT JOIN LATERAL (
                    SELECT confidence, predicted_label, features, model_ver
                    FROM analyzer.suggestion sg
                    WHERE sg.project_id = le.project_id AND sg.item_id = le.item_id
                      AND sg.created_at <= le.ts
                    ORDER BY sg.created_at DESC LIMIT 1
                ) sg ON true
                LEFT JOIN analyzer.test_history_stats ths
                       ON ths.project_id = le.project_id
                      AND ths.test_case_hash = ti.test_case_hash
                LEFT JOIN LATERAL (
                    SELECT fm.purity, fm.support
                    FROM analyzer.mode_membership mm
                    JOIN analyzer.failure_mode fm
                      ON fm.project_id = mm.project_id AND fm.mode_id = mm.mode_id
                    WHERE mm.project_id = le.project_id AND mm.item_id = le.item_id
                    ORDER BY mm.match_score DESC LIMIT 1
                ) fm ON true
                {where}
                ORDER BY le.ts DESC
                LIMIT %s
                """,
                params,
            )
            return cur.fetchall()
