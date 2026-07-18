"""T2.3 acceptance (G2 e2e) — grouping → matching → decision → suggestions.

Drives the bound :class:`PipelineHandlers` analyze/suggest/cluster/search routes
against a real migrated pgvector database with a deterministic fake embedder and
the real seed KB. A synthetic launch mixes three failure kinds:

* **OOM burst** (5 items, shared exception_fp) → one group, burst ``si_prior``,
  seed mode ``oom_java`` (si, 0.8) → auto-labeled ``si``;
* **NPE** matching a human-labeled history item (same ``error_hash``) → Stage-A
  inherit → auto-labeled ``pb001`` with ``relevantItem`` set;
* **benign** log (no exception, no seed) → abstain (stays ``ti``, omitted from the
  analyze reply).

Then determinism under shuffled input order, and the suggest read path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import uuid4

import numpy as np
import psycopg
import pytest
import xxhash
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from analyzer_ng.amqp.models import (
    AnalyzerConf,
    DefectUpdate,
    Launch,
    LaunchInfoForClustering,
    Log,
    SearchLogs,
)
from analyzer_ng.amqp.models import TestItem as RPItem
from analyzer_ng.amqp.models import TestItemInfo as RPItemInfo
from analyzer_ng.core.handlers import PipelineHandlers
from analyzer_ng.db.repositories.kb import PgKBStore
from analyzer_ng.db.startup import bootstrap_and_migrate
from analyzer_ng.seeds.loader import load_seed_kb

PROJECT = 4242
EMB_VER = 1

NPE_MSG = (
    "java.lang.NullPointerException: Cannot invoke service\n"
    "\tat com.acme.svc.OrderService.process(OrderService.java:42)\n"
    "\tat com.acme.svc.OrderController.handle(OrderController.java:20)"
)
OOM_MSG = (
    "java.lang.OutOfMemoryError: Java heap space\n"
    "\tat com.acme.cache.LoadingCache.grow(LoadingCache.java:88)"
)
BENIGN_MSG = "Screenshot captured and attached for manual review"


class FakeEmbedder:
    """Deterministic 384-d unit-vector embedder (same text → identical vector)."""

    emb_model_ver = "fake-r1"
    dims = 384

    def embed(self, text: str) -> np.ndarray:
        seed = xxhash.xxh3_64_intdigest(text.encode("utf-8")) % (2**32)
        v = np.random.default_rng(seed).standard_normal(self.dims).astype(np.float32)
        return v / np.linalg.norm(v)


def _dsn_for_db(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    if params.get("host") in (None, "localhost"):
        params["host"] = "127.0.0.1"
    params["dbname"] = dbname
    return make_conninfo(**params)


@contextmanager
def _fresh_db(base_dsn: str) -> Iterator[ConnectionPool]:
    dbname = f"anz_t23_{uuid4().hex[:12]}"
    dsn = _dsn_for_db(base_dsn, dbname)
    bootstrap_and_migrate(dsn, create_db=True, attempts=3, delay=0.0)
    pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True)
    try:
        yield pool
    finally:
        pool.close()
        with psycopg.connect(_dsn_for_db(base_dsn, "postgres"), autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


@pytest.fixture
def db_factory(postgres_dsn: str) -> Iterator[Callable[[], ConnectionPool]]:
    pools: list[ConnectionPool] = []
    cms: list = []

    def make() -> ConnectionPool:
        cm = _fresh_db(postgres_dsn)
        pool = cm.__enter__()
        cms.append(cm)
        pools.append(pool)
        return pool

    try:
        yield make
    finally:
        for cm in cms:
            cm.__exit__(None, None, None)


def _bound_handlers(pool: ConnectionPool) -> PipelineHandlers:
    h = PipelineHandlers()
    h.bind(
        pool,
        embedder=FakeEmbedder(),
        emb_model_ver=EMB_VER,
        emb_model_tag="fake-r1",
        seed_kb=load_seed_kb(PgKBStore(pool)),
    )
    return h


def _log(log_id: int, message: str) -> Log:
    return Log(logId=log_id, logLevel=40000, message=message)


def _item(item_id: int, name: str, message: str, tch: int, issue: str = "") -> RPItem:
    return RPItem(
        testItemId=item_id,
        isAutoAnalyzed=False,
        issueType=issue,
        testCaseHash=tch,
        testItemName=name,
        logs=[_log(item_id * 10, message)],
    )


def _analysis_launch(order: list[int] | None = None) -> Launch:
    """Launch under analysis: 5 OOM (burst) + 1 NPE + 1 benign; all unlabeled."""
    items = [
        *[_item(100 + i, f"oom_test_{i}", OOM_MSG, tch=1000 + i) for i in range(5)],
        _item(200, "order_flow", NPE_MSG, tch=2000),
        _item(300, "screenshot_step", BENIGN_MSG, tch=3000),
    ]
    if order is not None:
        by_id = {it.testItemId: it for it in items}
        items = [by_id[i] for i in order]
    return Launch(
        launchId=1000,
        project=PROJECT,
        launchName="Nightly",
        launchNumber=7,
        analyzerConfig=AnalyzerConf(analyzerMode="ALL"),
        testItems=items,
    )


def _history_launch() -> Launch:
    """Prior launch with an NPE item, later human-labeled pb001 (Stage-A source)."""
    return Launch(
        launchId=900,
        project=PROJECT,
        launchName="Nightly",
        launchNumber=6,
        testItems=[_item(800, "order_flow", NPE_MSG, tch=2000, issue="ti001")],
    )


def _seed_history_and_label(handlers: PipelineHandlers) -> None:
    handlers.index([_history_launch()])
    # Human confirms pb001 → label_event(source=rp_defect_update) → Stage-A inherits.
    handlers.defect_update(DefectUpdate(project=PROJECT, itemsToUpdate={800: "pb001"}))


def _labels(pool: ConnectionPool) -> dict[int, str | None]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT item_id, issue_type FROM analyzer.test_item WHERE project_id=%s",
            (PROJECT,),
        ).fetchall()
    return {int(r[0]): r[1] for r in rows}


# --------------------------------------------------------------------------- #
# analyze end-to-end
# --------------------------------------------------------------------------- #
def test_g2_analyze_groups_matches_and_decides(db_factory) -> None:
    pool = db_factory()
    handlers = _bound_handlers(pool)
    _seed_history_and_label(handlers)

    launch = _analysis_launch()
    handlers.index([launch])  # index the launch under analysis (RP indexes then analyzes)
    results = handlers.analyze([launch])

    by_item = {r.testItem: r for r in results}
    # NPE inherits the human pb001 via Stage A, relevantItem = history item 800.
    assert by_item[200].issueType == "pb001"
    assert by_item[200].relevantItem == 800
    # All five OOM items auto-labeled 'si001' (seed prior 0.8 ≥ τ_auto → RP locator).
    for i in range(5):
        assert by_item[100 + i].issueType == "si001"
    # The benign item abstained → omitted from the analyze reply (stays ti).
    assert 300 not in by_item

    labels = _labels(pool)
    assert labels[200] == "pb001"
    assert all(labels[100 + i] == "si001" for i in range(5))
    assert labels[300] is None  # never auto-labeled


def test_g2_suggestions_written_with_features_and_launch_groups(db_factory) -> None:
    pool = db_factory()
    handlers = _bound_handlers(pool)
    _seed_history_and_label(handlers)
    launch = _analysis_launch()
    handlers.index([launch])
    handlers.analyze([launch])

    with pool.connection() as conn:
        # Every decision wrote a suggestion row (7 items under analysis).
        sug = conn.execute(
            "SELECT item_id, predicted_label, confidence, features, model_ver, group_id "
            "FROM analyzer.suggestion WHERE project_id=%s AND launch_id=1000 ORDER BY item_id",
            (PROJECT,),
        ).fetchall()
        assert len(sug) == 7
        for _iid, _label, _conf, features, model_ver, group_id in sug:
            # 39 classical (spec 03 §6.4) + 2 LLM-extractor (spec 04 §4.2)
            # + 4 discriminant-agreement columns (2026-07-18 errata).
            assert len(features) == 45  # full feature snapshot (training reads these)
            assert model_ver.startswith("rule_cold")
            assert group_id is not None

        # launch_group rows persisted; the OOM group carries the burst si_prior.
        groups = conn.execute(
            "SELECT member_count, si_prior, dominant FROM analyzer.launch_group "
            "WHERE project_id=%s AND launch_id=1000 ORDER BY member_count DESC",
            (PROJECT,),
        ).fetchall()
        assert groups[0][0] == 5  # OOM group has 5 members
        assert groups[0][1] >= 0.5  # burst si_prior fired
        assert groups[0][2] is True

        # Exactly one lazy per-project copy of the matched seed mode(s).
        seeded = conn.execute(
            "SELECT seed_key, count(*) FROM analyzer.failure_mode "
            "WHERE project_id=%s AND seed_key IS NOT NULL GROUP BY seed_key",
            (PROJECT,),
        ).fetchall()
        assert all(cnt == 1 for _k, cnt in seeded)
        assert any(k == "oom_java" for k, _c in seeded)


def test_stage_a_respects_analyzer_mode_scope(db_factory) -> None:
    # CURRENT_LAUNCH bounds Stage A to the launch under analysis: the human-labeled
    # NPE lives in launch 900, so it is out of scope and must NOT be inherited.
    pool = db_factory()
    handlers = _bound_handlers(pool)
    _seed_history_and_label(handlers)

    launch = _analysis_launch()
    launch.analyzerConfig = AnalyzerConf(analyzerMode="CURRENT_LAUNCH")
    handlers.index([launch])
    results = handlers.analyze([launch])

    by_item = {r.testItem: r for r in results}
    assert 200 not in by_item  # NPE not inherited (history out of scope) → stays ti
    assert _labels(pool)[200] is None


def test_analyze_deterministic_under_shuffled_input(db_factory) -> None:
    order_a = [100, 101, 102, 103, 104, 200, 300]
    order_b = [300, 200, 104, 100, 103, 101, 102]

    def run(order: list[int]) -> dict[int, str | None]:
        pool = db_factory()
        handlers = _bound_handlers(pool)
        _seed_history_and_label(handlers)
        launch = _analysis_launch(order)
        handlers.index([launch])
        handlers.analyze([launch])
        return _labels(pool)

    labels_a = run(order_a)
    labels_b = run(order_b)
    # Same launch, different item order → identical final labels for the 7 items.
    for item_id in (100, 101, 102, 103, 104, 200, 300):
        assert labels_a[item_id] == labels_b[item_id]


# --------------------------------------------------------------------------- #
# suggest read path
# --------------------------------------------------------------------------- #
def test_suggest_returns_top_suggestion_for_known_failure(db_factory) -> None:
    pool = db_factory()
    handlers = _bound_handlers(pool)
    _seed_history_and_label(handlers)

    info = RPItemInfo(
        testItemId=555,
        launchId=1000,
        launchName="Nightly",
        launchNumber=7,
        project=PROJECT,
        testItemName="order_flow",
        testCaseHash=2000,
        analyzerConfig=AnalyzerConf(analyzerMode="ALL", numberOfLogLines=5, minShouldMatch=80),
        logs=[_log(5550, NPE_MSG)],
    )
    suggestions = handlers.suggest(info)
    assert suggestions, "a known failure should yield at least one suggestion"
    top = suggestions[0]
    assert top.issueType == "pb001"
    assert top.relevantItem == 800
    assert top.matchScore == pytest.approx(95.0, abs=0.5)
    assert top.modelFeatureNames and top.modelFeatureValues
    assert "gbm=none" in top.modelInfo
    assert top.methodName in ("auto_analysis", "suggestion")

    # A suggestion row is persisted for the read-path decision too.
    with pool.connection() as conn:
        n = conn.execute(
            "SELECT count(*) FROM analyzer.suggestion WHERE project_id=%s AND item_id=555",
            (PROJECT,),
        ).fetchone()[0]
    assert n == 1


def test_suggest_abstains_on_benign_log(db_factory) -> None:
    pool = db_factory()
    handlers = _bound_handlers(pool)
    info = RPItemInfo(
        testItemId=777,
        launchId=1000,
        project=PROJECT,
        testItemName="screenshot_step",
        logs=[_log(7770, BENIGN_MSG)],
    )
    # No history, no exception, no seed → below τ_suggest → empty reply (legacy).
    assert handlers.suggest(info) == []


# An exception-bearing signature that no history/seed can classify: the reply is
# empty (abstain) but a decision WAS made, so §6.6 requires the suggestion row —
# else a human label later has no feature snapshot and is dropped from training.
UNSEEDED_EXC_MSG = (
    "com.acme.WidgetGlitchException: widget glitch at node 7\n"
    "\tat com.acme.widget.WidgetService.spin(WidgetService.java:13)"
)


def test_suggest_abstain_still_writes_suggestion_row(db_factory) -> None:
    pool = db_factory()
    handlers = _bound_handlers(pool)
    info = RPItemInfo(
        testItemId=888,
        launchId=1000,
        project=PROJECT,
        testItemName="widget_check",
        logs=[_log(8880, UNSEEDED_EXC_MSG)],
    )
    # Abstain → empty reply (wire byte-stable), but the decision must be persisted.
    assert handlers.suggest(info) == []
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT predicted_label FROM analyzer.suggestion "
            "WHERE project_id=%s AND item_id=888",
            (PROJECT,),
        ).fetchone()
    assert row is not None, "abstained suggest must persist a suggestion row (spec 03 §6.6)"
    assert row[0] == "ti"


# --------------------------------------------------------------------------- #
# KB-mode learning loop — mode_membership + purity/centroid (live-fix Bug 1)
# --------------------------------------------------------------------------- #
def _mode_row(pool: ConnectionPool, seed_key: str) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT mode_id, purity, support, centroid IS NOT NULL AS has_centroid, "
            "emb_model_ver FROM analyzer.failure_mode WHERE project_id=%s AND seed_key=%s",
            (PROJECT, seed_key),
        ).fetchone()
    assert row is not None, f"seed mode {seed_key} not created"
    return {
        "mode_id": row[0], "purity": row[1], "support": row[2],
        "has_centroid": row[3], "emb_model_ver": row[4],
    }


def test_seed_mode_match_writes_membership_and_sets_matched_mode_id(db_factory) -> None:
    # live-fix Bug 1: an OOM item hits the oom_java seed mode → a mode_membership row
    # must be written and suggestion.matched_mode_id must be set (both were missing on
    # image eb2343e, so the whole KB-mode loop was dead).
    pool = db_factory()
    handlers = _bound_handlers(pool)
    launch = _analysis_launch()
    handlers.index([launch])
    handlers.analyze([launch])

    oom = _mode_row(pool, "oom_java")
    with pool.connection() as conn:
        members = conn.execute(
            "SELECT item_id, matched_by FROM analyzer.mode_membership "
            "WHERE project_id=%s AND mode_id=%s ORDER BY item_id",
            (PROJECT, oom["mode_id"]),
        ).fetchall()
        matched_mode_ids = conn.execute(
            "SELECT DISTINCT matched_mode_id FROM analyzer.suggestion "
            "WHERE project_id=%s AND item_id BETWEEN 100 AND 104",
            (PROJECT,),
        ).fetchall()
    # All five grouped OOM items became members of the seed mode.
    assert {m[0] for m in members} == {100, 101, 102, 103, 104}
    assert all(m[1] == "lexical" for m in members)  # seed rule hit → lexical
    # The suggestions now carry the matched mode id (was NULL on eb2343e).
    assert matched_mode_ids == [(oom["mode_id"],)]
    # The mode's emb_model_ver is stamped so update_purity can build a centroid.
    assert oom["emb_model_ver"] == EMB_VER


def test_defect_update_moves_purity_support_and_sets_centroid(db_factory) -> None:
    # After membership exists, a defect_update (label_event) must move the mode's
    # purity/support and seed its centroid from member embeddings (§6.7).
    pool = db_factory()
    handlers = _bound_handlers(pool)
    launch = _analysis_launch()
    handlers.index([launch])
    handlers.analyze([launch])

    before = _mode_row(pool, "oom_java")
    assert before["support"] == 0 and before["purity"] == 0 and not before["has_centroid"]

    # A human confirms the five OOM items as si001 (the burst si prior) via RP UI.
    handlers.defect_update(
        DefectUpdate(project=PROJECT, itemsToUpdate={100 + i: "si001" for i in range(5)})
    )

    after = _mode_row(pool, "oom_java")
    assert after["support"] == 5, "support = labeled members"
    assert after["purity"] == pytest.approx(1.0), "all members share the si label → pure"
    assert after["has_centroid"], "centroid seeded from member embeddings (§9)"


# --------------------------------------------------------------------------- #
# cluster + search
# --------------------------------------------------------------------------- #
def test_cluster_groups_launch_with_stable_ids(db_factory) -> None:
    pool = db_factory()
    handlers = _bound_handlers(pool)
    launch = _analysis_launch()
    info = LaunchInfoForClustering(launch=launch, project=PROJECT, numberOfLogLines=-1)

    first = handlers.cluster(info)
    second = handlers.cluster(info)
    assert first.project == PROJECT and first.launchId == 1000
    # OOM cluster (5 items) present; ids are stable across re-runs and positive int53.
    sizes = sorted(len(c.itemIds) for c in first.clusters)
    assert sizes[-1] == 5
    assert all(0 < c.clusterId < (1 << 53) for c in first.clusters)
    assert {c.clusterId for c in first.clusters} == {c.clusterId for c in second.clusters}
    oom = max(first.clusters, key=lambda c: len(c.itemIds))
    assert "OutOfMemoryError" in oom.clusterMessage


def test_search_returns_scored_item_matches_respecting_launch_filter(db_factory) -> None:
    pool = db_factory()
    handlers = _bound_handlers(pool)
    _seed_history_and_label(handlers)

    request = SearchLogs(
        launchId=1000,
        launchName="Nightly",
        itemId=999,
        projectId=PROJECT,
        filteredLaunchIds=[900],  # only the history launch is eligible
        logMessages=[NPE_MSG],
        logLines=5,
    )
    hits = handlers.search(request)
    assert any(h.testItemId == 800 for h in hits)
    assert all(0.0 <= h.matchScore <= 100.0 for h in hits)

    # A launch filter excluding the history launch yields no hit for item 800.
    request.filteredLaunchIds = [12345]
    assert all(h.testItemId != 800 for h in handlers.search(request))
