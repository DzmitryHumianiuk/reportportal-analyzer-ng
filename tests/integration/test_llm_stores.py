"""Integration tests for migration 0004 + the LLM event/state/cache stores (spec 04 §6.1).

Runs against real dockerized ``pgvector/pgvector:pg16`` via testcontainers. Covers:
- migration 0004 creates ``llm_event`` + ``llm_role_state`` with the spec DDL;
- event append + read-back; per-project role kill-switch read/write;
- extractor cache tenancy: same template_hash in one project is one cache row,
  the same content in another project is a separate row (spec §4.2 / §5.4).
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from analyzer_ng.db.repositories import (
    PgLlmCacheStore,
    PgLlmEventStore,
    PgLlmRoleStateStore,
)
from analyzer_ng.db.startup import bootstrap_and_migrate


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
    with ConnectionPool(store_dsn, min_size=1, max_size=4, open=True) as p:
        yield p


def test_migration_0004_creates_llm_tables(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'analyzer' AND table_name IN ('llm_event', 'llm_role_state')
            ORDER BY table_name
            """
        ).fetchall()
    assert [r[0] for r in rows] == ["llm_event", "llm_role_state"]


def test_llm_event_append_and_read(pool: ConnectionPool) -> None:
    store = PgLlmEventStore(pool)
    eid = store.record(
        project_id=1,
        item_id=10,
        role="judge",
        model="qwen3:4b-q4_K_M",
        prompt_hash="a" * 64,
        cache_hit=False,
        outcome="ok",
        output={"choice": "candidate_1", "reason": "match"},
        latency_ms=1234,
    )
    assert eid > 0
    recent = store.recent(1, "judge")
    assert len(recent) == 1
    assert recent[0]["outcome"] == "ok"
    assert recent[0]["output"] == {"choice": "candidate_1", "reason": "match"}
    assert recent[0]["cache_hit"] is False


def test_llm_event_outcome_check_constraint(pool: ConnectionPool) -> None:
    store = PgLlmEventStore(pool)
    with pytest.raises(psycopg.errors.CheckViolation):
        store.record(
            project_id=1,
            item_id=10,
            role="judge",
            model="m",
            prompt_hash="b" * 64,
            cache_hit=False,
            outcome="bogus_outcome",
        )


def test_role_state_defaults_enabled_and_toggles(pool: ConnectionPool) -> None:
    store = PgLlmRoleStateStore(pool)
    # No row → enabled by default.
    assert store.is_enabled(1, "explainer") is True
    store.set_state(1, "explainer", enabled=False, reason="auto_disabled_precision")
    assert store.is_enabled(1, "explainer") is False
    # Isolation: another project unaffected.
    assert store.is_enabled(2, "explainer") is True
    store.set_state(1, "explainer", enabled=True, reason="admin")
    assert store.is_enabled(1, "explainer") is True


def test_extractor_cache_tenancy(pool: ConnectionPool) -> None:
    cache = PgLlmCacheStore(pool)
    output = {"root_exception": "java.net.ConnectException", "error_class": "connection"}
    # Same cache_key/template_hash in two projects → two independent rows (§5.4).
    cache.put(1, "k" * 64, "extractor", "qwen3:4b-q4_K_M", output, template_hash=777)
    cache.put(2, "k" * 64, "extractor", "qwen3:4b-q4_K_M", output, template_hash=777)
    # Project 1 hit; project-2 key must miss for a project-3 reader.
    assert cache.get_fresh(1, "k" * 64, ttl_days=90) == output
    assert cache.get_fresh(3, "k" * 64, ttl_days=90) is None
    with pool.connection() as conn:
        n = conn.execute(
            "SELECT count(*) FROM analyzer.llm_cache WHERE role = 'extractor'"
        ).fetchone()
    assert n is not None and n[0] == 2


def test_cache_freshness_ttl(pool: ConnectionPool) -> None:
    cache = PgLlmCacheStore(pool)
    cache.put(1, "f" * 64, "explainer", "m", {"explanation": "x", "quoted_lines": []})
    # Backdate created_at beyond the TTL and confirm a stale read misses.
    with pool.connection() as conn:
        conn.execute(
            "UPDATE analyzer.llm_cache SET created_at = now() - interval '100 days' "
            "WHERE project_id = 1 AND cache_key = %s",
            ("f" * 64,),
        )
    assert cache.get_fresh(1, "f" * 64, ttl_days=90) is None
    # A generous TTL still finds it.
    assert cache.get_fresh(1, "f" * 64, ttl_days=200) is not None
