"""StatsStore — per-test failure/flakiness history + daily metrics (spec 02 §2.10, §2.12, §4.4)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from analyzer_ng.db.repositories._common import StoreBase, require_row


class PgStatsStore(StoreBase):
    """psycopg3 implementation of :class:`~...protocols.StatsStore`."""

    def bump_test_history(
        self, project_id: int, test_case_hash: int, failed: bool, ts: datetime
    ) -> None:
        """Incremental per-test upsert (spec 02 §2.10).

        A ``failed`` observation bumps runs+failures and counts a flip if the
        previous status was ``passed``; a passing observation bumps runs only and
        counts a flip if the previous status was ``failed`` (the fail->pass pair).
        Flakiness is the alpha=0.1 EWMA of the flip indicator.
        """
        current = "failed" if failed else "passed"
        opposite = "passed" if failed else "failed"
        failure_inc = 1 if failed else 0
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO analyzer.test_history_stats AS s
                    (project_id, test_case_hash, window_runs, window_failures, window_flips,
                     last_status, last_failure_ts, flakiness_score)
                VALUES (%(pid)s, %(tch)s, 1, %(finc)s, 0, %(cur)s,
                        CASE WHEN %(failed)s THEN %(ts)s ELSE NULL END, 0.0)
                ON CONFLICT (project_id, test_case_hash) DO UPDATE SET
                    window_runs     = s.window_runs + 1,
                    window_failures = s.window_failures + %(finc)s,
                    window_flips    = s.window_flips
                                      + CASE WHEN s.last_status = %(opp)s THEN 1 ELSE 0 END,
                    flakiness_score = s.flakiness_score * 0.9
                                      + 0.1 * CASE WHEN s.last_status = %(opp)s THEN 1 ELSE 0 END,
                    last_status     = %(cur)s,
                    last_failure_ts = CASE WHEN %(failed)s THEN %(ts)s ELSE s.last_failure_ts END,
                    updated_at      = now()
                """,
                {
                    "pid": project_id,
                    "tch": test_case_hash,
                    "finc": failure_inc,
                    "cur": current,
                    "opp": opposite,
                    "failed": failed,
                    "ts": ts,
                },
            )

    def get_test_history(self, project_id: int, test_case_hashes: Sequence[int]) -> dict[int, dict]:
        if not test_case_hashes:
            return {}
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT test_case_hash, window_runs, window_failures, window_flips,
                       last_status, last_failure_ts, flakiness_score, updated_at
                FROM analyzer.test_history_stats
                WHERE project_id = %s AND test_case_hash = ANY(%s)
                """,
                (project_id, list(test_case_hashes)),
            )
            return {row["test_case_hash"]: row for row in cur.fetchall()}

    def bump_metrics(
        self,
        project_id: int,
        day: date,
        *,
        suggestions: int = 0,
        accepted: int = 0,
        corrected: int = 0,
        ignored: int = 0,
        abstained: int = 0,
        label: str | None = None,
    ) -> None:
        with self._conn() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO analyzer.metrics_daily
                    (project_id, day, suggestions, accepted, corrected, ignored, abstained)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (project_id, day) DO UPDATE SET
                    suggestions = analyzer.metrics_daily.suggestions + EXCLUDED.suggestions,
                    accepted    = analyzer.metrics_daily.accepted    + EXCLUDED.accepted,
                    corrected   = analyzer.metrics_daily.corrected   + EXCLUDED.corrected,
                    ignored     = analyzer.metrics_daily.ignored     + EXCLUDED.ignored,
                    abstained   = analyzer.metrics_daily.abstained   + EXCLUDED.abstained
                """,
                (project_id, day, suggestions, accepted, corrected, ignored, abstained),
            )
            if label is not None:
                current = require_row(
                    conn.execute(
                        "SELECT per_label FROM analyzer.metrics_daily "
                        "WHERE project_id=%s AND day=%s",
                        (project_id, day),
                    )
                )[0]
                bucket = dict(current.get(label, {})) if current else {}
                bucket["suggested"] = bucket.get("suggested", 0) + suggestions
                bucket["accepted"] = bucket.get("accepted", 0) + accepted
                bucket["corrected"] = bucket.get("corrected", 0) + corrected
                merged = {**(current or {}), label: bucket}
                conn.execute(
                    "UPDATE analyzer.metrics_daily SET per_label=%s WHERE project_id=%s AND day=%s",
                    (Jsonb(merged), project_id, day),
                )

    def get_metrics(self, project_id: int, frm: date, to: date) -> list[dict]:
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT project_id, day, suggestions, accepted, corrected, ignored,
                       abstained, per_label
                FROM analyzer.metrics_daily
                WHERE project_id = %s AND day BETWEEN %s AND %s
                ORDER BY day
                """,
                (project_id, frm, to),
            )
            return cur.fetchall()
