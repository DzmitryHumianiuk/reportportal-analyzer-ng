"""Store layer: the six spec-02 §4 repositories over a psycopg3 pool.

One store per file / one responsibility:

- :class:`PgRetrievalStore` — ingest writes, deletes, hybrid candidate retrieval
- :class:`PgKBStore` — failure-mode knowledge base (stage-A match + lifecycle)
- :class:`PgLabelStore` — append-only label-event log + GBM training frame
- :class:`PgStatsStore` — per-test history + daily metrics
- :class:`PgDrain3StateStore` — Drain3 miner state (CAS) + template mirror
- :class:`PgLlmCacheStore` — per-project LLM output cache

The verbatim hybrid SQL lives in the versioned :mod:`.queries` module. Structural
contracts (for in-memory fakes) live in :mod:`.protocols`; DTOs in :mod:`.models`.
"""

from __future__ import annotations

from analyzer_ng.db.repositories.drain3_state import PgDrain3StateStore
from analyzer_ng.db.repositories.kb import PgKBStore
from analyzer_ng.db.repositories.labels import PgLabelStore
from analyzer_ng.db.repositories.llm_cache import PgLlmCacheStore
from analyzer_ng.db.repositories.models import (
    Candidate,
    CandidateFilters,
    LabelEventIn,
    MatchedBy,
    ModeIn,
    QuerySignature,
    SignatureIn,
    SuggestionIn,
    TestItemIn,
)
from analyzer_ng.db.repositories.protocols import (
    Drain3StateStore,
    KBStore,
    LabelStore,
    LlmCacheStore,
    RetrievalStore,
    StatsStore,
)
from analyzer_ng.db.repositories.queries import HYBRID_RETRIEVAL_VERSION
from analyzer_ng.db.repositories.retrieval import PgRetrievalStore
from analyzer_ng.db.repositories.stats import PgStatsStore

__all__ = [
    # implementations
    "PgRetrievalStore",
    "PgKBStore",
    "PgLabelStore",
    "PgStatsStore",
    "PgDrain3StateStore",
    "PgLlmCacheStore",
    # protocols
    "RetrievalStore",
    "KBStore",
    "LabelStore",
    "StatsStore",
    "Drain3StateStore",
    "LlmCacheStore",
    # models
    "TestItemIn",
    "SignatureIn",
    "SuggestionIn",
    "QuerySignature",
    "CandidateFilters",
    "Candidate",
    "ModeIn",
    "LabelEventIn",
    "MatchedBy",
    # query versioning
    "HYBRID_RETRIEVAL_VERSION",
]
