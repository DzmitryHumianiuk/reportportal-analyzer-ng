"""The ``search`` route — similar 'To Investigate' retrieval (spec 03 §8.2).

RP's Make Decision "Similar 'To Investigate' in the launch" panel drives this route.
Its whole purpose is finding still-uninvestigated (TI) look-alikes, so it uses a
TI-only, launch-scoped retrieval variant (``search_ti_candidates``) — the MIRROR of
the decision path, which excludes ``issue_type_group = 'ti'``. These tests pin that
the engine delegates to the TI variant (never the decision-path ``find_candidates``),
forwards ``filteredLaunchIds`` and the self item id, applies the cosine/FTS gate, and
returns the matched item's REAL RP log id as ``logId``.
"""

from __future__ import annotations

from typing import Any

from test_analysis_identity import (
    FakeDrainStore,
    FakeKB,
    FakeStats,
    IndexRetrieval,
)

from analyzer_ng.amqp.models import SearchLogs
from analyzer_ng.core.analysis import AnalysisEngine
from analyzer_ng.core.ingest import IndexPipeline
from analyzer_ng.db.repositories.models import Candidate

PROJECT = 3
QUERY_ITEM = 2765
LAUNCH = 176

NPE_MSG = (
    "java.lang.NullPointerException: cart total was null\n"
    "\tat com.shop.CartService.total(CartService.java:88)\n"
    "\tat com.shop.CheckoutController.pay(CheckoutController.java:31)"
)


class SearchSpyRetrieval:
    """Captures the search_ti_candidates call and serves a canned candidate list."""

    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates
        self.ti_calls: list[dict[str, Any]] = []
        self.find_candidates_calls = 0

    def search_ti_candidates(
        self,
        project_id: int,
        q: Any,
        k: int,
        filtered_launch_ids: Any,
        self_item_id: int,
    ) -> list[Candidate]:
        self.ti_calls.append(
            {
                "project_id": project_id,
                "k": k,
                "filtered_launch_ids": list(filtered_launch_ids),
                "self_item_id": self_item_id,
            }
        )
        return self._candidates

    def find_candidates(self, *a: Any, **k: Any) -> list[Candidate]:
        # The decision-path retrieval (issue_type_group <> 'ti') must never be used
        # by the search route — calling it here would be the original bug.
        self.find_candidates_calls += 1
        raise AssertionError("search must not use the decision-path find_candidates")


def _engine(retr: SearchSpyRetrieval) -> AnalysisEngine:
    pipe = IndexPipeline(IndexRetrieval(), FakeStats(), FakeDrainStore())
    return AnalysisEngine(
        retrieval=retr,  # type: ignore[arg-type]
        kb=FakeKB(),
        stats=FakeStats(),
        pipeline=pipe,
    )


def _request(**overrides: Any) -> SearchLogs:
    base: dict[str, Any] = dict(
        launchId=LAUNCH,
        launchName="Nightly",
        itemId=QUERY_ITEM,
        projectId=PROJECT,
        filteredLaunchIds=[LAUNCH],
        logMessages=[NPE_MSG],
        logLines=5,
    )
    base.update(overrides)
    return SearchLogs(**base)


def _cand(item_id: int, *, cosine: float, lex: float = 0.0, log_id: int | None = None) -> Candidate:
    return Candidate(
        item_id=item_id,
        mode_id=None,
        cosine=cosine,
        lex_score=lex,
        rrf_score=cosine,
        launch_id=LAUNCH,
        relevant_log_id=log_id,
    )


def test_search_returns_ti_twins_with_real_log_ids() -> None:
    twins = [_cand(3001, cosine=0.99, log_id=99001), _cand(3002, cosine=1.0, log_id=99002)]
    retr = SearchSpyRetrieval(twins)
    engine = _engine(retr)

    hits = engine.search(_request())

    assert {h.testItemId for h in hits} == {3001, 3002}
    # logId is the matched item's real RP log id (not 0, not the testItemId).
    assert {h.logId for h in hits} == {99001, 99002}
    assert all(0.0 <= h.matchScore <= 100.0 for h in hits)
    # Delegated to the TI-only retrieval, never the decision-path one.
    assert retr.find_candidates_calls == 0
    assert len(retr.ti_calls) == 1


def test_search_forwards_launch_scope_and_excludes_self() -> None:
    retr = SearchSpyRetrieval([_cand(3001, cosine=0.9, log_id=1)])
    engine = _engine(retr)

    engine.search(_request(filteredLaunchIds=[LAUNCH, 900]))

    call = retr.ti_calls[0]
    assert call["filtered_launch_ids"] == [LAUNCH, 900]  # RP's filteredLaunchIds honored
    assert call["self_item_id"] == QUERY_ITEM  # the query item is excluded


def test_search_applies_cosine_and_fts_gate() -> None:
    cands = [
        _cand(3001, cosine=0.99, log_id=1),  # strong dense hit -> kept
        _cand(3002, cosine=0.10, lex=0.0, log_id=2),  # weak, no FTS -> dropped
        _cand(3003, cosine=0.10, lex=0.5, log_id=3),  # weak cosine but FTS hit -> kept
    ]
    retr = SearchSpyRetrieval(cands)
    engine = _engine(retr)

    hits = engine.search(_request())

    assert {h.testItemId for h in hits} == {3001, 3003}


def test_search_null_log_id_coalesces_to_zero() -> None:
    # A pre-migration row has no stored log id; the reply carries 0 (RP drops just
    # that row) rather than crashing.
    retr = SearchSpyRetrieval([_cand(3001, cosine=0.99, log_id=None)])
    engine = _engine(retr)

    hits = engine.search(_request())

    assert len(hits) == 1
    assert hits[0].logId == 0


def test_search_empty_log_messages_returns_empty() -> None:
    retr = SearchSpyRetrieval([_cand(3001, cosine=1.0, log_id=1)])
    engine = _engine(retr)

    assert engine.search(_request(logMessages=["   ", ""])) == []
    # No retrieval attempted when there is nothing to search on.
    assert retr.ti_calls == []
