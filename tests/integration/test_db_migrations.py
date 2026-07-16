"""Integration acceptance tests for bootstrap + migration runner (spec 02 §8).

Run against real dockerized PostgreSQL via testcontainers. Covers the spec's
acceptance checklist items in scope for T1.1:

- clean bootstrap on an empty PG (auto-create DB, schema, extensions, 0001_init)
- idempotent restart (nothing applied)
- concurrent starts converge to a single application (advisory lock)
- checksum tamper detected (exit 5)
- fail-fast when pgvector is missing (exit 3), against vanilla postgres:16
- ANALYZER_PG_CREATE_DB=false against a missing DB fails fast (exit 3)
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from testcontainers.postgres import PostgresContainer

from analyzer_ng.db.migrate import (
    DEFAULT_MIGRATIONS_DIR,
    MigrationError,
    apply_migrations,
    compute_checksum,
)
from analyzer_ng.db.startup import (
    BootstrapError,
    bootstrap,
    bootstrap_and_migrate,
    bootstrap_and_migrate_or_exit,
)

_INIT_SQL = DEFAULT_MIGRATIONS_DIR / "0001_init.sql"

# Every base (non-partition) table 0001_init.sql must create.
_EXPECTED_TABLES = {
    "project",
    "drain3_state",
    "log_template",
    "test_item",
    "failure_signature",
    "failure_mode",
    "mode_membership",
    "label_event",
    "suggestion",
    "launch_group",
    "test_history_stats",
    "llm_cache",
    "metrics_daily",
}


def _force_ipv4(params: dict[str, str]) -> dict[str, str]:
    # testcontainers hands back host=localhost, which resolves to BOTH 127.0.0.1
    # and ::1. The PG container listens on IPv4 only, so a connection to a
    # missing DB yields 3D000 on v4 but connection-refused on v6; psycopg
    # collapses that into a composite error with no sqlstate, masking the
    # missing-DB signal. Pinning to 127.0.0.1 removes the ambiguity so the real
    # bootstrap logic (sqlstate 3D000 -> create DB) is exercised cleanly.
    if params.get("host") in (None, "localhost"):
        params["host"] = "127.0.0.1"
    return params


def _dsn_for_db(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    params["dbname"] = dbname
    return make_conninfo(**_force_ipv4(params))


def _drop_db(base_dsn: str, dbname: str) -> None:
    maintenance = _dsn_for_db(base_dsn, "postgres")
    with psycopg.connect(maintenance, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
        )


@pytest.fixture
def fresh_db_dsn(postgres_dsn: str) -> Iterator[str]:
    """A DSN pointing at a brand-new, not-yet-created database (dropped after)."""
    dbname = f"anz_{uuid4().hex[:12]}"
    try:
        yield _dsn_for_db(postgres_dsn, dbname)
    finally:
        _drop_db(postgres_dsn, dbname)


# --------------------------------------------------------------------------- #
# Acceptance #1 — clean bootstrap on an empty PG.
# --------------------------------------------------------------------------- #


def test_clean_bootstrap_creates_db_schema_and_migration(fresh_db_dsn: str) -> None:
    applied = bootstrap_and_migrate(fresh_db_dsn, create_db=True, attempts=3, delay=0.0)
    assert applied == [1]

    with psycopg.connect(fresh_db_dsn) as conn:
        conn.execute("SET search_path = analyzer, public")

        # schema_migrations: exactly one row, correct sha256.
        rows = conn.execute(
            "SELECT version, filename, checksum FROM analyzer.schema_migrations"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 1
        assert rows[0][1] == "0001_init.sql"
        assert rows[0][2] == compute_checksum(_INIT_SQL.read_bytes())

        # Extensions installed.
        exts = {
            r[0]
            for r in conn.execute(
                "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm')"
            ).fetchall()
        }
        assert exts == {"vector", "pg_trgm"}

        # All base tables present in the analyzer schema.
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'analyzer'"
            ).fetchall()
        }
        assert _EXPECTED_TABLES <= tables

        # 16 hash partitions for each hot table.
        for parent in ("test_item", "failure_signature"):
            n = conn.execute(
                "SELECT count(*) FROM pg_inherits i "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = %s::regclass",
                (f"analyzer.{parent}",),
            ).fetchone()[0]
            assert n == 16, f"{parent} should have 16 partitions, found {n}"

        # array_jaccard helper works.
        jac = conn.execute(
            "SELECT analyzer.array_jaccard(ARRAY[1,2,3]::bigint[], ARRAY[2,3,4]::bigint[])"
        ).fetchone()[0]
        assert jac == pytest.approx(2 / 4)


def test_generated_columns_and_halfvec(fresh_db_dsn: str) -> None:
    bootstrap_and_migrate(fresh_db_dsn, create_db=True, attempts=3, delay=0.0)
    with psycopg.connect(fresh_db_dsn, autocommit=True) as conn:
        conn.execute("SET search_path = analyzer, public")
        conn.execute("INSERT INTO project (project_id) VALUES (1)")

        # issue_type_group is generated from issue_type.
        conn.execute(
            "INSERT INTO test_item (project_id, item_id, launch_id, issue_type) "
            "VALUES (1, 10, 100, 'pb001')"
        )
        group = conn.execute(
            "SELECT issue_type_group FROM test_item WHERE project_id=1 AND item_id=10"
        ).fetchone()[0]
        assert group == "pb"

        # signature_text / signature_tsv are generated; emb is halfvec(384).
        conn.execute(
            "INSERT INTO failure_signature "
            "(project_id, item_id, exception_fp, error_hash, exc_text, msg_text, "
            "frames_text, emb, emb_model_ver) "
            "VALUES (1, 10, 111, 222, 'NullPointerException', 'boom at line', "
            "'com.Foo.bar', %s, 1)",
            ("[" + ",".join("0.1" for _ in range(384)) + "]",),
        )
        text, tsv_len = conn.execute(
            "SELECT signature_text, length(signature_tsv::text) "
            "FROM failure_signature WHERE project_id=1 AND item_id=10"
        ).fetchone()
        assert text == "NullPointerException boom at line com.Foo.bar"
        assert tsv_len > 0

        # halfvec column type check.
        udt = conn.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_schema='analyzer' AND table_name='failure_signature' AND column_name='emb'"
        ).fetchone()[0]
        assert udt == "halfvec"


def test_rows_land_in_correct_partition(fresh_db_dsn: str) -> None:
    bootstrap_and_migrate(fresh_db_dsn, create_db=True, attempts=3, delay=0.0)
    with psycopg.connect(fresh_db_dsn, autocommit=True) as conn:
        conn.execute("SET search_path = analyzer, public")
        for pid in range(1, 40):
            conn.execute("INSERT INTO project (project_id) VALUES (%s)", (pid,))
            conn.execute(
                "INSERT INTO test_item (project_id, item_id, launch_id) VALUES (%s, %s, %s)",
                (pid, pid * 10, pid * 100),
            )
        # Rows distribute across more than one partition (hash partitioning works).
        distinct_partitions = conn.execute(
            "SELECT count(DISTINCT tableoid::regclass) FROM test_item"
        ).fetchone()[0]
        assert distinct_partitions > 1
        total = conn.execute("SELECT count(*) FROM test_item").fetchone()[0]
        assert total == 39


# --------------------------------------------------------------------------- #
# Acceptance #2 — idempotent restart.
# --------------------------------------------------------------------------- #


def test_idempotent_restart(fresh_db_dsn: str) -> None:
    first = bootstrap_and_migrate(fresh_db_dsn, create_db=True, attempts=3, delay=0.0)
    assert first == [1]

    with psycopg.connect(fresh_db_dsn) as conn:
        applied_at_before = conn.execute(
            "SELECT applied_at FROM analyzer.schema_migrations WHERE version=1"
        ).fetchone()[0]

    second = bootstrap_and_migrate(fresh_db_dsn, create_db=True, attempts=3, delay=0.0)
    assert second == []

    with psycopg.connect(fresh_db_dsn) as conn:
        rows = conn.execute("SELECT applied_at FROM analyzer.schema_migrations").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == applied_at_before  # untouched


# --------------------------------------------------------------------------- #
# Acceptance #4 — concurrent starts converge to a single application.
# --------------------------------------------------------------------------- #


def test_concurrent_starts_apply_once(fresh_db_dsn: str) -> None:
    # Bootstrap the DB/schema/extensions once, then race two migration runners
    # on their own connections (the advisory lock must serialize them).
    conn0 = bootstrap(fresh_db_dsn, create_db=True, attempts=3, delay=0.0)
    conn0.close()

    barrier = threading.Barrier(2)
    results: list[list[int]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait()
            with psycopg.connect(fresh_db_dsn) as conn:
                applied = apply_migrations(conn)
            with lock:
                results.append(applied)
        except BaseException as exc:  # noqa: BLE001 - surface any race failure
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent runners errored: {errors}"
    # Exactly one runner applied version 1; the other found nothing pending.
    assert sorted(results) == [[], [1]]

    with psycopg.connect(fresh_db_dsn) as conn:
        rows = conn.execute("SELECT applied_at FROM analyzer.schema_migrations").fetchall()
        assert len(rows) == 1


# --------------------------------------------------------------------------- #
# Acceptance #5 — checksum tamper detected (exit 5).
# --------------------------------------------------------------------------- #


def test_checksum_tamper_detected(fresh_db_dsn: str, tmp_path: Path) -> None:
    migration = tmp_path / "0001_demo.sql"
    migration.write_text("CREATE TABLE demo (id int);\n")

    conn = bootstrap(fresh_db_dsn, create_db=True, attempts=3, delay=0.0)
    with conn:
        assert apply_migrations(conn, migrations_dir=tmp_path) == [1]

    # Mutate one byte of the already-applied file.
    migration.write_text("CREATE TABLE demo (id  int);\n")

    with psycopg.connect(fresh_db_dsn) as conn2:
        with pytest.raises(MigrationError) as exc:
            apply_migrations(conn2, migrations_dir=tmp_path)
    assert exc.value.exit_code == 5
    assert "0001_demo.sql" in str(exc.value)
    assert "changed after being applied" in str(exc.value)


# --------------------------------------------------------------------------- #
# ANALYZER_PG_CREATE_DB=false against a missing DB → fail fast (exit 3).
# --------------------------------------------------------------------------- #


def test_missing_db_without_create_flag_exits_3(fresh_db_dsn: str) -> None:
    with pytest.raises(BootstrapError) as exc:
        bootstrap(fresh_db_dsn, create_db=False, attempts=3, delay=0.0)
    assert exc.value.exit_code == 3


# --------------------------------------------------------------------------- #
# Acceptance #3 — fail fast when pgvector is missing (exit 3), vanilla PG.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def vanilla_postgres_dsn() -> Iterator[str]:
    """A stock postgres:16 container (no pgvector) for the fail-fast test."""
    container = PostgresContainer(
        image="postgres:16", username="analyzer", password="analyzer", dbname="analyzer"
    )
    with container:
        params = _force_ipv4(conninfo_to_dict(container.get_connection_url(driver=None)))
        yield make_conninfo(**params)


def test_missing_pgvector_fails_fast(
    vanilla_postgres_dsn: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="analyzer_ng.db.startup"):
        with pytest.raises(SystemExit) as exc:
            bootstrap_and_migrate_or_exit(
                vanilla_postgres_dsn, create_db=False, attempts=3, delay=0.0
            )
    assert exc.value.code == 3
    messages = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert "pgvector is not installed" in messages
    assert "pgvector/pgvector:pg16" in messages
