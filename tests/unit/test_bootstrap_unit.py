"""Pure-logic unit tests for the bootstrap fail-fast checks (spec 02 §1).

The server-version and pgvector-version guards are exercised here with a fake
connection so no old/vanilla server is needed; the container-backed
missing-pgvector path is covered in tests/integration/test_db_migrations.py.
"""

from __future__ import annotations

import psycopg
import pytest

from analyzer_ng.db import startup
from analyzer_ng.db.migrate import MigrationError
from analyzer_ng.db.startup import (
    EXIT_CONNECT,
    EXIT_FATAL,
    FATAL_PGVECTOR_MISSING,
    BootstrapError,
    _is_missing_database,
    _maintenance_dsn,
    _parse_version,
    _target_database_absent,
    bootstrap_and_migrate_or_exit,
    check_server_and_extensions,
)


class _FakeResult:
    def __init__(self, row: tuple | None) -> None:
        self._row = row

    def fetchone(self) -> tuple | None:
        return self._row


class _FakeConn:
    """Answers exactly the two queries check_server_and_extensions issues."""

    def __init__(self, server_version_num: int, vector_row: tuple | None) -> None:
        self._server_version_num = server_version_num
        self._vector_row = vector_row

    def execute(self, query: str, params: object = None) -> _FakeResult:
        if "server_version_num" in query:
            return _FakeResult((str(self._server_version_num),))
        if "pg_available_extensions" in query:
            return _FakeResult(self._vector_row)
        raise AssertionError(f"unexpected query: {query}")


def test_parse_version() -> None:
    assert _parse_version("0.8.0") == (0, 8, 0)
    assert _parse_version("0.8.1") == (0, 8, 1)
    assert _parse_version("0.7.4") == (0, 7, 4)


def test_check_passes_on_supported_server() -> None:
    check_server_and_extensions(_FakeConn(160004, ("0.8.0", "0.8.0")))  # no raise


def test_check_fails_on_old_server() -> None:
    with pytest.raises(BootstrapError) as exc:
        check_server_and_extensions(_FakeConn(150010, ("0.8.0", "0.8.0")))
    assert exc.value.exit_code == EXIT_FATAL
    assert "PostgreSQL 16+ required" in str(exc.value)


def test_check_fails_when_vector_absent() -> None:
    with pytest.raises(BootstrapError) as exc:
        check_server_and_extensions(_FakeConn(160004, None))
    assert exc.value.exit_code == EXIT_FATAL
    assert str(exc.value) == FATAL_PGVECTOR_MISSING


def test_check_fails_on_old_pgvector() -> None:
    # Not yet installed: default_version drives the guard.
    with pytest.raises(BootstrapError) as exc:
        check_server_and_extensions(_FakeConn(160004, ("0.7.4", None)))
    assert exc.value.exit_code == EXIT_FATAL
    assert "0.7.4" in str(exc.value)
    assert "0.8" in str(exc.value)


def test_target_absent_falls_back_to_text_when_maintenance_unreachable() -> None:
    # Port 1 => connection refused: the maintenance probe cannot decide, so the
    # decision falls back to the libpq-text heuristic on the original error.
    unreachable = "postgresql://u:p@127.0.0.1:1/postgres"
    missing = psycopg.OperationalError('database "app" does not exist')
    other = psycopg.OperationalError("some transient network blip")
    assert _target_database_absent(missing, unreachable, "app") is True
    assert _target_database_absent(other, unreachable, "app") is None


def test_is_missing_database_heuristic() -> None:
    assert _is_missing_database(psycopg.OperationalError('database "x" does not exist'), "x")
    assert not _is_missing_database(psycopg.OperationalError("connection refused"), "x")
    assert not _is_missing_database(psycopg.OperationalError("anything"), None)


def test_maintenance_dsn_swaps_dbname() -> None:
    dsn = "postgresql://u:p@host:5432/analyzer"
    maintenance, target = _maintenance_dsn(dsn)
    assert target == "analyzer"
    assert "dbname=postgres" in maintenance
    assert "analyzer" not in maintenance.replace("postgres", "")


def test_or_exit_maps_bootstrap_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> list[int]:
        raise BootstrapError("nope", EXIT_CONNECT)

    monkeypatch.setattr(startup, "bootstrap_and_migrate", boom)
    with pytest.raises(SystemExit) as exc:
        bootstrap_and_migrate_or_exit("postgresql://u:p@h/db")
    assert exc.value.code == EXIT_CONNECT


def test_or_exit_maps_migration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> list[int]:
        raise MigrationError("bad checksum")

    monkeypatch.setattr(startup, "bootstrap_and_migrate", boom)
    with pytest.raises(SystemExit) as exc:
        bootstrap_and_migrate_or_exit("postgresql://u:p@h/db")
    assert exc.value.code == 5
