"""Pydantic v2 models for the store layer (spec 02 §4.1, verbatim field names).

These are the DB-facing DTOs the six store contracts (§4.2–§4.4) accept and
return. Field names and defaults are copied from spec 02 §4.1; only the
one-field-per-line expansion of the spec's terse examples differs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MatchedBy = Literal["hash", "vector", "lexical", "human"]


class TestItemIn(BaseModel):
    """A failed test item received via ``index`` (spec 02 §2.4)."""

    item_id: int
    project_id: int
    launch_id: int
    launch_name: str = ""
    launch_number: int | None = None
    test_case_hash: int | None = None
    unique_id: str | None = None
    item_name: str = ""
    start_time: datetime | None = None
    is_auto_analyzed: bool = False
    issue_type: str | None = None
    log_count: int = 0
    log_time_max: datetime | None = None


class SignatureIn(BaseModel):
    """A failure signature document for a test item (spec 02 §2.5)."""

    project_id: int
    item_id: int
    exception_fp: int
    error_hash: int
    top_frames: list[str] = []
    template_ids: list[int] = []
    exc_text: str = ""
    msg_text: str = ""
    frames_text: str = ""
    tmpl_text: str = ""
    only_numbers: str = ""
    status_codes: list[str] = []
    urls: list[str] = []
    paths: list[str] = []
    emb: list[float] | None = None  # 384 floats; None => embed asynchronously
    emb_model_ver: int = 0
    # RP log id of the item's first ERROR log (spec 03 §8.2). The similar-TI search
    # reply must carry a real RP log id — the RP backend loads the log by this id and
    # drops any row it cannot find. None for items indexed before this was captured.
    error_log_id: int | None = None


class StoredSignature(BaseModel):
    """An item's persisted ``failure_signature`` identity, read back verbatim (§2.5).

    The *canonical* signature for read-path comparisons. ``error_hash`` and
    ``template_ids`` are computed at index time against that index's Drain3 state;
    the read path (analyze/suggest) mines against a read-only Drain clone whose
    templates have since drifted, so a recompute would differ. Any hash-identity
    comparison (Stage-A exact match, KB ``exception_fps`` GIN, burst novelty,
    launch-group fingerprint) MUST use these stored values so it only ever compares
    identities computed the same way (spec 03 §6.1 identity invariant).
    """

    project_id: int
    item_id: int
    exception_fp: int
    error_hash: int
    top_frames: list[str] = []
    template_ids: list[int] = []
    exc_text: str = ""
    msg_text: str = ""
    status_codes: list[str] = []
    emb_model_ver: int = 0


class QuerySignature(BaseModel):
    """Search-side view of a signature (built from the item under analysis)."""

    exception_fp: int
    error_hash: int
    top_frames: list[str]
    template_ids: list[int]
    salient_terms: list[str]  # for websearch_to_tsquery construction
    exception_names: list[str]  # for trgm fallback
    emb: list[float] | None
    emb_model_ver: int
    test_case_hash: int | None = None
    launch_id: int | None = None
    launch_number: int | None = None


class CandidateFilters(BaseModel):
    """Post-retrieval restrictions applied in Python (spec 02 §4.2)."""

    exclude_item_ids: list[int] = []  # e.g. the query item itself / same launch
    exclude_launch_ids: list[int] = []
    min_label_ts: datetime | None = None
    issue_type_groups: list[str] | None = None  # restrict evidence, rarely used


class Candidate(BaseModel):
    """A retrieval candidate: a KB mode (stage A) or labeled item (stage B)."""

    # identity
    item_id: int | None  # None for mode-only candidates
    mode_id: int | None  # set for stage-A KB matches (and stage-B via membership)
    # retrieval scores (CONTEXT §4-consilium field list)
    dense_rank: int | None = None
    sparse_rank: int | None = None
    rrf_score: float = 0.0
    cosine: float | None = None
    lex_score: float | None = None
    jaccard_templates: float = 0.0
    # label evidence
    issue_type: str | None = None
    label_source: Literal["seed", "human", "ai_suggested", "rp"] | None = None
    label_ts: datetime | None = None
    # cheap boolean/context features
    same_test_case: bool = False
    same_error_hash: bool = False
    same_exception_fp: bool = False
    # un-masked neighbour text (2026-07-18 errata) — carried so the decision layer can
    # run the deterministic boilerplate-only guard on the GBM top-1 neighbour.
    msg_text: str = ""
    exc_text: str = ""
    launch_distance: int | None = None  # |query.launch_number - cand.launch_number|
    launch_id: int | None = None  # candidate's launch (analyzerMode scope, §6.0)
    launch_name: str | None = None
    # KB-mode extras (stage A)
    mode_status: str | None = None
    mode_purity: float | None = None
    mode_support: int | None = None
    matched_by: MatchedBy | None = None
    # provenance for RP reply compatibility
    relevant_log_id: int | None = None


class ModeIn(BaseModel):
    """A failure-mode row to spawn/insert (spec 02 §2.6)."""

    project_id: int
    status: str = "candidate"
    label: str | None = None
    label_source: str | None = None
    centroid: list[float] | None = None
    emb_model_ver: int | None = None
    representative_template_ids: list[int] = []
    exception_fps: list[int] = []
    title: str | None = None
    summary: str | None = None


class SuggestionIn(BaseModel):
    """A ``suggestion`` row written on every decision (spec 02 §2.8, spec 03 §6.6)."""

    project_id: int
    item_id: int
    launch_id: int
    group_id: int | None = None
    predicted_label: str  # locator, or 'ti' when abstained
    confidence: float
    matched_mode_id: int | None = None
    matched_item_id: int | None = None  # relevantItem in RP replies
    features: dict[str, float] = {}
    model_ver: str
    llm_used: bool = False
    # Decision provenance persisted as real columns (migration 0007): how the decision
    # was reached ('hash'|'kb'|'gbm'|'rule_cold'|'coldstart') and, for an abstain, why
    # ('gbm_below_suggest'|'gbm_boilerplate_only_neighbor'|'no_confident_rule'). NULL on
    # rows written before the column existed (no backfill — NULL means "not captured").
    method: str | None = None
    abstain_reason: str | None = None
    # Deterministic template explanation written at decision time (extension
    # 2026-07-20): Stage-A inherits carry an "inherited from item N …" sentence with
    # ``llm_used=false`` — the provenance marker distinguishing a template rationale
    # from the LLM explainer (which sets ``llm_used=true``). None for every other path.
    explanation: str | None = None
    # Which route wrote the row (migration 0009, docs/EARLY-ITEM-AA.md). 'early' =
    # the per-item pre-launch-finish pass, whose launch-context features are a
    # degenerate group of one; the training frame refuses those snapshots. None =
    # a launch-scoped or suggest route (the trusted provenance).
    source: Literal["early"] | None = None


class LabelEventIn(BaseModel):
    """An append-only training-log event (spec 02 §2.7)."""

    project_id: int
    item_id: int
    old_label: str | None = None
    new_label: str
    source: Literal["rp_defect_update", "analyzer_suggestion_accepted", "human_ui"]
    suggestion_id: int | None = None
