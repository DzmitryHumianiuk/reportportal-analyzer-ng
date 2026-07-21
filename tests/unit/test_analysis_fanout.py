"""Fan-out identity split on the analyze path (spec 03 §5 grouping + §6.1 Stage A).

A launch group is bucketed by ``exception_fp`` (§5), so distinct failures (different
``error_hash``) of one exception class share a group. The group decision is computed on
the representative (§6). Stage A (§6.1) is an EXACT ``error_hash`` inherit, so fanning
the representative's hash-inherit — a high-confidence auto-label + its feature snapshot
— to members that never shared its ``error_hash`` lands them confidently-wrong (observed
live: a Region.rate NPE rep matched a labeled pb, auto-applying pb to grouped
Session.userId NPEs; a {201,500} assert rep auto-applied pb to grouped {201,503} asserts).

These tests drive the real IndexPipeline + AnalysisEngine.analyze and assert a member
carrying a DIFFERENT canonical ``error_hash`` than the representative is decided on ITS
OWN identity (no inherited pb; its own discriminant features), while a member sharing the
rep's hash still inherits.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from analyzer_ng.amqp.models import Launch, Log, TestItem
from analyzer_ng.core.analysis import AnalysisEngine
from analyzer_ng.core.ingest import IndexPipeline
from analyzer_ng.db.repositories.models import SignatureIn, StoredSignature, SuggestionIn

ERROR = 40000
PROJECT = 55
REP_ITEM = 100  # lowest id in the group -> representative (tie-break -item_id)
SHARE_ITEM = 101  # shares the rep's canonical error_hash
DIFF_ITEM = 102  # DIFFERENT canonical error_hash (the fan-out victim)
HIST_ITEM = 7  # labeled pb history the rep genuinely hash-matches

TestItem.__test__ = False  # type: ignore[attr-defined]

# Same exception class + frames => same exception_fp (Pass-1 bucket); different masked
# message token => different template hashes => different error_hash.
_FRAME = "\n\tat com.acme.svc.Worker.run(Worker.java:42)"
_NPE = "java.lang.NullPointerException: Cannot invoke"
_MSG_REGION = f'{_NPE} "Region.rate()" because it is null' + _FRAME
_MSG_SESSION = f'{_NPE} "Session.userId()" because it is null' + _FRAME


class FakeDrainStore:
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


class IndexRetrieval:
    """Records what the real IndexPipeline persists so we can read canonical sigs."""

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


class SpyRetrieval:
    """Serves stored signatures + a single labeled pb history for the rep's hash."""

    def __init__(
        self, stored: dict[int, StoredSignature], pb_hash: int, rep_item: int = REP_ITEM
    ) -> None:
        self._stored = stored
        self._pb_hash = pb_hash
        self._rep_item = rep_item
        self.suggestions: dict[int, SuggestionIn] = {}
        self.auto_labeled: dict[int, str] = {}

    def get_signatures(self, project: int, item_ids: Any) -> dict[int, StoredSignature]:
        return {i: self._stored[i] for i in item_ids if i in self._stored}

    def find_hash_matches(self, project: int, error_hash: int, limit: int = 10) -> list[dict]:
        if error_hash != self._pb_hash:
            return []  # a member with a different canonical hash matches nothing
        return [
            {
                "item_id": HIST_ITEM,
                "issue_type": "pb001",
                "issue_type_group": "pb",
                "is_auto_analyzed": False,
                "launch_id": 999,
                "launch_name": "hist",
                "exception_fp": self._stored[self._rep_item].exception_fp,
                "status_codes": [],
                "msg_text": self._stored[self._rep_item].msg_text,
                "label_source": "rp",
                "label_ts": datetime.now(UTC),
            }
        ]

    def error_hash_seen(self, project: int, error_hash: int, exclude_launch_id: int) -> bool:
        return True

    def find_candidates(self, project: int, q: Any, k: int = 20, filters: Any = None) -> list:
        return []

    def item_launch_names(self, project: int, item_ids: Any) -> dict[int, str]:
        return {}

    def test_case_first_seen(self, project: int, tch: int) -> None:
        return None

    def upsert_signatures(self, sigs: Any, *, conn: object | None = None) -> int:
        return len(list(sigs))

    def upsert_launch_group(self, *a: Any, **k: Any) -> int:
        return 500

    def write_suggestion(self, sug: SuggestionIn) -> int:
        self.suggestions[sug.item_id] = sug
        return 1

    def update_issue_type(self, project: int, item_id: int, issue_type: str, is_auto: bool) -> bool:
        self.auto_labeled[item_id] = issue_type
        return True


class FakeKB:
    def match_modes(self, project: int, q: Any, k: int = 10) -> list:
        return []

    def add_members(self, *a: Any, **k: Any) -> int:
        return 0


def _item(item_id: int, msg: str) -> TestItem:
    return TestItem(
        testItemId=item_id,
        isAutoAnalyzed=False,
        testItemName="t",
        testCaseHash=0,  # skip the stats path
        logs=[Log(logId=item_id * 10, logLevel=ERROR, message=msg)],
    )


def _launch(items: list[TestItem], *, launch_id: int) -> Launch:
    return Launch(
        launchId=launch_id,
        project=PROJECT,
        launchName="probe",  # analyzerConfig defaults to analyzerMode="ALL"
        testItems=items,
    )


def _stored(sig: SignatureIn) -> StoredSignature:
    return StoredSignature(
        project_id=sig.project_id,
        item_id=sig.item_id,
        exception_fp=sig.exception_fp,
        error_hash=sig.error_hash,
        top_frames=sig.top_frames,
        template_ids=sig.template_ids,
        exc_text=sig.exc_text,
        msg_text=sig.msg_text,
        status_codes=sig.status_codes,
        emb_model_ver=sig.emb_model_ver,
    )


def _index_three() -> tuple[IndexPipeline, dict[int, StoredSignature]]:
    """Index the rep (Region.rate), a hash-twin, and a Session.userId member; return
    the pipeline (read-only clone) and canonical stored signatures keyed by item id."""
    idx = IndexRetrieval()
    pipe = IndexPipeline(idx, FakeStats(), FakeDrainStore())
    pipe.index_launches(
        [
            _launch(
                [
                    _item(REP_ITEM, _MSG_REGION),
                    _item(SHARE_ITEM, _MSG_REGION),  # identical -> same error_hash as rep
                    _item(DIFF_ITEM, _MSG_SESSION),  # different -> different error_hash
                ],
                launch_id=1,
            )
        ]
    )
    stored = {iid: _stored(idx.sigs[(PROJECT, iid)]) for iid in (REP_ITEM, SHARE_ITEM, DIFF_ITEM)}
    # Preconditions that make the test meaningful.
    assert stored[REP_ITEM].exception_fp == stored[DIFF_ITEM].exception_fp != 0  # same group bucket
    assert stored[REP_ITEM].error_hash == stored[SHARE_ITEM].error_hash  # twin shares identity
    assert stored[REP_ITEM].error_hash != stored[DIFF_ITEM].error_hash  # victim differs
    return pipe, stored


def _engine(retr: SpyRetrieval, pipe: IndexPipeline) -> AnalysisEngine:
    return AnalysisEngine(retrieval=retr, kb=FakeKB(), stats=FakeStats(), pipeline=pipe)  # type: ignore[arg-type]


def test_hash_inherit_not_fanned_to_different_identity_member() -> None:
    pipe, stored = _index_three()
    retr = SpyRetrieval(stored, pb_hash=stored[REP_ITEM].error_hash)
    engine = _engine(retr, pipe)

    results = engine.analyze([_launch([_item(REP_ITEM, _MSG_REGION),
                                       _item(SHARE_ITEM, _MSG_REGION),
                                       _item(DIFF_ITEM, _MSG_SESSION)], launch_id=1)])

    # Rep + its hash-twin inherit pb (exact-identity match); the victim does NOT.
    assert retr.auto_labeled.get(REP_ITEM) == "pb001"
    assert retr.auto_labeled.get(SHARE_ITEM) == "pb001"
    assert DIFF_ITEM not in retr.auto_labeled  # never auto-labeled off the rep's hash
    labeled_items = {r.testItem for r in results}
    assert labeled_items == {REP_ITEM, SHARE_ITEM}

    # The victim's OWN suggestion reflects ITS identity, not the rep's fanned snapshot.
    victim = retr.suggestions[DIFF_ITEM]
    assert victim.predicted_label == "ti"  # abstain — its own hash matched nothing
    assert victim.matched_item_id is None
    assert victim.features["same_error_hash_top1"] == 0.0
    assert victim.features["status_codes_match_top1"] == 0.0

    # The rep's snapshot is the genuine hash match (sanity: the split kept it intact).
    rep = retr.suggestions[REP_ITEM]
    assert rep.predicted_label == "pb001"
    assert rep.matched_item_id == HIST_ITEM


def test_homogeneous_group_still_fans_out_group_decision() -> None:
    """A group whose members all share the rep's canonical hash inherits unchanged."""
    pipe, stored = _index_three()
    # Only the rep and its identical twin (same error_hash) — no differing member.
    twin_only = {REP_ITEM: stored[REP_ITEM], SHARE_ITEM: stored[SHARE_ITEM]}
    retr = SpyRetrieval(twin_only, pb_hash=stored[REP_ITEM].error_hash)
    engine = _engine(retr, pipe)

    engine.analyze([_launch([_item(REP_ITEM, _MSG_REGION),
                             _item(SHARE_ITEM, _MSG_REGION)], launch_id=1)])

    assert retr.auto_labeled.get(REP_ITEM) == "pb001"
    assert retr.auto_labeled.get(SHARE_ITEM) == "pb001"  # fanned to the same-identity twin


# --------------------------------------------------------------------------- #
# S16 / ADV-1 shape: same error_hash, divergent NEAR-ERROR CONTEXT (spec §3.3).
# The identical exception raised through the identical stack collapses to ONE
# error_hash; only the folded WARN context (SLOW QUERY→pb vs pool-exhausted→si vs
# bare→abstain) separates the members. The fan-out must split on that context.
# --------------------------------------------------------------------------- #
WARN = 30000
# Identical ERROR log (identity) across A/B/C -> one error_hash + one exception_fp. The
# multi-line stack separates cleanly, so msg_text is the description only and the folded
# WARN context is the entire discriminant (as in the real S16 corpus).
_ERR_TIMEOUT = (
    "org.openqa.selenium.TimeoutException: condition failed waiting for element\n"
    "\tat com.acme.svc.Worker.run(Worker.java:42)\n"
    "\tat com.acme.svc.Runner.exec(Runner.java:17)\n"
    "\tat java.base/java.lang.Thread.run(Thread.java:833)"
)
_CTX_SLOW = ["SLOW QUERY com.acme.dao.OrderDao.find com.acme.dao.OrderDao.scan took 900 ms"]
_CTX_POOL = ["POOL com.acme.pool.Hikari.acquire com.acme.pool.Hikari.wait exhausted 50 of 50"]

CTX_REP = 200  # representative — SLOW QUERY context (pb)
CTX_TWIN = 201  # identical SLOW QUERY context (must still inherit pb)
CTX_POOL_ITEM = 202  # pool-exhausted context (own decision -> si)
CTX_BARE = 203  # no context at all (own decision -> abstain: neighbourhood is mixed)
HIST_PB = 8  # labeled-pb history with SLOW-QUERY context
HIST_SI = 9  # labeled-si history with pool-exhausted context


def _item_ctx(item_id: int, err_msg: str, ctx_msgs: list[str]) -> TestItem:
    logs = [
        Log(logId=item_id * 10 + 1 + i, logLevel=WARN, message=c)
        for i, c in enumerate(ctx_msgs)
    ]
    logs.append(Log(logId=item_id * 10, logLevel=ERROR, message=err_msg))
    return TestItem(testItemId=item_id, isAutoAnalyzed=False, testItemName="t",
                    testCaseHash=0, logs=logs)


def _ctx_items() -> list[TestItem]:
    return [
        _item_ctx(CTX_REP, _ERR_TIMEOUT, _CTX_SLOW),
        _item_ctx(CTX_TWIN, _ERR_TIMEOUT, _CTX_SLOW),
        _item_ctx(CTX_POOL_ITEM, _ERR_TIMEOUT, _CTX_POOL),
        _item_ctx(CTX_BARE, _ERR_TIMEOUT, []),
    ]


def _index_ctx() -> tuple[IndexPipeline, dict[int, StoredSignature]]:
    idx = IndexRetrieval()
    pipe = IndexPipeline(idx, FakeStats(), FakeDrainStore())
    pipe.index_launches([_launch(_ctx_items(), launch_id=1)])
    ids = (CTX_REP, CTX_TWIN, CTX_POOL_ITEM, CTX_BARE)
    stored = {i: _stored(idx.sigs[(PROJECT, i)]) for i in ids}
    # Identity is stable across context: ALL four share one error_hash + exception_fp...
    assert len({s.error_hash for s in stored.values()}) == 1
    assert stored[CTX_REP].exception_fp != 0
    # ...but the folded context makes the message token sets diverge (the discriminant).
    assert stored[CTX_REP].msg_text == stored[CTX_TWIN].msg_text  # identical context
    assert stored[CTX_REP].msg_text != stored[CTX_POOL_ITEM].msg_text  # SLOW vs pool
    assert stored[CTX_REP].msg_text != stored[CTX_BARE].msg_text  # SLOW vs bare
    return pipe, stored


class CtxRetrieval(SpyRetrieval):
    """Serves TWO labeled histories on the shared error_hash: a pb with SLOW-QUERY
    context and an si with pool-exhausted context (the ADV-1 population). Which one an
    item inherits is decided purely by the Stage-A message gate on the folded context."""

    def find_hash_matches(self, project: int, error_hash: int, limit: int = 10) -> list[dict]:
        if error_hash != self._pb_hash:
            return []
        base = {
            "issue_type_group": "", "is_auto_analyzed": False, "launch_id": 999,
            "launch_name": "hist", "exception_fp": self._stored[CTX_REP].exception_fp,
            "status_codes": [], "label_source": "human", "label_ts": datetime.now(UTC),
        }
        return [
            {**base, "item_id": HIST_PB, "issue_type": "pb001", "issue_type_group": "pb",
             "msg_text": self._stored[CTX_REP].msg_text},  # SLOW-QUERY context
            {**base, "item_id": HIST_SI, "issue_type": "si001", "issue_type_group": "si",
             "msg_text": self._stored[CTX_POOL_ITEM].msg_text},  # pool-exhausted context
        ]


def test_same_hash_divergent_context_splits_pb_si_abstain() -> None:
    """THE ADV-1/S16 case. Four members raise the identical TimeoutException through the
    identical stack -> one error_hash, one launch group. Only the folded WARN context
    separates them. The fix must:
      * fan the pb representative's decision ONLY to its identical-context (SLOW QUERY) twin,
      * decide the pool-exhausted member on its own identity -> si (NOT the rep's pb),
      * decide the bare member on its own identity -> abstain (its gate matches neither the
        pb nor the si history unanimously).
    Getting si/ti here is only possible via an independent per-member decision, so this
    directly proves the representative's pb was NOT fanned to them."""
    pipe, stored = _index_ctx()
    retr = CtxRetrieval(stored, pb_hash=stored[CTX_REP].error_hash, rep_item=CTX_REP)
    engine = _engine(retr, pipe)

    engine.analyze([_launch(_ctx_items(), launch_id=1)])

    # Rep + identical-context twin inherit pb (truly-identical discriminant).
    assert retr.auto_labeled.get(CTX_REP) == "pb001"
    assert retr.auto_labeled.get(CTX_TWIN) == "pb001"
    # Pool-exhausted member: own decision -> si (proves it was NOT fanned the rep's pb).
    assert retr.suggestions[CTX_POOL_ITEM].predicted_label == "si001"
    assert retr.suggestions[CTX_POOL_ITEM].matched_item_id == HIST_SI
    assert CTX_POOL_ITEM not in retr.auto_labeled or retr.auto_labeled[CTX_POOL_ITEM] == "si001"
    # Bare member: own decision -> abstain (no unanimous gate match); never a confident pb.
    assert retr.suggestions[CTX_BARE].predicted_label == "ti"
    assert CTX_BARE not in retr.auto_labeled
    # Decision provenance persisted as real columns (migration 0007): the abstain row
    # carries method + abstain_reason; the pb inherit carries method='hash', no reason.
    bare = retr.suggestions[CTX_BARE]
    assert bare.method is not None and bare.abstain_reason is not None
    assert retr.suggestions[CTX_REP].method == "hash"
    assert retr.suggestions[CTX_REP].abstain_reason is None


# --------------------------------------------------------------------------- #
# Wire-order independence (RP-forwarding readiness proof).
#
# RP's service-api forwards a test item's logs as an unordered Set
# (IndexTestItem.logs), so once WARN context is forwarded the wire array order is
# NOT guaranteed chronological — the ERROR may arrive before its preceding WARN
# context. The analyzer re-establishes chronological order from logTime on ingest
# (_time_ordered_logs). This proves the near-error discriminant survives a scrambled
# wire order, i.e. the analyzer is ready for RP WARN-forwarding regardless of the
# Set's iteration order. Only the RP payload (WARN levels) was missing.
# --------------------------------------------------------------------------- #
_TS_BASE = (2026, 7, 21, 0, 0)  # Y, M, D, h, m — seconds/7th appended per log


def _item_ctx_ts(item_id: int, err_msg: str, ctx_msgs: list[str], *, scramble: bool) -> TestItem:
    """S16 item with realistic distinct logTime (context strictly BEFORE the error).
    ``scramble`` reverses the wire array (ERROR first) to mimic RP's unordered Set."""
    logs = [
        Log(logId=item_id * 10 + 1 + i, logLevel=WARN, message=c, logTime=(*_TS_BASE, i + 1, 0))
        for i, c in enumerate(ctx_msgs)
    ]
    logs.append(
        Log(logId=item_id * 10, logLevel=ERROR, message=err_msg,
            logTime=(*_TS_BASE, len(ctx_msgs) + 1, 0))
    )
    if scramble:
        logs.reverse()  # ERROR now precedes its context on the wire
    return TestItem(testItemId=item_id, isAutoAnalyzed=False, testItemName="t",
                    testCaseHash=0, logs=logs)


def test_wire_order_independence_via_logtime() -> None:
    def _index(scramble: bool) -> dict[int, StoredSignature]:
        idx = IndexRetrieval()
        pipe = IndexPipeline(idx, FakeStats(), FakeDrainStore())
        items = [
            _item_ctx_ts(CTX_REP, _ERR_TIMEOUT, _CTX_SLOW, scramble=scramble),
            _item_ctx_ts(CTX_POOL_ITEM, _ERR_TIMEOUT, _CTX_POOL, scramble=scramble),
        ]
        pipe.index_launches([_launch(items, launch_id=1)])
        return {i: _stored(idx.sigs[(PROJECT, i)]) for i in (CTX_REP, CTX_POOL_ITEM)}

    ordered = _index(scramble=False)
    scrambled = _index(scramble=True)

    # Scrambling the wire order changes NOTHING: logTime restores chronology, so the
    # folded near-error context (and thus msg_text) is byte-identical either way.
    for i in (CTX_REP, CTX_POOL_ITEM):
        assert ordered[i].msg_text == scrambled[i].msg_text

    # And the discriminant is genuinely present: shared identity, divergent context.
    assert scrambled[CTX_REP].error_hash == scrambled[CTX_POOL_ITEM].error_hash
    assert scrambled[CTX_REP].exception_fp != 0
    assert scrambled[CTX_REP].msg_text != scrambled[CTX_POOL_ITEM].msg_text
