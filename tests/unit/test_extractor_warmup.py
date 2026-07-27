"""Unit tests for the extractor cache-warmup enumeration (issue #5).

Pure-Python: no DB. Guards the verbatim enumeration SQL (project scoping,
role/freshness filters, one row per template set) and the stale-set selection —
hashing through the shared ``extractor_template_hash``, order-insensitive
dedupe, fresh-hash exclusion, newest-first ordering and the ``--limit`` bound.
"""

from __future__ import annotations

from analyzer_ng.llm.roles.extractor import extractor_template_hash
from analyzer_ng.llm.warmup import (
    FRESH_EXTRACTOR_HASHES_SQL,
    TEMPLATE_SETS_SQL,
    TemplateSet,
    stale_template_sets,
)

# ---------------------------------------------------------------------------- #
# SQL text guards (test_queries.py style: the selection must never silently
# lose its scoping or freshness semantics).
# ---------------------------------------------------------------------------- #


def test_template_sets_query_is_project_scoped_one_row_per_set() -> None:
    assert "WHERE fs.project_id = %(project)s" in TEMPLATE_SETS_SQL
    # One row per distinct template set, not per item (spec 04 §4.2).
    assert "GROUP BY fs.exception_fp, fs.template_ids" in TEMPLATE_SETS_SQL
    # Newest representative item — replayable and most likely to recur first.
    assert "max(fs.item_id) AS item_id" in TEMPLATE_SETS_SQL
    assert "ORDER BY item_id DESC" in TEMPLATE_SETS_SQL
    # Mirrors PgLlmFacts.load_signature: no test_item row → not replayable.
    assert "JOIN analyzer.test_item ti USING (project_id, item_id)" in TEMPLATE_SETS_SQL


def test_fresh_hashes_query_filters_role_project_and_freshness() -> None:
    assert "WHERE project_id = %(project)s" in FRESH_EXTRACTOR_HASHES_SQL
    assert "role = 'extractor'" in FRESH_EXTRACTOR_HASHES_SQL
    assert "template_hash IS NOT NULL" in FRESH_EXTRACTOR_HASHES_SQL
    # Same created_at window as the read paths (get_fresh / feature lookup):
    # a stale row misses there, so it must not suppress a warmup either.
    assert "created_at >= now() - make_interval(days => %(ttl_days)s)" in FRESH_EXTRACTOR_HASHES_SQL


# ---------------------------------------------------------------------------- #
# Stale-set selection.
# ---------------------------------------------------------------------------- #


def test_selection_uses_the_shared_template_hash() -> None:
    # The hash MUST come from extractor_template_hash — the single source of
    # truth shared with the cache write and the feature-time lookup — or the
    # warmup would enumerate sets production addresses under a different key.
    rows = [(11, [3, 1, 2], 100)]
    (only,) = stale_template_sets(rows, fresh_hashes=[])
    assert only == TemplateSet(
        template_hash=extractor_template_hash(11, [1, 2, 3]),
        exception_fp=11,
        template_ids=(3, 1, 2),
        item_id=100,
    )


def test_fresh_hashes_are_excluded() -> None:
    rows = [(11, [1, 2], 100), (22, [7], 90)]
    fresh = [extractor_template_hash(11, [1, 2])]
    out = stale_template_sets(rows, fresh)
    assert [ts.item_id for ts in out] == [90]


def test_same_set_in_different_order_collapses_to_newest_representative() -> None:
    # extractor_template_hash sorts template_ids, so [2, 1] and [1, 2] are one
    # template set; the first (newest, per the SQL ordering) representative wins.
    rows = [(11, [2, 1], 100), (11, [1, 2], 50)]
    out = stale_template_sets(rows, fresh_hashes=[])
    assert len(out) == 1
    assert out[0].item_id == 100


def test_limit_bounds_the_run() -> None:
    rows = [(fp, [fp], 1000 - fp) for fp in range(1, 6)]
    out = stale_template_sets(rows, fresh_hashes=[], limit=2)
    assert len(out) == 2
    # Input (newest-first) order is preserved; the bound truncates, not reorders.
    assert [ts.exception_fp for ts in out] == [1, 2]


def test_null_ish_row_values_normalize_like_the_fact_loader() -> None:
    # PgLlmFactLoader builds extractor input with exception_fp or 0 and
    # template_ids or []; the enumeration must hash the same normalization or
    # the warmed row would sit under a key the feature lookup never queries.
    rows = [(None, None, 5)]
    (only,) = stale_template_sets(rows, fresh_hashes=[])
    assert only.template_hash == extractor_template_hash(0, [])
    assert only.exception_fp == 0
    assert only.template_ids == ()
