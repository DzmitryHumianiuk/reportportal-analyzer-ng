"""The inspector must reuse the analyzer's *versioned* SQL, not a copy of it."""

from __future__ import annotations

from backend.queries_loader import load_queries


def test_loads_real_versioned_sql():
    q = load_queries()
    assert isinstance(q.HYBRID_RETRIEVAL_VERSION, int)
    # The fusion query must carry the RRF machinery verbatim.
    assert "rrf_score" in q.STAGE_B_HYBRID_SQL
    assert "FULL OUTER JOIN" in q.STAGE_B_HYBRID_SQL
    assert "array_jaccard" in q.STAGE_B_HYBRID_SQL
    assert "centroid" in q.STAGE_A_MODE_MATCH_SQL


def test_bind_numbered_maps_positional_placeholders():
    q = load_queries()
    sql, params = q.bind_numbered("SELECT $1, $2, $1", ["a", "b"])
    assert sql == "SELECT %s, %s, %s"
    assert params == ["a", "b", "a"]


def test_bind_numbered_escapes_literal_percent():
    q = load_queries()
    sql, _ = q.bind_numbered("a % b $1", ["x"])
    assert "%%" in sql  # trgm operator preserved, not read as a placeholder
