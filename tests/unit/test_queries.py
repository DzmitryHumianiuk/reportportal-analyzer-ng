"""Unit tests for the versioned hybrid-query module (spec 02 §5).

Pure-Python: no DB. Guards that the verbatim RRF SQL is preserved and that
``bind_numbered`` renders libpq ``$N`` placeholders to psycopg paramstyle
without corrupting the pg_trgm ``%`` operator.
"""

from __future__ import annotations

from analyzer_ng.db.repositories.queries import (
    HYBRID_RETRIEVAL_VERSION,
    SEARCH_TI_HYBRID_SQL,
    STAGE_A_MODE_MATCH_SQL,
    STAGE_B_HYBRID_SQL,
    bind_numbered,
)


def test_version_is_pinned() -> None:
    # Adding SEARCH_TI_HYBRID_SQL is a SEPARATE query, not a change to the versioned
    # stage-B fusion, so the golden-file ordering version must stay pinned.
    assert HYBRID_RETRIEVAL_VERSION == 3


def test_stage_b_projects_neighbour_text_for_boilerplate_guard() -> None:
    # errata: neighbour msg_text/exc_text are projected so the decision layer can run
    # the deterministic boilerplate-only guard on the GBM top-1 neighbour.
    assert "fs.msg_text, fs.exc_text" in STAGE_B_HYBRID_SQL


def test_dense_and_mode_match_carry_deterministic_tiebreaks() -> None:
    # A tie on cosine distance / score must resolve stably, not by physical row order.
    assert "ORDER BY fs.emb <=> $4::halfvec(384), fs.item_id DESC" in STAGE_B_HYBRID_SQL
    assert "fm.mode_id DESC" in STAGE_A_MODE_MATCH_SQL


def test_stage_b_preserves_rrf_k60_and_deterministic_tiebreaks() -> None:
    # RRF with k=60 fused in SQL (spec 02 §5.1) — the fusion must never be
    # silently reimplemented.
    assert "1.0 / (60 + s.l_rank)" in STAGE_B_HYBRID_SQL
    assert "1.0 / (60 + d.d_rank)" in STAGE_B_HYBRID_SQL
    # Deterministic tie-breaks (item_id DESC) keep golden ordering stable.
    assert "ORDER BY f.rrf_score DESC, f.item_id DESC" in STAGE_B_HYBRID_SQL
    # Only labeled, non-ti evidence is retrieved.
    assert "ti.issue_type IS NOT NULL" in STAGE_B_HYBRID_SQL
    assert "ti.issue_type_group <> 'ti'" in STAGE_B_HYBRID_SQL


def test_stage_a_orders_fingerprint_hits_first() -> None:
    assert "ORDER BY fp_hit DESC" in STAGE_A_MODE_MATCH_SQL
    assert "fm.status IN ('seed', 'candidate', 'confirmed')" in STAGE_A_MODE_MATCH_SQL


def test_search_ti_query_targets_ti_only_scoped_and_self_excluded() -> None:
    # The similar-TI search is the mirror of the decision path: TI-only, launch-scoped
    # to filteredLaunchIds ($8, empty = unrestricted), query item ($9) excluded.
    assert "ti.issue_type_group = 'ti'" in SEARCH_TI_HYBRID_SQL
    assert "ti.issue_type_group <> 'ti'" not in SEARCH_TI_HYBRID_SQL
    assert "ti.item_id <> $9" in SEARCH_TI_HYBRID_SQL
    launch_scope = "cardinality($8::bigint[]) = 0 OR ti.launch_id = ANY($8::bigint[])"
    assert launch_scope in SEARCH_TI_HYBRID_SQL
    # Same RRF k=60 fusion + deterministic tie-breaks as stage B (reused, not forked).
    assert "1.0 / (60 + s.l_rank)" in SEARCH_TI_HYBRID_SQL
    assert "1.0 / (60 + d.d_rank)" in SEARCH_TI_HYBRID_SQL
    assert "ORDER BY f.rrf_score DESC, f.item_id DESC" in SEARCH_TI_HYBRID_SQL
    # Returns the real RP log id for the reply's logId.
    assert "fs.error_log_id" in SEARCH_TI_HYBRID_SQL


def test_search_ti_query_binds_full_param_set() -> None:
    given = [7, 1, "terms", "[0,1]", "Exc", "{1,2}", 20, "{176}", 2765]
    sql, params = bind_numbered(SEARCH_TI_HYBRID_SQL, given)
    assert params.count(7) == SEARCH_TI_HYBRID_SQL.count("$1")
    assert "$" not in sql  # all numbered placeholders consumed
    assert "%s" in sql


def test_bind_numbered_maps_placeholders_in_occurrence_order() -> None:
    sql, params = bind_numbered("SELECT $1, $2, $1", ["a", "b"])
    assert sql == "SELECT %s, %s, %s"
    assert params == ["a", "b", "a"]


def test_bind_numbered_escapes_literal_percent_operator() -> None:
    # The pg_trgm `%` operator must be doubled so psycopg does not read it as a
    # placeholder; $N still renders to %s.
    sql, params = bind_numbered("WHERE exc % $1", ["typo"])
    assert sql == "WHERE exc %% %s"
    assert params == ["typo"]


def test_bind_numbered_binds_full_stage_b_param_set() -> None:
    given = [7, 1, "terms", "[0,1]", "Exc", "{1,2}", 20]
    sql, params = bind_numbered(STAGE_B_HYBRID_SQL, given)
    # $1 is reused many times; every occurrence binds the same value.
    assert params.count(7) == STAGE_B_HYBRID_SQL.count("$1")
    assert "$" not in sql  # all numbered placeholders consumed
    assert "%s" in sql
