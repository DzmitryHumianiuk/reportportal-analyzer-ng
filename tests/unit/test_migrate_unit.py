"""Pure-logic unit tests for the migration runner (spec 02 §3).

No database here — just the deterministic pieces: checksum normalization,
statement splitting (dollar-quote/quote aware), migration discovery, and the
gap/duplicate/checksum guards. The DB-backed acceptance items live in
tests/integration/test_db_migrations.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analyzer_ng.db import gen_partitions
from analyzer_ng.db.migrate import (
    ADVISORY_LOCK_KEY,
    DEFAULT_MIGRATIONS_DIR,
    Migration,
    MigrationError,
    _verify_applied,
    compute_checksum,
    discover_migrations,
    ledger_gaps,
    normalize_bytes,
    select_pending,
    split_statements,
)


def test_advisory_lock_key_is_ascii_anzmigr0() -> None:
    assert ADVISORY_LOCK_KEY == 0x616E7A6D69677230
    assert ADVISORY_LOCK_KEY.to_bytes(8, "big") == b"anzmigr0"
    # Fits in a signed int64 (top bit clear), as pg_advisory_lock requires.
    assert 0 < ADVISORY_LOCK_KEY < 2**63


def test_checksum_is_crlf_insensitive() -> None:
    assert normalize_bytes(b"a\r\nb") == b"a\nb"
    assert compute_checksum(b"a\r\nb\r\n") == compute_checksum(b"a\nb\n")
    assert compute_checksum(b"a\nb\n") != compute_checksum(b"a\nb\nc\n")


def test_split_statements_respects_dollar_quotes_and_literals() -> None:
    sql = """
    CREATE TABLE t (a int);
    CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $$
        SELECT 1;  -- semicolon inside the body must not split
    $$;
    INSERT INTO t VALUES (';');  -- semicolon inside a literal must not split
    """
    statements = split_statements(sql)
    assert len(statements) == 3
    assert statements[0].startswith("CREATE TABLE t")
    assert "$$" in statements[1] and "SELECT 1" in statements[1]
    assert statements[2].startswith("INSERT INTO t")


def test_split_statements_drops_comment_only_regions() -> None:
    sql = "-- just a comment\n-- another\nSELECT 1;\n-- trailing\n"
    assert split_statements(sql) == ["SELECT 1"]


def test_init_migration_splits_without_error() -> None:
    text = normalize_bytes((DEFAULT_MIGRATIONS_DIR / "0001_init.sql").read_bytes()).decode()
    statements = split_statements(text)
    # The array_jaccard function body (with its inner semicolon) survives as one
    # statement; every partition + table lands as its own statement.
    joined = "\n".join(statements)
    assert any("CREATE OR REPLACE FUNCTION analyzer.array_jaccard" in s for s in statements)
    assert joined.count("PARTITION OF test_item") == 16
    assert joined.count("PARTITION OF failure_signature") == 16


def test_discover_real_migrations() -> None:
    migrations = discover_migrations()
    assert [m.version for m in migrations] == [1, 2, 3, 4, 5, 6, 7]
    assert migrations[0].filename == "0001_init.sql"
    assert migrations[0].no_transaction is False
    assert migrations[1].filename == "0002_failure_mode_seed_key.sql"
    assert migrations[1].no_transaction is False
    assert migrations[2].filename == "0003_model_artifact.sql"
    assert migrations[2].no_transaction is False
    assert migrations[3].filename == "0004_llm.sql"
    assert migrations[3].no_transaction is False
    assert migrations[4].filename == "0005_metrics_daily_ext.sql"
    assert migrations[4].no_transaction is False
    assert migrations[5].filename == "0006_error_log_id.sql"
    assert migrations[5].no_transaction is False
    assert migrations[6].filename == "0007_suggestion_decision_cols.sql"
    assert migrations[6].no_transaction is False


def test_discover_tolerates_reserved_version_gap(tmp_path: Path) -> None:
    # A gap is a version reserved by an unmerged branch (e.g. Phase-4's 0004 while
    # develop holds 0003 + 0005). Discovery must succeed and apply what is present in
    # version order; only *duplicate* versions are a hard error.
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0003_c.sql").write_text("SELECT 1;")
    migrations = discover_migrations(tmp_path)
    assert [m.version for m in migrations] == [1, 3]


def test_discover_detects_duplicate(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 2;")
    with pytest.raises(MigrationError, match="duplicate"):
        discover_migrations(tmp_path)


def test_discover_rejects_bad_filename(tmp_path: Path) -> None:
    (tmp_path / "init.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="NNNN_name.sql"):
        discover_migrations(tmp_path)


def test_no_transaction_marker_detected(tmp_path: Path) -> None:
    (tmp_path / "0001_x.sql").write_text(
        "-- analyzer:no-transaction\nCREATE INDEX CONCURRENTLY IF NOT EXISTS i ON t (a);"
    )
    (tmp_path / "0002_y.sql").write_text("-- normal\nSELECT 1;")
    migrations = discover_migrations(tmp_path)
    assert migrations[0].no_transaction is True
    assert migrations[1].no_transaction is False


def _mk(version: int, checksum: str) -> Migration:
    return Migration(
        version=version,
        filename=f"{version:04d}_x.sql",
        path=Path("x"),
        checksum=checksum,
        sql="",
        no_transaction=False,
    )


def test_verify_applied_flags_tampered_checksum() -> None:
    by_version = {1: _mk(1, "aa")}
    with pytest.raises(MigrationError, match="changed after being applied"):
        _verify_applied({1: ("0001_x.sql", "bb")}, by_version)


def test_verify_applied_flags_missing_file() -> None:
    with pytest.raises(MigrationError, match="has no file"):
        _verify_applied({1: ("0001_x.sql", "aa")}, {})


def test_select_pending_closes_a_reserved_version_gap() -> None:
    # Ledger recorded {1,2,3,5} (0004 reserved by an unmerged branch when 0005
    # shipped). With all five files present, 0004 must now be pending — a
    # version > max(applied) high-watermark would skip it forever.
    migrations = [_mk(v, "aa") for v in (1, 2, 3, 4, 5)]
    pending = select_pending({1, 2, 3, 5}, migrations)
    assert [m.version for m in pending] == [4]


def test_select_pending_empty_when_ledger_current() -> None:
    migrations = [_mk(v, "aa") for v in (1, 2, 3)]
    assert select_pending({1, 2, 3}, migrations) == []


def test_ledger_gaps_ignores_unmerged_branch_gap() -> None:
    # File 0004 present but not yet applied → NOT a gap (it is pending, closes on run).
    migrations = [_mk(v, "aa") for v in (1, 2, 3, 4, 5)]
    assert ledger_gaps({1, 2, 3, 5}, migrations) == []


def test_ledger_gaps_flags_a_skipped_version_with_no_file() -> None:
    # Ledger {1,2,3,5} but no 0004 file on disk → a genuine, unfillable hole.
    migrations = [_mk(v, "aa") for v in (1, 2, 3, 5)]
    assert ledger_gaps({1, 2, 3, 5}, migrations) == [4]


def test_gen_partitions_blocks() -> None:
    block = gen_partitions.partition_ddl("test_item", 16)
    assert block.count("PARTITION OF test_item") == 16
    assert "MODULUS 16, REMAINDER 0)" in block
    assert "MODULUS 16, REMAINDER 15)" in block
    assert block.count("autovacuum_vacuum_scale_factor = 0.05") == 16
