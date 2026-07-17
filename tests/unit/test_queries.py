"""Unit tests for the versioned hybrid-query module (spec 02 §5).

Pure-Python: no DB. Guards that the verbatim RRF SQL is preserved and that
``bind_numbered`` renders libpq ``$N`` placeholders to psycopg paramstyle
without corrupting the pg_trgm ``%`` operator.
"""

from __future__ import annotations

from analyzer_ng.db.repositories.queries import (
    HYBRID_RETRIEVAL_VERSION,
    STAGE_A_MODE_MATCH_SQL,
    STAGE_B_HYBRID_SQL,
    bind_numbered,
)


def test_version_is_pinned() -> None:
    assert HYBRID_RETRIEVAL_VERSION == 2


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
