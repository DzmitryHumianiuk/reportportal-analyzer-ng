"""Integration tests for lazy per-project seed-KB persistence (spec 03 §9, §11).

Run against real dockerized ``pgvector/pgvector:pg16`` via testcontainers.

Acceptance covered here:
- First match on a project creates exactly one lazy per-project copy
  (``status='candidate'``, ``label_source='seed'``, ``seed_key`` set, centroid
  NULL — spec 03 §9).
- Idempotent under concurrent workers: N threads racing to seed the same mode
  converge to a single row and the same mode_id (§11).
- Re-running the seed path never duplicates rows; distinct projects are isolated.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from analyzer_ng.db.repositories import PgKBStore
from analyzer_ng.db.startup import bootstrap_and_migrate
from analyzer_ng.seeds.loader import load_seed_kb


def _dsn_for_db(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    if params.get("host") in (None, "localhost"):
        params["host"] = "127.0.0.1"
    params["dbname"] = dbname
    return make_conninfo(**params)


@pytest.fixture
def store_dsn(postgres_dsn: str) -> Iterator[str]:
    dbname = f"anz_{uuid4().hex[:12]}"
    dsn = _dsn_for_db(postgres_dsn, dbname)
    bootstrap_and_migrate(dsn, create_db=True, attempts=3, delay=0.0)
    try:
        yield dsn
    finally:
        with psycopg.connect(_dsn_for_db(postgres_dsn, "postgres"), autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


@pytest.fixture
def pool(store_dsn: str) -> Iterator[ConnectionPool]:
    with ConnectionPool(store_dsn, min_size=1, max_size=8, open=True) as p:
        yield p


def _mode_rows(pool: ConnectionPool, project_id: int) -> list[dict]:
    with pool.connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT mode_id, status, label, label_source, seed_key, title, centroid "
            "FROM analyzer.failure_mode WHERE project_id = %s ORDER BY mode_id",
            (project_id,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def test_first_match_creates_one_lazy_copy(pool: ConnectionPool) -> None:
    kb = load_seed_kb(PgKBStore(pool))
    hit = kb.match_and_seed(42, exc_classes=["java.lang.OutOfMemoryError"])

    assert hit is not None
    assert hit.mode_key == "oom_java"
    assert hit.label == "si"
    assert hit.confidence == pytest.approx(0.8)

    rows = _mode_rows(pool, 42)
    assert len(rows) == 1
    row = rows[0]
    assert row["mode_id"] == hit.mode_id
    assert row["status"] == "candidate"  # spec 03 §9: per-project copy is a candidate
    assert row["label"] == "si"
    assert row["label_source"] == "seed"
    assert row["seed_key"] == "oom_java"
    assert row["centroid"] is None  # set from first matched items later


def test_rematch_is_idempotent(pool: ConnectionPool) -> None:
    kb = load_seed_kb(PgKBStore(pool))
    first = kb.match_and_seed(7, msg_text="java.io.IOException: No space left on device")
    second = kb.match_and_seed(7, msg_text="No space left on device: /var full")

    assert first is not None and second is not None
    assert first.mode_key == second.mode_key == "disk_full"
    assert first.mode_id == second.mode_id
    assert len(_mode_rows(pool, 7)) == 1


def test_distinct_seed_modes_get_distinct_rows(pool: ConnectionPool) -> None:
    kb = load_seed_kb(PgKBStore(pool))
    a = kb.match_and_seed(9, exc_classes=["org.openqa.selenium.StaleElementReferenceException"])
    b = kb.match_and_seed(9, exc_classes=["java.lang.NullPointerException"])

    assert a is not None and b is not None
    assert {a.mode_key, b.mode_key} == {"wd_stale_element", "npe_undefined"}
    assert a.mode_id != b.mode_id
    rows = _mode_rows(pool, 9)
    assert len(rows) == 2
    assert {r["seed_key"] for r in rows} == {"wd_stale_element", "npe_undefined"}


def test_projects_are_isolated(pool: ConnectionPool) -> None:
    kb = load_seed_kb(PgKBStore(pool))
    h1 = kb.match_and_seed(100, exc_classes=["java.lang.OutOfMemoryError"])
    h2 = kb.match_and_seed(200, exc_classes=["java.lang.OutOfMemoryError"])

    assert h1 is not None and h2 is not None
    assert len(_mode_rows(pool, 100)) == 1
    assert len(_mode_rows(pool, 200)) == 1
    # independent identity per project (both are mode_id 1 of their own project)
    assert _mode_rows(pool, 100)[0]["seed_key"] == "oom_java"
    assert _mode_rows(pool, 200)[0]["seed_key"] == "oom_java"


def test_no_match_creates_no_row(pool: ConnectionPool) -> None:
    kb = load_seed_kb(PgKBStore(pool))
    assert kb.match_and_seed(5, msg_text="INFO everything nominal, all steps green") is None
    assert _mode_rows(pool, 5) == []


def test_concurrent_workers_create_exactly_one_copy(pool: ConnectionPool) -> None:
    store = PgKBStore(pool)
    kb = load_seed_kb(store)
    mode = kb.match(exc_classes=["java.lang.StackOverflowError"])
    assert mode is not None

    results: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait()
            results.append(kb.ensure_project_copy(555, mode))
        except BaseException as exc:  # noqa: BLE001 - surfaced via assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    rows = _mode_rows(pool, 555)
    assert len(rows) == 1, f"expected one lazy copy, got {len(rows)}"
    assert set(results) == {rows[0]["mode_id"]}
