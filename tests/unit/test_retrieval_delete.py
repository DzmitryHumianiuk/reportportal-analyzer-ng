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


# --------------------------------------------------------------------------- #
# reap_orphan_label_events — SQL-shape / guard proofs (tech-debt #6).
#
# Behavioral row-level proof (mark / unmark / sweep-after-grace / mid-reindex
# never purges) lives in the pg-backed
# tests/integration/test_db_stores.py::test_reap_orphan_label_events_* suite.
# Here we prove, without a database, the invariants that keep the reaper from
# ever destroying live learning history.
# --------------------------------------------------------------------------- #
class _ReapCursor:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _ReapConn:
    """Records (sql, params) for every execute; returns a fixed rowcount."""

    def __init__(self, executed: list[tuple[str, object]], rowcount: int) -> None:
        self._executed = executed
        self._rowcount = rowcount
        self.autocommit = True

    def execute(self, sql: str, params: object = None) -> _ReapCursor:
        self._executed.append((sql, params))
        return _ReapCursor(self._rowcount)

    @contextmanager
    def transaction(self) -> Iterator[_ReapConn]:
        yield self


class _ReapPool:
    def __init__(self, rowcount: int = 4) -> None:
        self.executed: list[tuple[str, object]] = []
        self._rowcount = rowcount

    @contextmanager
    def connection(self) -> Iterator[_ReapConn]:
        yield _ReapConn(self.executed, self._rowcount)


def test_reap_marks_unmarks_and_sweeps_guarded_by_test_item() -> None:
    pool = _ReapPool(rowcount=4)
    store = PgRetrievalStore(pool)  # type: ignore[arg-type]

    purged = store.reap_orphan_label_events(grace_days=30)

    # The sweep's DELETE FROM label_event rowcount is returned.
    assert purged == 4
    sqls = [sql for sql, _ in pool.executed]
    joined = "\n---\n".join(sqls)

    # 1. mark: only items with NO test_item are tombstoned.
    mark = next(s for s in sqls if "INSERT INTO analyzer.label_event_orphan" in s)
    assert "NOT EXISTS" in mark and "analyzer.test_item" in mark
    assert "ON CONFLICT (project_id, item_id) DO NOTHING" in mark

    # 2. unmark: a reappeared test_item clears the tombstone (reindex, not deletion).
    unmark = next(
        s
        for s in sqls
        if "DELETE FROM analyzer.label_event_orphan" in s and " EXISTS (" in s
    )
    assert "analyzer.test_item" in unmark

    # 3. sweep: label_event is deleted ONLY past the grace AND still with no
    #    test_item — never on age alone.
    sweep = next(s for s in sqls if "DELETE FROM analyzer.label_event le" in s)
    assert "make_interval(days =>" in sweep
    assert "NOT EXISTS" in sweep and "analyzer.test_item" in sweep

    # The only table whose rows the reaper deletes (besides its own tombstone) is
    # label_event — never test_item or any derived table.
    deleted_targets = {
        line.split("DELETE FROM analyzer.", 1)[1].split()[0]
        for line in joined.splitlines()
        if "DELETE FROM analyzer." in line
    }
    assert deleted_targets == {"label_event", "label_event_orphan"}


def test_reap_disabled_grace_skips_sweep() -> None:
    # grace_days <= 0 disables the destructive sweep entirely (mark/unmark still
    # run) so an operator can never set an aggressive grace that reaps history.
    pool = _ReapPool(rowcount=9)
    store = PgRetrievalStore(pool)  # type: ignore[arg-type]

    purged = store.reap_orphan_label_events(grace_days=0)

    assert purged == 0
    sqls = [sql for sql, _ in pool.executed]
    assert not any("DELETE FROM analyzer.label_event le" in s for s in sqls)
    # mark + unmark still ran (they are non-destructive to label_event).
    assert any("INSERT INTO analyzer.label_event_orphan" in s for s in sqls)
