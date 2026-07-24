"""Integration acceptance tests for the store layer (spec 02 §4, §5, §8).

Run against real dockerized ``pgvector/pgvector:pg16`` via testcontainers with
deterministic seeded fixtures (synthetic fixed embeddings — no Phase-2 embedder).
Covers the spec's checklist items in scope for T1.3:

- #6  roundtrip: upsert lands rows in the correct hash partition, gen cols populated
- #7  hybrid query: fused top-k, near-duplicates on top, RRF formula exact, ti/unlabeled excluded
- #8  trgm fallback when FTS yields nothing
- #9  emb_model_ver isolation: dense returns only the current version
- #10 mode matching: fingerprint hits first, near-centroid cosine > 0.9
- #11 tenancy: no cross-project leakage (items and llm_cache)
- #12 deletes: launches/project/time-range with label_event retained
- #13 Drain3 CAS: exactly one concurrent save wins
- #14 exact-scan vs HNSW: both query paths covered
- #15 golden stability: byte-identical ordered item_id list across runs
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from analyzer_ng.db.repositories import (
    LabelEventIn,
    ModeIn,
    PgDrain3StateStore,
    PgKBStore,
    PgLabelStore,
    PgLlmCacheStore,
    PgRetrievalStore,
    QuerySignature,
    SignatureIn,
    TestItemIn,
)
from analyzer_ng.db.startup import bootstrap_and_migrate

# TestItemIn is a pydantic DTO, not a test class — opt it out of collection so
# the suite output stays pristine (its name matches pytest's Test* pattern).
TestItemIn.__test__ = False  # type: ignore[attr-defined]

_DIMS = 384
NOW = datetime(2026, 1, 1, tzinfo=UTC)

# Two orthogonal-ish unit-length-ish vectors so cosine cleanly separates the
# near-duplicate cluster from filler rows.
NEAR_VEC = [1.0 if i == 0 else 0.0 for i in range(_DIMS)]


def _far_vec(seed: int) -> list[float]:
    return [1.0 if i == (1 + seed % (_DIMS - 1)) else 0.0 for i in range(_DIMS)]


def _dsn_for_db(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    if params.get("host") in (None, "localhost"):
        params["host"] = "127.0.0.1"  # PG container listens on IPv4 only
    params["dbname"] = dbname
    return make_conninfo(**params)


@pytest.fixture
def store_dsn(postgres_dsn: str) -> Iterator[str]:
    """A freshly created + migrated throwaway database, dropped after the test."""
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


def _query() -> QuerySignature:
    return QuerySignature(
        exception_fp=501,
        error_hash=9001,
        top_frames=["com.foo.Bar.baz"],
        template_ids=[1, 2, 3],
        salient_terms=['"NullPointerException"', "connection pool timeout"],
        exception_names=["NullPointerException"],
        emb=NEAR_VEC,
        emb_model_ver=1,
        test_case_hash=1001,
        launch_number=10,
    )


def _seed_hybrid_fixture(store: PgRetrievalStore, project_id: int) -> None:
    """50 labeled signatures; items 1..5 are near-duplicates of the query.

    Items 49/50 are near-duplicate in content+embedding but ti-labeled /
    unlabeled respectively, so they must never surface as evidence.
    """
    items: list[TestItemIn] = []
    sigs: list[SignatureIn] = []
    for i in range(1, 49):
        near = i <= 5
        items.append(
            TestItemIn(
                item_id=i,
                project_id=project_id,
                launch_id=100,
                launch_number=i,
                test_case_hash=1000 + i,
                issue_type="pb001",
            )
        )
        sigs.append(
            SignatureIn(
                project_id=project_id,
                item_id=i,
                exception_fp=501 if near else 700 + i,
                error_hash=9001 if near else 8000 + i,
                template_ids=[1, 2, 3] if near else [90 + i],
                exc_text="NullPointerException" if near else f"OtherError{i}",
                msg_text="connection pool timeout exhausted" if near else f"benign detail {i}",
                frames_text="com.foo.Bar.baz" if near else f"com.other.Cls{i}",
                emb=NEAR_VEC if near else _far_vec(i),
                emb_model_ver=1,
            )
        )
    # ti-labeled and unlabeled near-duplicates — evidence must exclude both.
    items.append(TestItemIn(item_id=49, project_id=project_id, launch_id=100, issue_type="ti001"))
    items.append(TestItemIn(item_id=50, project_id=project_id, launch_id=100, issue_type=None))
    for iid in (49, 50):
        sigs.append(
            SignatureIn(
                project_id=project_id,
                item_id=iid,
                exception_fp=501,
                error_hash=9001,
                template_ids=[1, 2, 3],
                exc_text="NullPointerException",
                msg_text="connection pool timeout exhausted",
                frames_text="com.foo.Bar.baz",
                emb=NEAR_VEC,
                emb_model_ver=1,
            )
        )
    store.upsert_items(items)
    store.upsert_signatures(sigs)


# --------------------------------------------------------------------------- #
# #6 roundtrip
# --------------------------------------------------------------------------- #
def test_roundtrip_partitioning_and_generated_columns(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    items = [
        TestItemIn(item_id=i, project_id=7, launch_id=1, test_case_hash=i, issue_type="pb001")
        for i in range(200)
    ]
    assert store.upsert_items(items) == 200
    sigs = [
        SignatureIn(project_id=7, item_id=i, exception_fp=i, error_hash=i, exc_text="Boom")
        for i in range(200)
    ]
    assert store.upsert_signatures(sigs) == 200

    with pool.connection() as conn:
        # All 200 items for project 7 hash to exactly one partition.
        parts = conn.execute(
            "SELECT count(DISTINCT tableoid::regclass) FROM analyzer.test_item WHERE project_id=7"
        ).fetchone()[0]
        assert parts == 1
        # Generated issue_type_group populated.
        grp = conn.execute(
            "SELECT DISTINCT issue_type_group FROM analyzer.test_item WHERE project_id=7"
        ).fetchall()
        assert grp == [("pb",)]
        # Generated signature_tsv populated.
        tsv = conn.execute(
            "SELECT count(*) FROM analyzer.failure_signature "
            "WHERE project_id=7 AND signature_tsv IS NOT NULL AND signature_tsv <> ''::tsvector"
        ).fetchone()[0]
        assert tsv == 200


# --------------------------------------------------------------------------- #
# #7 hybrid query + #15 golden stability
# --------------------------------------------------------------------------- #
def test_hybrid_rrf_ordering_golden_and_formula(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    _seed_hybrid_fixture(store, project_id=1)
    q = _query()

    cands = store.find_candidates(1, q, k=20)
    item_order = [c.item_id for c in cands if c.item_id is not None]

    # Golden: the 5 near-duplicates occupy the top ranks, deterministically
    # ordered by the item_id DESC tie-break (identical fused scores).
    assert item_order[:5] == [5, 4, 3, 2, 1]

    # ti-labeled (49) and unlabeled (50) never appear.
    assert 49 not in item_order and 50 not in item_order

    # Every returned row's rrf_score == 1/(60+sparse) + 1/(60+dense) (nulls -> 0).
    for c in cands:
        if c.item_id is None:
            continue
        expected = (1.0 / (60 + c.sparse_rank) if c.sparse_rank else 0.0) + (
            1.0 / (60 + c.dense_rank) if c.dense_rank else 0.0
        )
        assert c.rrf_score == pytest.approx(expected)

    # Golden stability (#15): identical byte-for-byte ordering across runs.
    again = [c.item_id for c in store.find_candidates(1, q, k=20) if c.item_id is not None]
    assert again == item_order


def test_hybrid_derived_booleans(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    _seed_hybrid_fixture(store, project_id=1)
    q = _query()
    cands = {c.item_id: c for c in store.find_candidates(1, q, k=20) if c.item_id}
    top = cands[5]
    assert top.same_error_hash and top.same_exception_fp
    assert top.launch_distance == abs(10 - 5)
    # item 1 shares the query's test_case_hash (1001).
    assert cands[1].same_test_case


# --------------------------------------------------------------------------- #
# #8 trgm fallback
# --------------------------------------------------------------------------- #
def test_trgm_fallback_when_fts_empty(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    _seed_hybrid_fixture(store, project_id=1)
    # salient terms that hit no tsvector token; exception name is a 1-char typo of
    # the stored exc_text; emb_model_ver mismatch disables the dense CTE so only
    # the lexical trgm fallback can return rows.
    q = QuerySignature(
        exception_fp=501,
        error_hash=9001,
        top_frames=[],
        template_ids=[1, 2, 3],
        salient_terms=["zzqqxxnomatchtoken"],
        exception_names=["NullPointerExceptionX"],
        emb=NEAR_VEC,
        emb_model_ver=999,
    )
    cands = store.find_candidates(1, q, k=20)
    items = {c.item_id for c in cands if c.item_id}
    assert items, "trgm fallback returned no candidates"
    assert items <= {1, 2, 3, 4, 5}  # only the near-duplicate exc_text matches
    assert all(c.dense_rank is None for c in cands if c.item_id)  # dense disabled


# --------------------------------------------------------------------------- #
# #9 emb_model_ver isolation
# --------------------------------------------------------------------------- #
def test_emb_model_version_isolation(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    # Two near-duplicates: item 1 at ver=1, item 2 at ver=2, same text/emb.
    store.upsert_items(
        [
            TestItemIn(item_id=1, project_id=1, launch_id=1, issue_type="pb001"),
            TestItemIn(item_id=2, project_id=1, launch_id=1, issue_type="pb001"),
        ]
    )
    store.upsert_signatures(
        [
            SignatureIn(
                project_id=1,
                item_id=1,
                exception_fp=1,
                error_hash=1,
                exc_text="NullPointerException",
                msg_text="pool timeout",
                emb=NEAR_VEC,
                emb_model_ver=1,
            ),
            SignatureIn(
                project_id=1,
                item_id=2,
                exception_fp=1,
                error_hash=1,
                exc_text="NullPointerException",
                msg_text="pool timeout",
                emb=NEAR_VEC,
                emb_model_ver=2,
            ),
        ]
    )
    q = QuerySignature(
        exception_fp=1,
        error_hash=1,
        top_frames=[],
        template_ids=[],
        salient_terms=['"NullPointerException"', "pool timeout"],
        exception_names=["NullPointerException"],
        emb=NEAR_VEC,
        emb_model_ver=1,
    )
    by_item = {c.item_id: c for c in store.find_candidates(1, q, k=20) if c.item_id}
    # Dense only sees the current version (item 1); item 2 is lexical-only.
    assert by_item[1].dense_rank is not None
    assert by_item[2].dense_rank is None
    assert by_item[2].sparse_rank is not None  # still reachable lexically


# --------------------------------------------------------------------------- #
# #10 mode matching
# --------------------------------------------------------------------------- #
def test_mode_matching_fp_first_and_cosine(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    store.upsert_items([TestItemIn(item_id=1, project_id=1, launch_id=1, issue_type="pb001")])
    kb = PgKBStore(pool)
    # Confirmed mode whose centroid == the query embedding (cosine 1.0).
    centroid_mode = kb.spawn_candidate_mode(
        ModeIn(
            project_id=1,
            status="confirmed",
            label="pb001",
            label_source="human",
            centroid=NEAR_VEC,
            emb_model_ver=1,
            exception_fps=[999],
        ),
        seed_item_ids=[1],
    )
    # Fingerprint-overlap mode (no centroid match to the query vector).
    fp_mode = kb.spawn_candidate_mode(
        ModeIn(
            project_id=1,
            status="confirmed",
            label="ab001",
            label_source="human",
            centroid=_far_vec(3),
            emb_model_ver=1,
            exception_fps=[501],
        ),
        seed_item_ids=[],
    )
    q = _query()  # exception_fp=501, emb=NEAR_VEC
    modes = kb.match_modes(1, q)
    assert modes[0].mode_id == fp_mode  # fp_hit ordered first
    assert modes[0].matched_by == "hash"
    centroid_hit = next(m for m in modes if m.mode_id == centroid_mode)
    assert centroid_hit.cosine is not None and centroid_hit.cosine > 0.9


def test_merge_modes_support_weighted_centroid(pool: ConnectionPool) -> None:
    # Two centroids differing on the first two dims, with supports 3 and 1: the
    # merged centroid must be the support-weighted average (spec 02 §4.3).
    kb = PgKBStore(pool)
    dst_vec = [0.0] * _DIMS
    dst_vec[0], dst_vec[1] = 1.0, 0.0
    src_vec = [0.0] * _DIMS
    src_vec[0], src_vec[1] = 0.0, 1.0
    dst = kb.spawn_candidate_mode(
        ModeIn(
            project_id=1,
            status="confirmed",
            label="pb001",
            centroid=dst_vec,
            emb_model_ver=1,
            exception_fps=[1],
        ),
        seed_item_ids=[],
    )
    src = kb.spawn_candidate_mode(
        ModeIn(
            project_id=1,
            status="confirmed",
            label="pb001",
            centroid=src_vec,
            emb_model_ver=1,
            exception_fps=[2],
        ),
        seed_item_ids=[],
    )
    # Set known supports directly (weights of the average).
    with pool.connection() as conn:
        conn.execute("UPDATE analyzer.failure_mode SET support=3 WHERE mode_id=%s", (dst,))
        conn.execute("UPDATE analyzer.failure_mode SET support=1 WHERE mode_id=%s", (src,))

    kb.merge_modes(1, src_mode_id=src, dst_mode_id=dst)

    with pool.connection() as conn:
        centroid_text, support, status_src = conn.execute(
            "SELECT centroid::text, support, "
            "(SELECT status FROM analyzer.failure_mode WHERE mode_id=%s) "
            "FROM analyzer.failure_mode WHERE mode_id=%s",
            (src, dst),
        ).fetchone()
    merged = [float(x) for x in centroid_text.strip("[]").split(",")]
    # weighted avg: (dst*3 + src*1) / 4  ->  dim0 = 3/4, dim1 = 1/4
    assert merged[0] == pytest.approx(0.75, abs=1e-2)
    assert merged[1] == pytest.approx(0.25, abs=1e-2)
    assert all(abs(v) < 1e-2 for v in merged[2:])
    assert support == 4  # supports summed
    assert status_src == "retired"  # source retired


# --------------------------------------------------------------------------- #
# #11 tenancy — no cross-project leakage
# --------------------------------------------------------------------------- #
def test_tenancy_no_cross_project_leakage(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    _seed_hybrid_fixture(store, project_id=1)
    _seed_hybrid_fixture(store, project_id=2)
    # A project-2-ONLY marker item with an item_id that does not exist in project
    # 1, and content identical to the query. If retrieval leaked across tenants it
    # would be a top hit for project 1; its distinct id makes a leak detectable
    # (the colliding 1..50 ids in the shared fixture cannot).
    marker_id = 999_002
    store.upsert_items(
        [TestItemIn(item_id=marker_id, project_id=2, launch_id=100, issue_type="pb001")]
    )
    store.upsert_signatures(
        [
            SignatureIn(
                project_id=2,
                item_id=marker_id,
                exception_fp=501,
                error_hash=9001,
                template_ids=[1, 2, 3],
                exc_text="NullPointerException",
                msg_text="connection pool timeout exhausted",
                frames_text="com.foo.Bar.baz",
                emb=NEAR_VEC,
                emb_model_ver=1,
            )
        ]
    )
    q = _query()
    a = store.find_candidates(1, q, k=50)
    returned = {c.item_id for c in a if c.item_id is not None}
    assert returned, "project 1 retrieval returned nothing (fixture sanity)"
    # The project-2-only marker must never surface in project-1 results.
    assert marker_id not in returned
    # And every returned stage-B item must belong to project 1.
    with pool.connection() as conn:
        for item_id in returned:
            owner = conn.execute(
                "SELECT 1 FROM analyzer.test_item WHERE item_id=%s AND project_id=1",
                (item_id,),
            ).fetchone()
            assert owner is not None, f"item {item_id} not owned by project 1"

    # llm_cache is keyed by project_id: A's key never returns B's row.
    cache = PgLlmCacheStore(pool)
    cache.put(1, "shared-key", "explainer", "qwen3:4b", {"p": 1})
    cache.put(2, "shared-key", "explainer", "qwen3:4b", {"p": 2})
    assert cache.get(1, "shared-key") == {"p": 1}
    assert cache.get(2, "shared-key") == {"p": 2}


# --------------------------------------------------------------------------- #
# #12 deletes
# --------------------------------------------------------------------------- #
def test_delete_launches_retains_label_events(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    _seed_hybrid_fixture(store, project_id=1)
    labels = PgLabelStore(pool)
    labels.append_event(
        LabelEventIn(project_id=1, item_id=1, new_label="pb001", source="rp_defect_update")
    )
    removed = store.delete_launches(1, [100])
    assert removed >= 50
    with pool.connection() as conn:
        for table in ("test_item", "failure_signature"):
            n = conn.execute(
                f"SELECT count(*) FROM analyzer.{table} WHERE project_id=1"
            ).fetchone()[0]
            assert n == 0
        # label_event survives the delete.
        events = conn.execute(
            "SELECT count(*) FROM analyzer.label_event WHERE project_id=1"
        ).fetchone()[0]
        assert events == 1


def test_delete_project_purges_derived_data_but_keeps_label_events(pool: ConnectionPool) -> None:
    # RP's "Generate index" is delete->rebuild via this route, so delete_project
    # must wipe only DERIVED data and keep the append-only label_event log — else
    # every reindex destroys the project's learning history.
    store = PgRetrievalStore(pool)
    _seed_hybrid_fixture(store, project_id=1)
    PgKBStore(pool).spawn_candidate_mode(
        ModeIn(project_id=1, status="candidate", exception_fps=[1]), seed_item_ids=[1]
    )
    PgLabelStore(pool).append_event(
        LabelEventIn(project_id=1, item_id=1, new_label="pb001", source="human_ui")
    )
    PgDrain3StateStore(pool).save(1, b"blob", 0, {})
    store.delete_project(1)
    with pool.connection() as conn:
        # Derived tables are wiped (regenerated on reindex; test_history_stats is
        # rebuilt incrementally so it must not linger and double-count).
        for table in (
            "test_item",
            "failure_signature",
            "failure_mode",
            "mode_membership",
            "drain3_state",
            "log_template",
            "suggestion",
            "test_history_stats",
            "project",
        ):
            n = conn.execute(
                f"SELECT count(*) FROM analyzer.{table} WHERE project_id=1"
            ).fetchone()[0]
            assert n == 0, f"{table} still has rows for project 1"
        # label_event survives (no FK to project; append-only learning log).
        events = conn.execute(
            "SELECT item_id, new_label FROM analyzer.label_event WHERE project_id=1"
        ).fetchall()
        assert events == [(1, "pb001")]

    # After reindex the item returns under its stable item_id and rejoins its
    # preserved event (the training stream is intact).
    store.upsert_items([TestItemIn(item_id=1, project_id=1, launch_id=1, issue_type="pb001")])
    with pool.connection() as conn:
        joined = conn.execute(
            "SELECT ti.item_id, le.new_label FROM analyzer.test_item ti "
            "JOIN analyzer.label_event le "
            "  ON le.project_id = ti.project_id AND le.item_id = ti.item_id "
            "WHERE ti.project_id=1"
        ).fetchall()
        assert joined == [(1, "pb001")]


def _orphan_reaper_seed(pool: ConnectionPool) -> PgRetrievalStore:
    """A project with one labeled item, then genuinely deleted (item orphaned)."""
    store = PgRetrievalStore(pool)
    store.upsert_items([TestItemIn(item_id=1, project_id=1, launch_id=1, issue_type="pb001")])
    PgLabelStore(pool).append_event(
        LabelEventIn(project_id=1, item_id=1, new_label="pb001", source="human_ui")
    )
    store.delete_project(1)  # genuine deletion: test_item gone, label_event kept
    return store


def _label_events(pool: ConnectionPool) -> int:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT count(*) FROM analyzer.label_event WHERE project_id=1"
        ).fetchone()[0]


def _orphan_tombstones(pool: ConnectionPool) -> int:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT count(*) FROM analyzer.label_event_orphan WHERE project_id=1"
        ).fetchone()[0]


def _backdate_tombstone(pool: ConnectionPool, days: int) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE analyzer.label_event_orphan "
            "SET first_orphaned_at = now() - make_interval(days => %s) WHERE project_id=1",
            (days,),
        )


def test_reap_marks_but_spares_freshly_orphaned_label_events(pool: ConnectionPool) -> None:
    # tech-debt #6: the first pass only MARKS a genuinely-deleted item; nothing is
    # purged until the grace elapses, so a same-day reindex can still reclaim it.
    _orphan_reaper_seed(pool)
    store = PgRetrievalStore(pool)

    purged = store.reap_orphan_label_events(grace_days=30)

    assert purged == 0
    assert _label_events(pool) == 1  # history intact
    assert _orphan_tombstones(pool) == 1  # tombstoned, grace running


def test_reap_sweeps_label_events_orphaned_past_grace(pool: ConnectionPool) -> None:
    # An item orphaned continuously past the grace (genuine deletion, never rebuilt)
    # is finally purged, closing the orphan-accumulation leak.
    _orphan_reaper_seed(pool)
    store = PgRetrievalStore(pool)
    store.reap_orphan_label_events(grace_days=30)  # mark
    _backdate_tombstone(pool, days=31)  # grace has now elapsed

    purged = store.reap_orphan_label_events(grace_days=30)  # sweep

    assert purged == 1
    assert _label_events(pool) == 0  # orphan reclaimed
    assert _orphan_tombstones(pool) == 0  # tombstone cleaned up too


def test_reap_never_purges_mid_reindex_even_past_grace(pool: ConnectionPool) -> None:
    # The safety property: even if a tombstone is older than the grace, a reindex
    # that re-creates test_item clears it via UNMARK, so live learning history is
    # NEVER purged. Backdating past the grace makes the test independent of timing.
    _orphan_reaper_seed(pool)
    store = PgRetrievalStore(pool)
    store.reap_orphan_label_events(grace_days=30)  # mark
    _backdate_tombstone(pool, days=99)  # far past grace — maximally adversarial

    # Reindex: RP re-publishes the launch, test_item returns under its stable id.
    store.upsert_items([TestItemIn(item_id=1, project_id=1, launch_id=1, issue_type="pb001")])
    purged = store.reap_orphan_label_events(grace_days=30)

    assert purged == 0  # unmark cleared the tombstone before any sweep
    assert _label_events(pool) == 1  # history survived the reindex
    assert _orphan_tombstones(pool) == 0  # tombstone gone (item re-indexed)


def test_reap_disabled_grace_marks_but_never_sweeps(pool: ConnectionPool) -> None:
    # grace_days <= 0 disables the destructive sweep; an operator cannot set an
    # aggressive grace that reaps history.
    _orphan_reaper_seed(pool)
    store = PgRetrievalStore(pool)
    store.reap_orphan_label_events(grace_days=30)  # mark
    _backdate_tombstone(pool, days=999)

    purged = store.reap_orphan_label_events(grace_days=0)

    assert purged == 0
    assert _label_events(pool) == 1  # never swept


def test_delete_by_log_time_respects_log_time_max(pool: ConnectionPool) -> None:
    store = PgRetrievalStore(pool)
    old, new = NOW - timedelta(days=10), NOW
    store.upsert_items(
        [
            TestItemIn(item_id=1, project_id=1, launch_id=1, issue_type="pb001", log_time_max=old),
            TestItemIn(item_id=2, project_id=1, launch_id=1, issue_type="pb001", log_time_max=new),
        ]
    )
    removed = store.delete_by_time_range(1, "log_time", NOW - timedelta(days=5))
    assert removed == 1
    with pool.connection() as conn:
        left = conn.execute("SELECT item_id FROM analyzer.test_item WHERE project_id=1").fetchall()
        assert left == [(2,)]


# --------------------------------------------------------------------------- #
# #13 Drain3 CAS
# --------------------------------------------------------------------------- #
def test_drain3_concurrent_save_cas(pool: ConnectionPool) -> None:
    store = PgDrain3StateStore(pool)
    store.save(1, b"v0", 0, {})  # establishes state_version = 1
    barrier = threading.Barrier(2)
    results: list[bool] = []
    guard = threading.Lock()

    def worker(payload: bytes) -> None:
        barrier.wait()
        ok = store.save(1, payload, expected_version=1, config={})
        with guard:
            results.append(ok)

    threads = [threading.Thread(target=worker, args=(p,)) for p in (b"a", b"b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, True]  # exactly one CAS winner
    _, version = store.load(1)
    assert version == 2


# --------------------------------------------------------------------------- #
# #14 exact-scan vs HNSW — both query paths covered
# --------------------------------------------------------------------------- #
def test_hnsw_and_exact_scan_paths(pool: ConnectionPool, store_dsn: str) -> None:
    store = PgRetrievalStore(pool)
    _seed_hybrid_fixture(store, project_id=1)
    q = _query()

    # Exact-scan path (no HNSW index yet).
    exact = [c.item_id for c in store.find_candidates(1, q, k=20) if c.item_id]
    assert exact[:5] == [5, 4, 3, 2, 1]

    # Find the partition holding project 1 and build the HNSW index CONCURRENTLY,
    # exactly as the §2.5 maintenance job would.
    with pool.connection() as conn:
        part = conn.execute(
            "SELECT tableoid::regclass::text FROM analyzer.failure_signature "
            "WHERE project_id=1 LIMIT 1"
        ).fetchone()[0]
    with psycopg.connect(store_dsn, autocommit=True) as raw:
        raw.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS fs_hnsw_test ON {part} "
            "USING hnsw (emb halfvec_cosine_ops) WITH (m=16, ef_construction=96)"
        )
        # EXPLAIN proves the dense scan uses the HNSW index when a seqscan is off.
        raw.execute("SET enable_seqscan = off")
        plan = "\n".join(
            r[0]
            for r in raw.execute(
                f"EXPLAIN SELECT item_id FROM {part} "
                "WHERE emb IS NOT NULL ORDER BY emb <=> %s::halfvec(384) LIMIT 5",
                ("[" + ",".join(map(str, NEAR_VEC)) + "]",),
            ).fetchall()
        )
    assert "fs_hnsw_test" in plan, plan

    # HNSW path: the same store query still fuses correctly with the index present.
    hnsw = [c.item_id for c in store.find_candidates(1, q, k=20) if c.item_id]
    assert set(hnsw[:5]) == {1, 2, 3, 4, 5}
