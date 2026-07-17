"""Versioned hybrid-retrieval query module (spec 02 §5).

The two SQL constants below are **copied verbatim** from spec 02 §5.1 (stage-B
item-history hybrid, RRF k=60 fused in SQL) and §5.2 (stage-A mode-centroid
match). They carry libpq ``$N`` positional placeholders exactly as the spec
writes them; :func:`bind_numbered` renders them to psycopg's client-side
paramstyle at execution time without touching the query body (so the RRF fusion,
ranking, and deterministic tie-breaks are never reimplemented — global rule 1).

``HYBRID_RETRIEVAL_VERSION`` pins the fusion semantics: golden-file ordering
tests assert stability against this version, and the BM25 swap-in point noted in
§5.1 would bump it. Bump the version whenever either SQL constant changes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

# Bump whenever STAGE_B_HYBRID_SQL or STAGE_A_MODE_MATCH_SQL changes (spec 02 §5).
# v2: added deterministic tie-breaks to the dense-CTE LIMIT and the mode-match
# ORDER BY so rows tied on distance/score select stably (item_id / mode_id DESC).
HYBRID_RETRIEVAL_VERSION = 2

# Session tuning that must precede STAGE_B_HYBRID_SQL in the same transaction
# (SET LOCAL). pgvector >= 0.8 iterative scans make the post-filter on
# issue_type/emb_model_ver safe with an HNSW index; harmless under exact scan.
SESSION_TUNING = (
    "SET LOCAL hnsw.ef_search = 80",
    "SET LOCAL hnsw.iterative_scan = relaxed_order",  # pgvector >= 0.8
)

# --------------------------------------------------------------------------- #
# spec 02 §5.1 — stage B: item-history hybrid (lexical + dense, RRF k=60 in SQL)
# params: $1 project_id, $2 emb_model_ver, $3 salient-terms string,
#         $4 query halfvec literal, $5 exception-name string for trgm fallback,
#         $6 query template_ids bigint[], $7 k
# --------------------------------------------------------------------------- #
STAGE_B_HYBRID_SQL = """
WITH q AS (
    SELECT websearch_to_tsquery('simple', $3) AS tsq
),
lex AS (                                          -- lexical top-50, field-boosted
    SELECT fs.item_id,
           ts_rank_cd('{0.1, 0.2, 0.4, 1.0}',    -- weights {D,C,B,A}: tmpl,frames,msg,exc
                      fs.signature_tsv, q.tsq) AS lex_score,
           row_number() OVER (
               ORDER BY ts_rank_cd('{0.1,0.2,0.4,1.0}', fs.signature_tsv, q.tsq) DESC,
                        fs.item_id DESC) AS l_rank
    FROM analyzer.failure_signature fs
    JOIN analyzer.test_item ti USING (project_id, item_id)
    CROSS JOIN q
    WHERE fs.project_id = $1
      AND ti.issue_type IS NOT NULL
      AND ti.issue_type_group <> 'ti'
      AND fs.signature_tsv @@ q.tsq
    ORDER BY lex_score DESC, fs.item_id DESC
    LIMIT 50
),
lex_trgm AS (                                     -- fallback when FTS found nothing
    SELECT fs.item_id,
           similarity(fs.exc_text, $5) AS lex_score,
           row_number() OVER (ORDER BY similarity(fs.exc_text, $5) DESC,
                              fs.item_id DESC) AS l_rank
    FROM analyzer.failure_signature fs
    JOIN analyzer.test_item ti USING (project_id, item_id)
    WHERE fs.project_id = $1
      AND ti.issue_type IS NOT NULL AND ti.issue_type_group <> 'ti'
      AND $5 <> '' AND fs.exc_text % $5
      AND NOT EXISTS (SELECT 1 FROM lex)
    ORDER BY lex_score DESC, fs.item_id DESC
    LIMIT 50
),
sparse AS (
    SELECT * FROM lex UNION ALL SELECT * FROM lex_trgm
),
dense AS (                                        -- dense top-50 (HNSW or exact per §2.5)
    SELECT fs.item_id,
           1 - (fs.emb <=> $4::halfvec(384)) AS cosine,
           row_number() OVER (ORDER BY fs.emb <=> $4::halfvec(384),
                              fs.item_id DESC) AS d_rank
    FROM analyzer.failure_signature fs
    JOIN analyzer.test_item ti USING (project_id, item_id)
    WHERE fs.project_id = $1
      AND fs.emb_model_ver = $2
      AND fs.emb IS NOT NULL
      AND ti.issue_type IS NOT NULL
      AND ti.issue_type_group <> 'ti'
    ORDER BY fs.emb <=> $4::halfvec(384), fs.item_id DESC
    LIMIT 50
),
fused AS (                                        -- RRF, k = 60
    SELECT COALESCE(s.item_id, d.item_id)                    AS item_id,
           s.l_rank                                          AS sparse_rank,
           d.d_rank                                          AS dense_rank,
           s.lex_score, d.cosine,
           COALESCE(1.0 / (60 + s.l_rank), 0)
         + COALESCE(1.0 / (60 + d.d_rank), 0)                AS rrf_score
    FROM sparse s FULL OUTER JOIN dense d USING (item_id)
)
SELECT f.item_id, f.sparse_rank, f.dense_rank, f.lex_score, f.cosine, f.rrf_score,
       analyzer.array_jaccard(fs.template_ids, $6) AS jaccard_templates,
       fs.error_hash, fs.exception_fp, fs.template_ids,
       ti.issue_type, ti.test_case_hash, ti.launch_id, ti.launch_number,
       ti.is_auto_analyzed,
       le.source AS label_source, le.ts AS label_ts,
       mm.mode_id
FROM fused f
JOIN analyzer.failure_signature fs ON fs.project_id = $1 AND fs.item_id = f.item_id
JOIN analyzer.test_item          ti ON ti.project_id = $1 AND ti.item_id = f.item_id
LEFT JOIN LATERAL (
    SELECT source, ts FROM analyzer.label_event le
    WHERE le.project_id = $1 AND le.item_id = f.item_id
    ORDER BY le.ts DESC LIMIT 1
) le ON true
LEFT JOIN LATERAL (
    SELECT mode_id FROM analyzer.mode_membership mm
    WHERE mm.project_id = $1 AND mm.item_id = f.item_id
    ORDER BY mm.match_score DESC LIMIT 1
) mm ON true
ORDER BY f.rrf_score DESC, f.item_id DESC
LIMIT $7;
""".strip()

# --------------------------------------------------------------------------- #
# spec 02 §5.2 — stage A: mode-centroid match
# params: $1 project_id, $2 emb_model_ver, $3 query halfvec, $4 exception_fp bigint[]
# --------------------------------------------------------------------------- #
STAGE_A_MODE_MATCH_SQL = """
SELECT fm.mode_id, fm.status, fm.label, fm.label_source, fm.purity, fm.support,
       fm.representative_template_ids, fm.title,
       1 - (fm.centroid <=> $3::halfvec(384))          AS cosine,
       (fm.exception_fps && $4)                        AS fp_hit
FROM analyzer.failure_mode fm
WHERE fm.project_id = $1
  AND fm.status IN ('seed', 'candidate', 'confirmed')
  AND ((fm.centroid IS NOT NULL AND fm.emb_model_ver = $2) OR fm.exception_fps && $4)
ORDER BY fp_hit DESC,                                   -- exact fingerprint hits first
         fm.centroid <=> $3::halfvec(384) NULLS LAST,
         fm.mode_id DESC                                 -- deterministic tie-break
LIMIT 10;
""".strip()


_PARAM_RE = re.compile(r"\$(\d+)")


def bind_numbered(sql_text: str, params: Sequence[Any]) -> tuple[str, list[Any]]:
    """Render libpq ``$N`` placeholders to psycopg ``%s`` client-side paramstyle.

    ``params`` is 1-indexed by position: ``params[0]`` is ``$1``. Each ``$N`` in
    *occurrence order* becomes one ``%s`` and appends ``params[N-1]`` to the
    returned positional list, so a placeholder reused several times (``$1``,
    ``$4``, ``$5`` in §5.1) binds the same value each time. Any literal ``%`` in
    the body (the pg_trgm ``%`` operator) is doubled first so psycopg does not
    read it as a placeholder — the query text is otherwise untouched.
    """
    escaped = sql_text.replace("%", "%%")
    ordered: list[Any] = []

    def _sub(match: re.Match[str]) -> str:
        ordered.append(params[int(match.group(1)) - 1])
        return "%s"

    return _PARAM_RE.sub(_sub, escaped), ordered
