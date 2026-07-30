"""Live Stage-B hybrid retrieval reconstruction.

Given an item's stored signature, re-run the analyzer's **versioned** hybrid RRF
SQL (``STAGE_B_HYBRID_SQL`` from ``queries.py``, spec 02 §5.1) against the
*current* database and return the candidate table exactly as the matcher would
see it: lexical rank, dense rank, RRF fused score, template Jaccard and the
per-candidate label. This is explicitly labelled "reconstructed against current
data" in the UI — it is a re-execution of the shipped fusion SQL, never a
reimplementation of the ranking.
"""

from __future__ import annotations

from typing import Any

from .db import Database
from .queries_loader import load_queries

_Q = load_queries()

HYBRID_RETRIEVAL_VERSION: int = _Q.HYBRID_RETRIEVAL_VERSION


def _halfvec_literal(emb: list[float] | None) -> str:
    """A syntactically valid halfvec(384) literal; zeros when the item is un-embedded."""
    if emb and len(emb) == 384:
        return "[" + ",".join(repr(float(x)) for x in emb) + "]"
    return "[" + ",".join(["0"] * 384) + "]"


def reconstruct_stage_b(
    db: Database,
    project_id: int,
    signature: dict[str, Any],
    *,
    k: int = 20,
) -> dict[str, Any]:
    """Re-run the hybrid RRF SQL for ``signature`` and return candidates + metadata.

    ``signature`` is the query item's failure_signature row (dict). We use its
    own text/template fields as the query, then drop the item itself from the
    result so candidates are *other* items it would retrieve.
    """
    exc_text = (signature.get("exc_text") or "").strip()
    msg_text = (signature.get("msg_text") or "").strip()
    template_ids = signature.get("template_ids") or []
    emb = signature.get("emb_vec")  # parsed list[float] or None
    emb_model_ver = signature.get("emb_model_ver") or 0
    self_item = signature.get("item_id")

    # $3 salient-terms string for websearch_to_tsquery (exception + message terms).
    salient = " ".join(t for t in (exc_text, msg_text) if t).strip()

    params = [
        project_id,  # $1
        emb_model_ver,  # $2
        salient,  # $3
        _halfvec_literal(emb),  # $4
        exc_text,  # $5 trgm fallback exception name
        template_ids,  # $6
        k,  # $7
    ]

    sql, ordered = _Q.bind_numbered(_Q.STAGE_B_HYBRID_SQL, params)

    rows = db.rows(sql, tuple(ordered))

    candidates: list[dict[str, Any]] = []
    for r in rows:
        candidates.append(
            {
                "item_id": r.get("item_id"),
                "is_self": r.get("item_id") == self_item,
                "sparse_rank": r.get("sparse_rank"),
                "dense_rank": r.get("dense_rank"),
                "lex_score": _f(r.get("lex_score")),
                "cosine": _f(r.get("cosine")),
                "rrf_score": _f(r.get("rrf_score")),
                "jaccard_templates": _f(r.get("jaccard_templates")),
                "error_hash": _s(r.get("error_hash")),
                "exception_fp": _s(r.get("exception_fp")),
                "issue_type": r.get("issue_type"),
                "launch_id": r.get("launch_id"),
                "launch_number": r.get("launch_number"),
                "is_auto_analyzed": r.get("is_auto_analyzed"),
                "label_source": r.get("label_source"),
                "mode_id": r.get("mode_id"),
            }
        )

    dense_active = bool(emb) and emb_model_ver != 0
    return {
        "hybrid_retrieval_version": HYBRID_RETRIEVAL_VERSION,
        "reconstructed": True,
        "query": {
            "salient_terms": salient,
            "exc_text": exc_text,
            "template_ids": [str(t) for t in template_ids],
            "emb_model_ver": emb_model_ver,
            "dense_active": dense_active,
        },
        "candidates": candidates,
        "note": (
            "Re-executed the analyzer's versioned Stage-C hybrid RRF SQL "
            "(internal name STAGE_B_HYBRID_SQL) "
            f"(HYBRID_RETRIEVAL_VERSION={HYBRID_RETRIEVAL_VERSION}) against current data."
            + (
                ""
                if dense_active
                else " Dense (vector) leg is inactive: this item has no embedding "
                "(emb_model_ver=0), so ranking is lexical-only."
            )
        ),
    }


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _s(v: Any) -> str | None:
    return None if v is None else str(v)
