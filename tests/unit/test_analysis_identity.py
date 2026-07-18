"""Signature-identity consistency on the analyze/suggest read path (spec 03 §6.1).

The invariant under test: hash-identity comparisons only ever compare values
computed the same way. ``error_hash`` folds the ordered Drain3 template ids, which
drift as new logs re-cluster the miner; the read path mines against a *read-only
Drain clone* whose templates have moved on from the state each history row was
indexed under. So a read-time recompute of the query item's ``error_hash`` can
diverge from the persisted value — causing false Stage-A matches (a drifted hash
colliding with an unrelated history row → wrong inherited label) and false
non-matches. These tests reproduce the drift with the *real* IndexPipeline and
assert the decision path resolves the item's CANONICAL (persisted) identity.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from analyzer_ng.amqp.models import Launch, Log, TestItem
from analyzer_ng.core import scope
from analyzer_ng.core.analysis import AnalysisEngine
from analyzer_ng.core.ingest import IndexPipeline, ItemAnalysis
from analyzer_ng.db.repositories.models import SignatureIn, StoredSignature, TestItemIn

ERROR = 40000
PROJECT = 77
ITEM = 2001

# Pydantic DTOs whose names start with "Test" — opt them out of pytest collection.
TestItemIn.__test__ = False  # type: ignore[attr-defined]
TestItem.__test__ = False  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Index-side fakes (drive the real IndexPipeline to persist + then drift)
# --------------------------------------------------------------------------- #
class IndexRetrieval:
    """Records what the index path persists (test_item + failure_signature)."""

    def __init__(self) -> None:
        self.sigs: dict[tuple[int, int], SignatureIn] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def upsert_items(self, items: Any, *, conn: object | None = None) -> int:
        return len(list(items))

    def upsert_signatures(self, sigs: Any, *, conn: object | None = None) -> int:
        for s in sigs:
            self.sigs[(s.project_id, s.item_id)] = s
        return len(list(sigs))


class FakeStats:
    def bump_test_history(self, *a: Any, **k: Any) -> None:
        return None

    def get_test_history(self, project_id: int, hashes: Any) -> dict:
        return {}


class FakeDrainStore:
    """Minimal CAS-correct Drain3 state store (mirrors tests/unit/test_ingest.py)."""

    def __init__(self) -> None:
        self.states: dict[int, tuple[bytes, int]] = {}
        self.templates: dict[int, list[dict]] = {}

    def load(self, project_id: int) -> tuple[bytes, int] | None:
        return self.states.get(project_id)

    def save(self, project_id: int, state: bytes, expected_version: int, config: dict) -> bool:
        current = self.states.get(project_id)
        current_version = current[1] if current else 0
        if current_version != expected_version:
            return False
        self.states[project_id] = (state, current_version + 1)
        return True

    def load_template_texts(self, project_id: int) -> list[str]:
        return [t["pattern"] for t in self.templates.get(project_id, [])]

    def upsert_templates(self, project_id: int, templates: Any) -> int:
        self.templates[project_id] = list(templates)
        return len(self.templates[project_id])


def _launch(item_id: int, msg: str, *, launch_id: int = 1, tch: int = 4242) -> Launch:
    return Launch(
        launchId=launch_id,
        project=PROJECT,
        testItems=[
            TestItem(
                testItemId=item_id,
                isAutoAnalyzed=False,
                testItemName="t",
                testCaseHash=tch,
                issueType="pb001",
                logs=[Log(logId=item_id * 10, logLevel=ERROR, message=msg)],
            )
        ],
    )


_BASE_MSG = (
    "java.lang.IllegalStateException: widget alpha failed at node 5\n"
    "\tat com.acme.svc.Worker.run(Worker.java:42)\n"
    "\tat com.acme.svc.Engine.tick(Engine.java:11)"
)


def _drifted_rep() -> tuple[ItemAnalysis, StoredSignature]:
    """Index ITEM under Drain state S1, then mutate the miner so its template
    re-clusters, and return (drifted read-time analysis, canonical stored sig).

    Asserts drift actually happened so the identity assertions below are meaningful.
    """
    index_retr = IndexRetrieval()
    pipe = IndexPipeline(index_retr, FakeStats(), FakeDrainStore())
    # Index the item -> persists failure_signature with the S1 error_hash.
    pipe.index_launches([_launch(ITEM, _BASE_MSG)])
    persisted = index_retr.sigs[(PROJECT, ITEM)]

    # Mutate the miner: index sibling logs that fall in the SAME Drain cluster but
    # with different variable tokens, so the template pattern generalizes (S1 -> S2).
    for i, tok in enumerate(["node 6", "node 77", "node 888", "node 1234", "banana 9"], start=1):
        sib = _BASE_MSG.replace("node 5", tok)
        pipe.index_launches([_launch(3000 + i, sib, launch_id=2)])

    # Read-time recompute against the drifted read-only clone (never save_manager).
    ana = _launch(ITEM, _BASE_MSG)
    rep = pipe.build_item_analyses(PROJECT, [(ana, ana.testItems[0])])[0]

    # Precondition: the recompute genuinely drifted away from the persisted value.
    assert rep.signature.error_hash != persisted.error_hash

    stored = StoredSignature(
        project_id=PROJECT,
        item_id=ITEM,
        exception_fp=persisted.exception_fp,
        error_hash=persisted.error_hash,
        top_frames=persisted.top_frames,
        template_ids=persisted.template_ids,
        exc_text=persisted.exc_text,
        msg_text=persisted.msg_text,
        status_codes=persisted.status_codes,
        emb_model_ver=persisted.emb_model_ver,
    )
    return rep, stored


# --------------------------------------------------------------------------- #
# Read-side fakes (drive AnalysisEngine identity resolution + Stage A)
# --------------------------------------------------------------------------- #
class SpyRetrieval:
    """Serves stored signatures and records hash-identity comparison arguments."""

    def __init__(self, stored: dict[int, StoredSignature]) -> None:
        self._stored = stored
        self.hash_match_calls: list[int] = []
        self.error_hash_seen_calls: list[int] = []
        self.upserted: list[SignatureIn] = []

    def get_signatures(self, project: int, item_ids: Any) -> dict[int, StoredSignature]:
        return {i: self._stored[i] for i in item_ids if i in self._stored}

    def find_hash_matches(self, project: int, error_hash: int, limit: int = 10) -> list[dict]:
        self.hash_match_calls.append(error_hash)
        return []

    def error_hash_seen(self, project: int, error_hash: int, exclude_launch_id: int) -> bool:
        self.error_hash_seen_calls.append(error_hash)
        return True

    def find_candidates(self, project: int, q: Any, k: int = 20, filters: Any = None) -> list:
        return []

    def item_launch_names(self, project: int, item_ids: Any) -> dict[int, str]:
        return {}

    def test_case_first_seen(self, project: int, tch: int) -> None:
        return None

    def upsert_signatures(self, sigs: Any, *, conn: object | None = None) -> int:
        self.upserted.extend(sigs)
        return len(list(sigs))


class FakeKB:
    def match_modes(self, project: int, q: Any, k: int = 10) -> list:
        return []

    def add_members(self, *a: Any, **k: Any) -> int:
        return 0


def _engine(retr: SpyRetrieval, pipe: IndexPipeline) -> AnalysisEngine:
    return AnalysisEngine(
        retrieval=retr,  # type: ignore[arg-type]
        kb=FakeKB(),
        stats=FakeStats(),
        pipeline=pipe,
    )


def _pipe() -> IndexPipeline:
    return IndexPipeline(IndexRetrieval(), FakeStats(), FakeDrainStore())


def _scope() -> scope.ScopeQuery:
    return scope.ScopeQuery(
        project_id=PROJECT, launch_id=1, launch_name="", previous_launch_id=None
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_resolve_identity_uses_stored_not_drifted_recompute() -> None:
    """The query identity equals the STORED signature, never the drifted recompute."""
    rep, stored = _drifted_rep()
    retr = SpyRetrieval({ITEM: stored})
    engine = _engine(retr, _pipe())

    ident = engine._resolve_identity(PROJECT, rep)

    # Canonical (index-time) identity — not the read-time recompute in rep.signature.
    assert ident.error_hash == stored.error_hash
    assert ident.error_hash != rep.signature.error_hash
    assert list(ident.template_ids) == list(stored.template_ids)
    assert list(ident.top_frames) == list(stored.top_frames)

    # The QuerySignature built for retrieval carries the stored identity too.
    q = engine._query_from_identity(ident, rep, launch_id=1, launch_number=0)
    assert q.error_hash == stored.error_hash
    assert q.template_ids == list(stored.template_ids)
    # emb stays the live read-time vector (a separate, non-identity signal).
    assert q.emb == rep.emb

    # Nothing persisted — the item was already indexed (no fallback upsert).
    assert retr.upserted == []


def test_stage_a_compares_stored_vs_stored() -> None:
    """Stage A queries history with the STORED error_hash (stored-vs-stored)."""
    rep, stored = _drifted_rep()
    rep = rep.__class__(**{**rep.__dict__, "item": _no_tch(rep.item)})  # skip stats path
    retr = SpyRetrieval({ITEM: stored})
    engine = _engine(retr, _pipe())

    group = engine._singleton_group(rep)
    decision, _mode = engine._decide(
        PROJECT, _scope(), "ALL", rep, group, total_failures=1, route="analyze"
    )

    # find_hash_matches was asked with the persisted hash, never the drifted one.
    assert retr.hash_match_calls == [stored.error_hash]
    assert rep.signature.error_hash not in retr.hash_match_calls
    assert decision is not None


def test_group_novelty_check_uses_stored_error_hash() -> None:
    """The burst-novelty ``error_hash_seen`` probe uses the canonical error_hash."""
    rep, stored = _drifted_rep()
    retr = SpyRetrieval({ITEM: stored})
    engine = _engine(retr, _pipe())

    engine._group(PROJECT, launch_id=1, analyses=[rep])

    assert retr.error_hash_seen_calls == [stored.error_hash]
    assert rep.signature.error_hash not in retr.error_hash_seen_calls


def test_never_indexed_item_falls_back_and_persists() -> None:
    """A never-indexed item analyzes on the recompute AND persists it (canonical next time)."""
    ana = _launch(ITEM, _BASE_MSG, tch=0)
    pipe = _pipe()
    rep = pipe.build_item_analyses(PROJECT, [(ana, ana.testItems[0])])[0]
    retr = SpyRetrieval({})  # no stored signature — genuinely never indexed
    engine = _engine(retr, pipe)

    ident = engine._resolve_identity(PROJECT, rep)

    # Fallback: identity is the recompute (no stored value to prefer)...
    assert ident.error_hash == rep.signature.error_hash
    # ...and it was persisted (upserted) so it becomes canonical for later compares.
    assert len(retr.upserted) == 1
    up = retr.upserted[0]
    assert up.project_id == PROJECT and up.item_id == ITEM
    assert up.error_hash == rep.signature.error_hash
    assert up.exception_fp == rep.signature.exception_fp


def test_never_indexed_item_still_decides() -> None:
    """The full decision path runs for a never-indexed item (fallback, no stored row)."""
    ana = _launch(ITEM, _BASE_MSG, tch=0)
    pipe = _pipe()
    rep = pipe.build_item_analyses(PROJECT, [(ana, ana.testItems[0])])[0]
    retr = SpyRetrieval({})
    engine = _engine(retr, pipe)

    group = engine._singleton_group(rep)
    decision, _mode = engine._decide(
        PROJECT, _scope(), "ALL", rep, group, total_failures=1, route="suggest"
    )
    assert decision is not None
    # Stage A still used the (now-canonical) recomputed hash, and it was persisted.
    assert retr.hash_match_calls == [rep.signature.error_hash]
    assert len(retr.upserted) == 1


def _no_tch(item: TestItem) -> TestItem:
    return item.model_copy(update={"testCaseHash": 0})
