"""delete_project table policy (spec 02 §2.7 — learning-log retention).

RP's project-wide "Generate index" is a delete->rebuild that drives the same
``delete`` route as a real project deletion. delete_project must therefore wipe
only DERIVED data and PRESERVE the append-only ``label_event`` log, or every
reindex silently destroys the project's GBM-training / KB-purity history.

These are unit-level proofs over a recording fake connection (no database), so the
exact set of tables the store deletes is asserted without needing Docker; the
pg-backed behavior is covered by
``tests/integration/test_db_stores.py::test_delete_project_purges_derived_data_but_keeps_label_events``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from analyzer_ng.db.repositories.retrieval import PgRetrievalStore


class _FakeCursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...]:
        return self._row


class _RecordingConn:
    """Records every SQL string executed; returns a fixed count for the SELECT."""

    def __init__(self, executed: list[str], count: int) -> None:
        self._executed = executed
        self._count = count
        self.autocommit = True

    def execute(self, sql: str, params: object = None) -> _FakeCursor:
        self._executed.append(sql)
        return _FakeCursor((self._count,))

    @contextmanager
    def transaction(self) -> Iterator[_RecordingConn]:
        yield self


class _RecordingPool:
    def __init__(self, count: int = 7) -> None:
        self.executed: list[str] = []
        self._count = count

    @contextmanager
    def connection(self) -> Iterator[_RecordingConn]:
        yield _RecordingConn(self.executed, self._count)


def _deleted_tables(pool: _RecordingPool) -> set[str]:
    return {
        sql.split("analyzer.", 1)[1].split()[0]
        for sql in pool.executed
        if "DELETE FROM" in sql
    }


def test_delete_project_preserves_label_event() -> None:
    pool = _RecordingPool(count=7)
    store = PgRetrievalStore(pool)  # type: ignore[arg-type]

    removed = store.delete_project(1)

    assert removed == 7  # returns the pre-delete test_item count
    deleted = _deleted_tables(pool)
    # The append-only learning log is never deleted here.
    assert "label_event" not in deleted


def test_delete_project_wipes_derived_tables_including_history_stats() -> None:
    pool = _RecordingPool()
    store = PgRetrievalStore(pool)  # type: ignore[arg-type]

    store.delete_project(1)

    deleted = _deleted_tables(pool)
    # Every derived table is cleared. test_history_stats is DERIVED from indexing
    # (window run/failure counters rebuilt incrementally on reindex), so it is
    # wiped here — keeping it would double-count on rebuild.
    for table in (
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
        "project",
    ):
        assert table in deleted, f"{table} should be deleted by delete_project"
