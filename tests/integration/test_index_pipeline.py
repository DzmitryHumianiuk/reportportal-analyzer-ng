"""T2.2 acceptance — real index + maintenance pipeline over pgvector.

Drives the bound :class:`PipelineHandlers` (index / deletes / defect_update)
against a real, freshly migrated PostgreSQL database and asserts the pipeline's
DB effects: real rows written, idempotent re-index, real deletions with
label_event retention, and the defect_update feedback contract (spec 01 §4.5).

Uses the G1 ``rp_index_launches.json`` fixture so the same payload that proves
wire compatibility in ``test_rp_compat`` is here proven against the real store.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from analyzer_ng.amqp.dispatcher import Dispatcher, ProcessingItem, WorkerPool, build_routes
from analyzer_ng.amqp.models import (
    DefectUpdate,
    DeleteLaunchesRequest,
    DeleteLogsRequest,
    DeleteTestItemsRequest,
    Launch,
    RemoveByDatesRequest,
)
from analyzer_ng.core.handlers import PipelineHandlers, StubHandlers
from analyzer_ng.db.repositories import LabelEventIn, ModeIn, PgKBStore, PgLabelStore
from analyzer_ng.db.repositories._common import StoreBase
from analyzer_ng.db.startup import bootstrap_and_migrate

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PROJECT = 123


def _dsn_for_db(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    if params.get("host") in (None, "localhost"):
        params["host"] = "127.0.0.1"
    params["dbname"] = dbname
    return make_conninfo(**params)


@pytest.fixture
def store_dsn(postgres_dsn: str) -> Iterator[str]:
    dbname = f"anz_idx_{uuid4().hex[:12]}"
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
    with ConnectionPool(store_dsn, min_size=1, max_size=4, open=True) as p:
        yield p


@pytest.fixture
def handlers(pool: ConnectionPool) -> PipelineHandlers:
    h = PipelineHandlers()
    h.bind(pool)
    return h


def _launches() -> list[Launch]:
    data = json.loads((_FIXTURES / "rp_index_launches.json").read_text())
    return [Launch(**item) for item in data]


def _count(pool: ConnectionPool, table: str, project: int = PROJECT) -> int:
    with pool.connection() as conn:
        return conn.execute(
            f"SELECT count(*) FROM analyzer.{table} WHERE project_id=%s", (project,)
        ).fetchone()[0]


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #
def test_index_writes_real_rows_lowercased_and_stats(
    handlers: PipelineHandlers, pool: ConnectionPool
) -> None:
    resp = handlers.index(_launches())
    assert resp.errors is False
    assert resp.took >= 0
    assert any("NullPointerException" in e for lr in resp.logResults for e in lr.foundExceptions)

    assert _count(pool, "test_item") == 2
    assert _count(pool, "failure_signature") == 2

    with pool.connection() as conn:
        # issue_type lowercased so the generated issue_type_group resolves.
        rows = conn.execute(
            "SELECT item_id, issue_type, issue_type_group FROM analyzer.test_item "
            "WHERE project_id=%s ORDER BY item_id",
            (PROJECT,),
        ).fetchall()
        assert rows == [(2001, "ab001", "ab"), (2002, "pb002", "pb")]

        # signatures carry non-zero identity hashes + template ids.
        fp, eh, tids = conn.execute(
            "SELECT exception_fp, error_hash, template_ids FROM analyzer.failure_signature "
            "WHERE project_id=%s AND item_id=2001",
            (PROJECT,),
        ).fetchone()
        assert fp != 0 and eh != 0

        # incremental test_history_stats: one failure observation per test_case_hash.
        stats = conn.execute(
            "SELECT test_case_hash, window_runs, window_failures FROM "
            "analyzer.test_history_stats WHERE project_id=%s ORDER BY test_case_hash",
            (PROJECT,),
        ).fetchall()
        assert stats == [(1234567, 1, 1), (7654321, 1, 1)]

        # Drain3 state persisted + template mirror populated.
        assert (
            conn.execute(
                "SELECT count(*) FROM analyzer.drain3_state WHERE project_id=%s", (PROJECT,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM analyzer.log_template WHERE project_id=%s", (PROJECT,)
            ).fetchone()[0]
            >= 1
        )


def test_concurrent_burst_index_one_project_serializes_no_loss(store_dsn: str) -> None:
    """Burst of concurrent ``index`` batches for ONE project persists every launch.

    Reproduces RP's project-wide "Generate index" against the real store: several
    batches for the same project run on parallel threads, each doing
    load-version -> Drain-mine -> CAS-save. The per-project Postgres advisory lock
    (``PgDrain3StateStore.project_lock``) serializes that critical section, so no
    batch loses the optimistic-concurrency race and gets dropped — the live defect.

    Uses its own generously sized pool so the lock's held connection plus the
    load/save/retrieval connections never exhaust it (constraint: pool_max must
    exceed the number of concurrent same-project workers).
    """

    def _batch(item_id: int) -> Launch:
        return Launch(
            launchId=item_id,
            launchName="burst",
            launchNumber=item_id,
            project=PROJECT,
            testItems=[
                {
                    "testItemId": item_id,
                    "isAutoAnalyzed": False,
                    "testItemName": "t",
                    "testCaseHash": item_id,
                    "issueType": "ab001",
                    "logs": [
                        {
                            "logId": item_id,
                            "logLevel": 40000,
                            "message": "NullPointerException at Foo.bar(Foo.java:1)",
                        }
                    ],
                }
            ],
        )

    item_ids = [3001, 3002, 3003, 3004, 3005]
    with ConnectionPool(store_dsn, min_size=2, max_size=12, open=True) as burst_pool:
        handlers = PipelineHandlers()
        handlers.bind(burst_pool)
        results: dict[int, Any] = {}

        def worker(iid: int) -> None:
            results[iid] = handlers.index([_batch(iid)])

        threads = [threading.Thread(target=worker, args=(iid,)) for iid in item_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "an index worker deadlocked"

        # Every batch succeeded — none lost the CAS under the advisory lock.
        assert all(not r.errors for r in results.values())
        with burst_pool.connection() as conn:
            item_count = conn.execute(
                "SELECT count(DISTINCT item_id) FROM analyzer.test_item WHERE project_id=%s",
                (PROJECT,),
            ).fetchone()[0]
            version = conn.execute(
                "SELECT state_version FROM analyzer.drain3_state WHERE project_id=%s",
                (PROJECT,),
            ).fetchone()[0]
    assert item_count == len(item_ids)  # every launch persisted, nothing dropped
    assert version == len(item_ids)  # one serialized CAS save per batch, none lost


def test_reindex_is_idempotent_no_duplicate_rows(
    handlers: PipelineHandlers, pool: ConnectionPool
) -> None:
    handlers.index(_launches())
    with pool.connection() as conn:
        first = conn.execute(
            "SELECT error_hash FROM analyzer.failure_signature "
            "WHERE project_id=%s AND item_id=2001",
            (PROJECT,),
        ).fetchone()[0]

    handlers.index(_launches())  # same launch again
    assert _count(pool, "test_item") == 2  # upsert, not duplicate
    assert _count(pool, "failure_signature") == 2
    with pool.connection() as conn:
        again = conn.execute(
            "SELECT error_hash FROM analyzer.failure_signature "
            "WHERE project_id=%s AND item_id=2001",
            (PROJECT,),
        ).fetchone()[0]
    assert again == first  # deterministic identity across runs


# --------------------------------------------------------------------------- #
# deletions
# --------------------------------------------------------------------------- #
def test_delete_returns_count_and_purges_project(
    handlers: PipelineHandlers, pool: ConnectionPool
) -> None:
    handlers.index(_launches())
    removed = handlers.delete(PROJECT)
    assert removed == 2
    for table in ("test_item", "failure_signature", "drain3_state", "log_template"):
        assert _count(pool, table) == 0


def test_item_remove_and_launch_remove_retain_label_events(
    handlers: PipelineHandlers, pool: ConnectionPool
) -> None:
    handlers.index(_launches())
    PgLabelStore(pool).append_event(
        LabelEventIn(project_id=PROJECT, item_id=2001, new_label="ab001", source="rp_defect_update")
    )

    removed = handlers.item_remove(DeleteTestItemsRequest(project=PROJECT, itemsToDelete=[2001]))
    assert removed == 1
    assert _count(pool, "test_item") == 1  # 2002 remains
    assert _count(pool, "label_event") == 1  # append-only log retained

    removed = handlers.launch_remove(DeleteLaunchesRequest(project=PROJECT, launch_ids=[1001]))
    assert removed == 1  # 2002
    assert _count(pool, "test_item") == 0
    assert _count(pool, "label_event") == 1


def test_remove_by_log_time_respects_window(
    handlers: PipelineHandlers, pool: ConnectionPool
) -> None:
    launch = Launch(
        launchId=5,
        project=PROJECT,
        launchName="L",
        testItems=[
            {
                "testItemId": 10,
                "isAutoAnalyzed": False,
                "issueType": "pb001",
                "testItemName": "old",
                "testCaseHash": 11,
                "logs": [
                    {
                        "logId": 1,
                        "logLevel": 40000,
                        "logTime": [2025, 1, 1, 0, 0, 0, 0],
                        "message": "java.lang.RuntimeException: boom",
                    }
                ],
            },
            {
                "testItemId": 11,
                "isAutoAnalyzed": False,
                "issueType": "pb001",
                "testItemName": "new",
                "testCaseHash": 12,
                "logs": [
                    {
                        "logId": 2,
                        "logLevel": 40000,
                        "logTime": [2026, 6, 1, 0, 0, 0, 0],
                        "message": "java.lang.RuntimeException: boom",
                    }
                ],
            },
        ],
    )
    handlers.index([launch])
    removed = handlers.remove_by_log_time(
        RemoveByDatesRequest(
            project=PROJECT,
            interval_start_date="2024-12-01T00:00:00",
            interval_end_date="2025-02-01T00:00:00",
        )
    )
    assert removed == 1
    with pool.connection() as conn:
        left = conn.execute(
            "SELECT item_id FROM analyzer.test_item WHERE project_id=%s", (PROJECT,)
        ).fetchall()
    assert left == [(11,)]


def test_clean_returns_zero_no_per_log_storage(handlers: PipelineHandlers) -> None:
    # analyzer-ng stores no per-log rows (spec 02) — clean has nothing to delete.
    assert handlers.clean(DeleteLogsRequest(ids=[3001, 3002], project=PROJECT)) == 0


# --------------------------------------------------------------------------- #
# defect_update — the primary feedback source (spec 01 §4.5)
# --------------------------------------------------------------------------- #
def test_defect_update_appends_events_updates_labels_and_reports_unknown(
    handlers: PipelineHandlers, pool: ConnectionPool
) -> None:
    handlers.index(_launches())
    request = DefectUpdate(
        project=PROJECT,
        itemsToUpdate={
            2001: "PB001",  # bare locator string form (upper-case on the wire)
            2002: {
                "issueType": "si002",
                "issueComment": "reclassified",
                "timestamp": [2026, 7, 14, 12, 0, 0, 0],
            },
            9999: "ab001",  # unknown to analyzer storage
        },
    )
    not_updated = handlers.defect_update(request)
    assert not_updated == [9999]

    with pool.connection() as conn:
        # current label overwritten (lowercased) on the known items.
        labels = dict(
            conn.execute(
                "SELECT item_id, issue_type FROM analyzer.test_item WHERE project_id=%s",
                (PROJECT,),
            ).fetchall()
        )
        assert labels == {2001: "pb001", 2002: "si002"}

        # one append-only label_event per known item, with the prior label captured.
        events = conn.execute(
            "SELECT item_id, old_label, new_label, source FROM analyzer.label_event "
            "WHERE project_id=%s ORDER BY item_id",
            (PROJECT,),
        ).fetchall()
        assert events == [
            (2001, "ab001", "pb001", "rp_defect_update"),
            (2002, "pb002", "si002", "rp_defect_update"),
        ]


def test_defect_update_recomputes_mode_purity(
    handlers: PipelineHandlers, pool: ConnectionPool
) -> None:
    handlers.index(_launches())
    kb = PgKBStore(pool)
    mode_id = kb.spawn_candidate_mode(
        ModeIn(project_id=PROJECT, status="candidate", exception_fps=[1]), seed_item_ids=[2001]
    )
    # Before feedback the member is unlabeled-from-the-mode's view -> purity 0.
    handlers.defect_update(DefectUpdate(project=PROJECT, itemsToUpdate={2001: "pb001"}))
    with pool.connection() as conn:
        purity, support = conn.execute(
            "SELECT purity, support FROM analyzer.failure_mode WHERE project_id=%s AND mode_id=%s",
            (PROJECT, mode_id),
        ).fetchone()
    # The sole labeled member makes the mode pure.
    assert support == 1
    assert purity == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Review follow-up #1 — atomic index write (spec 02 §2.10, one transaction)
# --------------------------------------------------------------------------- #
def test_index_write_is_atomic_on_signature_failure(
    handlers: PipelineHandlers, pool: ConnectionPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after test_item upsert must roll back the whole per-project write.

    Proves §2.10: test_item + failure_signature + test_history_stats commit
    together, so nothing is persisted for the project when the signature write
    fails mid-sequence (never items-without-signatures).
    """
    retrieval = handlers._pipeline._retrieval  # type: ignore[union-attr]

    def boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("injected signature write failure")

    monkeypatch.setattr(retrieval, "upsert_signatures", boom)
    resp = handlers.index(_launches())

    assert resp.errors is True  # BulkResponse reports the failure
    # Nothing committed for the project: the transaction rolled back the items too.
    assert _count(pool, "test_item") == 0
    assert _count(pool, "failure_signature") == 0
    assert _count(pool, "test_history_stats") == 0


# --------------------------------------------------------------------------- #
# Review follow-up #2 — PG statement_timeout to remaining budget (spec 01 §8.2)
# --------------------------------------------------------------------------- #
class _SleepStore(StoreBase):
    def sleep(self, seconds: float) -> None:
        with self._conn() as conn:
            conn.execute("SELECT pg_sleep(%s)", (seconds,))


class _PgSleepHandlers(StubHandlers):
    def __init__(self, store: _SleepStore) -> None:
        self._store = store

    def noop_sleep(self, seconds: Any) -> None:
        self._store.sleep(float(seconds))
        return None


class _Publisher:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str, str]] = []
        self.dead_letters: list[tuple[bytes, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def reply(self, reply_to: str, correlation_id: str, body: str) -> None:
        with self._lock:
            self.replies.append((reply_to, correlation_id, body))

    def dead_letter(self, body: bytes, headers: dict[str, Any]) -> None:
        with self._lock:
            self.dead_letters.append((body, headers))


def _wait(predicate: Any, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def test_pg_statement_timeout_cancels_hung_sql(pool: ConnectionPool) -> None:
    """A SQL statement that would outlive the task budget is cancelled by PG.

    Without the §8.2 statement_timeout wiring a ``pg_sleep(30)`` in handler work
    would leak the worker thread for 30 s; with it, the server cancels the query
    at the remaining budget and the task fails cleanly to the DLQ path.
    """
    handlers = _PgSleepHandlers(_SleepStore(pool))
    pub = _Publisher()
    worker = WorkerPool(
        Dispatcher(build_routes(handlers)),
        pub,
        workers=1,
        retry_delays=[0.0, 0.0, 0.0],
        task_timeout=0.3,
    )
    worker.start()
    try:
        started = time.monotonic()
        worker.submit(
            ProcessingItem(1, 1, "noop_sleep", reply_to=None, correlation_id="c", body=30)
        )
        _wait(lambda: pub.dead_letters, timeout=15)
        elapsed = time.monotonic() - started
        # Cancelled promptly at the ~0.3 s budget (× a few retries), never ~30 s.
        assert elapsed < 10, f"statement not cancelled promptly (took {elapsed:.1f}s)"
        _body, headers = pub.dead_letters[0]
        assert headers["x-routing-key"] == "noop_sleep"
    finally:
        worker.shutdown(timeout=2)
