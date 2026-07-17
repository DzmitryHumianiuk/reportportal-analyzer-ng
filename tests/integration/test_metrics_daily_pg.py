"""Integration: nightly metrics_daily rollup over real suggestion rows (spec 03 §10.3).

Covers acceptance §11 "metrics_daily rows appear after a simulated accept/correct
cycle": suggestions are written with outcomes, the nightly job aggregates the day
and upserts ``metrics_daily``, and ``get_metrics`` reads the persisted counts back.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from analyzer_ng.db.repositories.stats import PgStatsStore
from analyzer_ng.db.startup import bootstrap_and_migrate
from analyzer_ng.ml.reporting import MetricsDailyJob

DAY = date(2026, 7, 15)
_TS = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)


def _dsn_for_db(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    if params.get("host") in (None, "localhost"):
        params["host"] = "127.0.0.1"
    params["dbname"] = dbname
    return make_conninfo(**params)


@pytest.fixture
def pool(postgres_dsn: str) -> Iterator[ConnectionPool]:
    dbname = f"anz_{uuid4().hex[:12]}"
    dsn = _dsn_for_db(postgres_dsn, dbname)
    bootstrap_and_migrate(dsn, create_db=True, attempts=3, delay=0.0)
    try:
        with ConnectionPool(dsn, min_size=1, max_size=4, open=True) as p:
            yield p
    finally:
        with psycopg.connect(_dsn_for_db(postgres_dsn, "postgres"), autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


def _insert_suggestion(
    pool: ConnectionPool, project_id: int, item_id: int, label: str, conf: float, outcome: str
) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO analyzer.suggestion
                (project_id, item_id, launch_id, predicted_label, confidence,
                 model_ver, outcome, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (project_id, item_id, 1, label, conf, "gbm-test", outcome, _TS),
        )


def test_metrics_daily_populated_after_accept_correct_cycle(pool: ConnectionPool) -> None:
    # A day's suggestions: one accepted auto-label, one auto-label later corrected,
    # one below-band suggestion, one abstain (ti).
    _insert_suggestion(pool, 1, 10, "pb001", 0.90, "accepted")
    _insert_suggestion(pool, 1, 11, "ab001", 0.85, "corrected")  # auto-corrected (safety)
    _insert_suggestion(pool, 1, 12, "si001", 0.50, "ignored")
    _insert_suggestion(pool, 1, 13, "ti", 0.10, "ignored")

    store = PgStatsStore(pool)
    written = MetricsDailyJob(store, emb_model_ver="e5s-int8-rTEST").run(DAY)
    assert len(written) == 1
    dm = written[0]
    assert dm.auto_labeled == 2 and dm.auto_corrected == 1

    # Persisted counters + §10.3 extension columns read back through get_metrics.
    [row] = store.get_metrics(1, DAY, DAY)
    assert row["suggestions"] == 4
    assert row["accepted"] == 1
    assert row["corrected"] == 1
    assert row["ignored"] == 2
    assert row["abstained"] == 1
    assert row["auto_labeled"] == 2
    assert row["auto_corrected"] == 1
    assert row["model_ver"] == "gbm-test"
    assert row["emb_model_ver"] == "e5s-int8-rTEST"
    assert row["per_label"]["pb"] == {"suggested": 1, "accepted": 1, "corrected": 0}
    assert row["per_label"]["ab"] == {"suggested": 1, "accepted": 0, "corrected": 1}

    # Install-wide health summary reflects the same day.
    summary = store.metrics_summary(DAY)
    assert summary["suggestions"] == 4
    assert summary["auto_corrected"] == 1
    assert summary["last_day"] == DAY.isoformat()


def test_rollup_is_idempotent(pool: ConnectionPool) -> None:
    _insert_suggestion(pool, 2, 20, "pb001", 0.9, "accepted")
    store = PgStatsStore(pool)
    job = MetricsDailyJob(store)
    job.run(DAY)
    job.run(DAY)  # a re-run must not double-count (absolute upsert)
    [row] = store.get_metrics(2, DAY, DAY)
    assert row["suggestions"] == 1
    assert row["accepted"] == 1
