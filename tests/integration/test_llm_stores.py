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
from analyzer_ng.db.repositories.llm_eval import PgLlmComparisonSource
from analyzer_ng.db.repositories.suggestion_ops import PgSuggestionOps
from analyzer_ng.db.startup import bootstrap_and_migrate
from analyzer_ng.llm.eval import LlmEvalJob
from analyzer_ng.llm.wiring import build_extractor_feature_lookup


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


def _seed_suggestions(
    conn, project_id: int, *, model_ver: str, n: int, accepted: int, item_base: int
) -> None:
    """Insert ``n`` resolved suggestions, ``accepted`` of them ``accepted``."""
    conn.execute(
        "INSERT INTO analyzer.project (project_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (project_id,),
    )
    for i in range(n):
        outcome = "accepted" if i < accepted else "corrected"
        conn.execute(
            """
            INSERT INTO analyzer.suggestion
                (project_id, item_id, launch_id, predicted_label, confidence,
                 model_ver, outcome)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (project_id, item_base + i, 1, "si001", 0.55, model_ver, outcome),
        )


def test_kill_switch_disables_underperforming_project_only(pool: ConnectionPool) -> None:
    tag = "qwen3:4b-q4_K_M"
    with pool.connection() as conn:
        # Project 1: cold-start precision 0.70 vs rule 0.80 over N=60 → disable.
        _seed_suggestions(conn, 1, model_ver=f"rubric+{tag}", n=60, accepted=42, item_base=1000)
        _seed_suggestions(conn, 1, model_ver="rule_cold;fs=2", n=60, accepted=48, item_base=2000)
        # Project 2: cold-start precision 0.85 vs rule 0.80 (LLM better) → keep.
        _seed_suggestions(conn, 2, model_ver=f"rubric+{tag}", n=60, accepted=51, item_base=1000)
        _seed_suggestions(conn, 2, model_ver="rule_cold;fs=2", n=60, accepted=48, item_base=2000)

    from datetime import UTC, datetime, timedelta

    state = PgLlmRoleStateStore(pool)
    LlmEvalJob(
        PgLlmComparisonSource(pool),
        state,
        clock=lambda: datetime.now(UTC) + timedelta(days=0),
    ).run()
    assert state.is_enabled(1, "coldstart") is False  # underperforming → disabled
    assert state.is_enabled(2, "coldstart") is True  # isolated: untouched
    rows = state.list_states(1)
    assert any(r["reason"] == "auto_disabled_precision" for r in rows)


def test_extractor_feature_lookup_tenancy(pool: ConnectionPool) -> None:
    from analyzer_ng.llm.roles.extractor import extractor_template_hash

    cache = PgLlmCacheStore(pool)
    thash = extractor_template_hash(4242, [3, 1, 2])
    out = {"failing_layer": "infrastructure", "error_class": "http_5xx"}
    cache.put(1, "e" * 64, "extractor", "m", out, template_hash=thash)
    lookup = build_extractor_feature_lookup(cache)
    # Project 1 (owner) hits at feature time; project 2 (same template set) misses.
    assert lookup(1, 4242, [1, 2, 3]) == ("infrastructure", "http_5xx")
    assert lookup(2, 4242, [1, 2, 3]) is None


def test_suggestion_ops_coldstart_and_explanation(pool: ConnectionPool) -> None:
    ops = PgSuggestionOps(pool)
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO analyzer.project (project_id) VALUES (7) ON CONFLICT DO NOTHING"
        )
    sid = ops.insert_coldstart(
        project_id=7,
        item_id=100,
        launch_id=5,
        predicted_label="si001",
        confidence=0.65,
        model_ver="rubric+qwen3:4b-q4_K_M",
        features={"coldstart": {"rule": "R6", "confidence": "high"}},
    )
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT confidence, llm_used, predicted_label FROM analyzer.suggestion "
            "WHERE project_id=7 AND suggestion_id=%s",
            (sid,),
        ).fetchone()
    assert row is not None
    assert row[0] < 0.75  # < τ_auto (cold-start never auto-applies)
    assert row[1] is True  # llm_used
    ops.set_explanation(7, sid, "matched a connection failure mode")
    with pool.connection() as conn:
        expl = conn.execute(
            "SELECT explanation, llm_used FROM analyzer.suggestion WHERE suggestion_id=%s",
            (sid,),
        ).fetchone()
    assert expl is not None and expl[0] == "matched a connection failure mode"
    assert expl[1] is True


def test_judge_annotation_persists_and_surfaces(pool: ConnectionPool) -> None:
    from analyzer_ng.db.repositories.retrieval import PgRetrievalStore

    ops = PgSuggestionOps(pool)
    retrieval = PgRetrievalStore(pool)
    with pool.connection() as conn:
        conn.execute("INSERT INTO analyzer.project (project_id) VALUES (3) ON CONFLICT DO NOTHING")
        conn.execute(
            """
            INSERT INTO analyzer.suggestion
                (project_id, item_id, launch_id, predicted_label, confidence, model_ver)
            VALUES (3, 50, 1, 'pb001', 0.55, 'rule_cold;fs=2')
            """
        )
    # §4.3: annotate the judge verdict (chosen candidate = item 777).
    patch = {
        "judge": {"choice": "candidate_1", "chosen_item_id": 777, "model": "m", "prompt_hash": "h"}
    }
    ops.annotate_judge(3, 50, chosen_item_id=777, features_patch=patch)
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT features -> 'judge' ->> 'chosen_item_id', llm_used "
            "FROM analyzer.suggestion WHERE project_id=3 AND item_id=50",
        ).fetchone()
    assert row is not None
    assert row[0] == "777"  # verdict persisted (never touched label/confidence)
    assert row[1] is True  # llm_used flipped
    # The read path surfaces the freshest verdict for the item.
    verdict = retrieval.latest_judge(3, 50)
    assert verdict is not None and verdict["chosen_item_id"] == 777
    # A verdict older than the 14-day judge TTL is not surfaced.
    with pool.connection() as conn:
        conn.execute(
            "UPDATE analyzer.suggestion SET created_at = now() - interval '20 days' "
            "WHERE project_id=3 AND item_id=50"
        )
    assert retrieval.latest_judge(3, 50) is None


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
