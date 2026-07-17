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
        self,
        project_id: int,
        test_case_hash: int,
        failed: bool,
        ts: datetime,
        *,
        conn: object | None = None,
    ) -> None:
        """Incremental per-test upsert (spec 02 §2.10).

        A ``failed`` observation bumps runs+failures and counts a flip if the
        previous status was ``passed``; a passing observation bumps runs only and
        counts a flip if the previous status was ``failed`` (the fail->pass pair).
        Flakiness is the alpha=0.1 EWMA of the flip indicator. Pass ``conn`` to run
        in the caller's transaction — §2.10 requires this upsert to share the
        ``index`` write's transaction with ``test_item``.
        """
        current = "failed" if failed else "passed"
        opposite = "passed" if failed else "failed"
        failure_inc = 1 if failed else 0
        if conn is not None:
            self._bump(conn, project_id, test_case_hash, current, opposite, failure_inc, failed, ts)
        else:
            with self._conn() as own:
                self._bump(
                    own, project_id, test_case_hash, current, opposite, failure_inc, failed, ts
                )

    @staticmethod
    def _bump(
        conn: object,
        project_id: int,
        test_case_hash: int,
        current: str,
        opposite: str,
        failure_inc: int,
        failed: bool,
        ts: datetime,
    ) -> None:
        conn.execute(  # type: ignore[attr-defined]
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

    # NOTE: an incremental `bump_metrics` (ON CONFLICT ... SET col = col + EXCLUDED)
    # was intentionally REMOVED. The metrics_daily row is owned exclusively by the
    # nightly rollup :meth:`upsert_daily_metrics`, which writes ABSOLUTE values
    # (SET col = EXCLUDED) so a recompute is idempotent. An incremental bump racing
    # that absolute SET would double-count or clobber the day's rollup, so no
    # incremental writer to metrics_daily is permitted (spec 03 §10.3).

    def get_metrics(self, project_id: int, frm: date, to: date) -> list[dict]:
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT project_id, day, suggestions, accepted, corrected, ignored,
                       abstained, auto_labeled, auto_corrected, per_label,
                       model_ver, emb_model_ver
                FROM analyzer.metrics_daily
                WHERE project_id = %s AND day BETWEEN %s AND %s
                ORDER BY day
                """,
                (project_id, frm, to),
            )
            return cur.fetchall()

    # -- nightly daily-metrics rollup (spec 03 §10.3) --------------------------- #
    def fetch_suggestions_for_day(self, day: date) -> list[dict]:
        """All ``suggestion`` rows *shown* on ``day``, across projects (§10.3).

        Aggregation keys off the suggestion's ``created_at`` day; outcomes are read
        from the same row (``defect_update`` stamps them in place).
        """
        with self._conn() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT project_id, item_id, created_at, predicted_label,
                       confidence, outcome, model_ver
                FROM analyzer.suggestion
                WHERE created_at >= %s::date AND created_at < (%s::date + INTERVAL '1 day')
                """,
                (day, day),
            )
            return cur.fetchall()

    def metrics_summary(self, since: date) -> dict:
        """Install-wide metrics_daily rollup since ``since`` (health summary, §10.3)."""
        keys = (
            "suggestions",
            "accepted",
            "corrected",
            "ignored",
            "abstained",
            "auto_labeled",
            "auto_corrected",
        )
        with self._conn() as conn:
            row = require_row(
                conn.execute(
                    """
                    SELECT coalesce(sum(suggestions),0), coalesce(sum(accepted),0),
                           coalesce(sum(corrected),0), coalesce(sum(ignored),0),
                           coalesce(sum(abstained),0), coalesce(sum(auto_labeled),0),
                           coalesce(sum(auto_corrected),0), max(day)
                    FROM analyzer.metrics_daily WHERE day >= %s
                    """,
                    (since,),
                )
            )
        summary: dict = {k: int(row[i]) for i, k in enumerate(keys)}
        summary["since"] = since.isoformat()
        summary["last_day"] = row[7].isoformat() if row[7] is not None else None
        return summary

    def upsert_daily_metrics(self, dm: object) -> None:
        """Idempotent absolute upsert of one (project, day) rollup (§10.3).

        Writes the counters, the auto-analysis safety counters, the per-label
        breakdown, and the active model/embedding provenance as computed by the
        nightly job. Absolute SET (not ``+=``) so a recompute is safe to re-run.
        The extension columns land in migration 0005.
        """
        with self._conn() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO analyzer.metrics_daily
                    (project_id, day, suggestions, accepted, corrected, ignored,
                     abstained, auto_labeled, auto_corrected, per_label,
                     model_ver, emb_model_ver)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (project_id, day) DO UPDATE SET
                    suggestions    = EXCLUDED.suggestions,
                    accepted       = EXCLUDED.accepted,
                    corrected      = EXCLUDED.corrected,
                    ignored        = EXCLUDED.ignored,
                    abstained      = EXCLUDED.abstained,
                    auto_labeled   = EXCLUDED.auto_labeled,
                    auto_corrected = EXCLUDED.auto_corrected,
                    per_label      = EXCLUDED.per_label,
                    model_ver      = EXCLUDED.model_ver,
                    emb_model_ver  = EXCLUDED.emb_model_ver
                """,
                (
                    dm.project_id,  # type: ignore[attr-defined]
                    dm.day,  # type: ignore[attr-defined]
                    dm.suggestions,  # type: ignore[attr-defined]
                    dm.accepted,  # type: ignore[attr-defined]
                    dm.corrected,  # type: ignore[attr-defined]
                    dm.ignored,  # type: ignore[attr-defined]
                    dm.abstained,  # type: ignore[attr-defined]
                    dm.auto_labeled,  # type: ignore[attr-defined]
                    dm.auto_corrected,  # type: ignore[attr-defined]
                    Jsonb(dm.per_label),  # type: ignore[attr-defined]
                    dm.model_ver,  # type: ignore[attr-defined]
                    dm.emb_model_ver,  # type: ignore[attr-defined]
                ),
            )
