"""Early per-item auto-analysis route (docs/EARLY-ITEM-AA.md).

A failed item is analyzed seconds after it finishes, while its launch is still
running. The route reuses the suggest route's singleton pipeline, then applies
the policy gate the consilium ruled: only deterministic decisions (Stage-A
exact-hash inherit, KB short-circuit) may auto-label early; every GBM decision
is demoted to a stored suggestion regardless of confidence, because the
singleton feature corner (group_dominance=1.0, si_prior=0.0) was never
validated against the auto threshold. Every row is tagged ``source='early'`` so
the training frame can refuse degenerate snapshots and the early-vs-final flip
rate is measurable.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from analyzer_ng.amqp.models import AnalyzerConf, Launch, Log, TestItem
from analyzer_ng.core.analysis import AnalysisEngine
from analyzer_ng.core.decision import (
    ACTION_ABSTAIN,
    ACTION_AUTO,
    ACTION_SUGGEST,
    METHOD_GBM,
    METHOD_HASH,
    METHOD_KB,
    DecisionResult,
)

TestItem.__test__ = False  # type: ignore[attr-defined]  # pydantic DTO, not a test

PROJECT = 7
LAUNCH_ID = 313
ITEM = 5917


class FakeRetrieval:
    def __init__(self) -> None:
        self.suggestions: list[Any] = []
        self.issue_updates: list[tuple[int, int, str, bool]] = []

    def write_suggestion(self, row: Any) -> int:
        self.suggestions.append(row)
        return 9000 + len(self.suggestions)

    def update_issue_type(
        self, project: int, item_id: int, issue_type: str, *, is_auto: bool
    ) -> None:
        self.issue_updates.append((project, item_id, issue_type, is_auto))


class FakeSidecar:
    enabled = True

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, int, int]] = []

    def enqueue(self, role: str, project: int, item_id: int, payload: dict) -> None:
        self.enqueued.append((role, project, item_id))

    def role_enabled(self, role: str) -> bool:
        return True


class FakePipeline:
    """Returns one canned ItemAnalysis-shaped rep per test item."""

    def __init__(self, signature_text: str = "TEST: boom") -> None:
        self._signature_text = signature_text

    def build_item_analyses(self, project: int, entries: list[Any]) -> list[Any]:
        return [
            SimpleNamespace(
                item=item,
                signature=SimpleNamespace(
                    signature_text=self._signature_text,
                    exception_fp=555,
                    error_hash=777,
                    has_stacktrace=True,
                ),
                emb=None,
                log_count=1,
            )
            for _launch, item in entries
        ]


def _decision(
    *,
    action: str = ACTION_AUTO,
    method: str = METHOD_HASH,
    issue_type: str = "pb001",
    label: str = "pb",
    confidence: float = 0.9,
    relevant_item_id: int | None = 42,
) -> DecisionResult:
    return DecisionResult(
        label=label,
        issue_type=issue_type,
        confidence=confidence,
        method=method,
        action=action,
        abstain_reason=None if label != "ti" else "gbm_below_suggest",
        relevant_item_id=relevant_item_id,
        matched_mode_id=None,
        features={"top1_cosine": 0.9},
    )


def _launch(n_items: int = 1) -> Launch:
    return Launch(
        launchId=LAUNCH_ID,
        project=PROJECT,
        launchName="nightly",
        launchNumber=4,
        analyzerConfig=AnalyzerConf(),
        testItems=[
            TestItem(
                testItemId=ITEM + i,
                isAutoAnalyzed=False,
                uniqueId=f"u{i}",
                logs=[Log(logId=100 + i, message="boom", logLevel=40000)],
            )
            for i in range(n_items)
        ],
    )


def _engine(
    retrieval: FakeRetrieval,
    sidecar: FakeSidecar | None,
    decisions: list[DecisionResult],
    *,
    enabled: bool = True,
    policy: str = "kb_inherit_only",
    signature_text: str = "TEST: boom",
) -> AnalysisEngine:
    engine = AnalysisEngine(
        retrieval=retrieval,  # type: ignore[arg-type]
        kb=object(),
        stats=object(),
        pipeline=FakePipeline(signature_text),  # type: ignore[arg-type]
        sidecar=sidecar,
        early_item_analysis=enabled,
        early_label_policy=policy,
    )
    queue = list(decisions)
    engine.decide_routes: list[str] = []  # type: ignore[attr-defined]

    def _fake_decide(*a: Any, **k: Any) -> tuple[DecisionResult, None]:
        engine.decide_routes.append(k.get("route", ""))  # type: ignore[attr-defined]
        return (queue.pop(0), None)

    engine._decide = _fake_decide  # type: ignore[method-assign]
    engine._record_membership = lambda *a, **k: None  # type: ignore[method-assign]
    return engine


# ---- flag off ------------------------------------------------------------- #


def test_flag_off_is_inert() -> None:
    retr = FakeRetrieval()
    sidecar = FakeSidecar()
    engine = _engine(retr, sidecar, [], enabled=False)

    assert engine.analyze_item_early([_launch()]) == []
    assert retr.suggestions == []
    assert retr.issue_updates == []
    assert sidecar.enqueued == []


# ---- deterministic decisions auto-label ----------------------------------- #


def test_stage_a_auto_is_applied_and_tagged_early() -> None:
    retr = FakeRetrieval()
    engine = _engine(retr, FakeSidecar(), [_decision(method=METHOD_HASH)])

    out = engine.analyze_item_early([_launch()])

    assert len(out) == 1
    assert (out[0].testItem, out[0].issueType, out[0].relevantItem) == (ITEM, "pb001", 42)
    assert retr.issue_updates == [(PROJECT, ITEM, "pb001", True)]
    assert len(retr.suggestions) == 1
    assert retr.suggestions[0].source == "early"


def test_kb_short_circuit_auto_is_applied() -> None:
    retr = FakeRetrieval()
    engine = _engine(
        retr, FakeSidecar(), [_decision(method=METHOD_KB, issue_type="si001", label="si")]
    )

    out = engine.analyze_item_early([_launch()])

    assert [(r.testItem, r.issueType) for r in out] == [(ITEM, "si001")]
    assert retr.issue_updates == [(PROJECT, ITEM, "si001", True)]


# ---- the demotion: GBM never auto-labels early ---------------------------- #


def test_gbm_auto_band_is_demoted_to_stored_suggestion() -> None:
    retr = FakeRetrieval()
    engine = _engine(retr, FakeSidecar(), [_decision(method=METHOD_GBM, confidence=0.97)])

    out = engine.analyze_item_early([_launch()])

    assert out == []  # nothing applied, however confident the GBM was
    assert retr.issue_updates == []
    assert len(retr.suggestions) == 1  # the snapshot is still stored...
    assert retr.suggestions[0].source == "early"  # ...and tagged


def test_abstain_writes_row_and_enqueues_llm() -> None:
    retr = FakeRetrieval()
    sidecar = FakeSidecar()
    engine = _engine(
        retr,
        sidecar,
        [
            _decision(
                action=ACTION_ABSTAIN,
                method=METHOD_GBM,
                issue_type="ti",
                label="ti",
                confidence=0.3,
                relevant_item_id=None,
            )
        ],
    )

    out = engine.analyze_item_early([_launch()])

    assert out == []
    assert len(retr.suggestions) == 1
    assert retr.suggestions[0].source == "early"
    assert ("extractor", PROJECT, ITEM) in sidecar.enqueued  # cache warms early


def test_suggest_band_is_stored_not_applied() -> None:
    retr = FakeRetrieval()
    engine = _engine(
        retr, FakeSidecar(), [_decision(action=ACTION_SUGGEST, method=METHOD_GBM, confidence=0.6)]
    )

    assert engine.analyze_item_early([_launch()]) == []
    assert retr.issue_updates == []
    assert len(retr.suggestions) == 1


# ---- suggest_only policy -------------------------------------------------- #


def test_suggest_only_policy_demotes_even_stage_a() -> None:
    retr = FakeRetrieval()
    engine = _engine(retr, FakeSidecar(), [_decision(method=METHOD_HASH)], policy="suggest_only")

    assert engine.analyze_item_early([_launch()]) == []
    assert retr.issue_updates == []
    assert len(retr.suggestions) == 1  # stored and tagged, never applied
    assert retr.suggestions[0].source == "early"


# ---- edge cases ----------------------------------------------------------- #


def test_empty_signature_is_skipped_entirely() -> None:
    retr = FakeRetrieval()
    sidecar = FakeSidecar()
    engine = _engine(retr, sidecar, [], signature_text="")

    assert engine.analyze_item_early([_launch()]) == []
    assert retr.suggestions == []
    assert sidecar.enqueued == []


def test_mixed_batch_applies_only_the_deterministic_item() -> None:
    retr = FakeRetrieval()
    engine = _engine(
        retr,
        FakeSidecar(),
        [_decision(method=METHOD_HASH), _decision(method=METHOD_GBM, issue_type="ab001")],
    )

    out = engine.analyze_item_early([_launch(n_items=2)])

    assert [(r.testItem, r.issueType) for r in out] == [(ITEM, "pb001")]
    assert len(retr.suggestions) == 2
    assert {s.source for s in retr.suggestions} == {"early"}


def test_route_is_analyze_scoped() -> None:
    # Review finding (2026-07-26): the early route can APPLY labels, so its
    # Stage-A/Stage-C candidates must pass the hard in_analyze_scope filter that
    # honors analyzerMode — without this, a LAUNCH_NAME-scoped project could
    # early-inherit (and auto-apply) a label from a launch the finish pass would
    # refuse to look at. Two pins: the route string is threaded into _decide,
    # and that string is a member of the hard-scoped route set.
    from analyzer_ng.core.analysis import ANALYZE_SCOPED_ROUTES

    retr = FakeRetrieval()
    engine = _engine(retr, FakeSidecar(), [_decision(method=METHOD_HASH)])
    engine.analyze_item_early([_launch()])
    assert engine.decide_routes == ["analyze_item_early"]
    assert "analyze_item_early" in ANALYZE_SCOPED_ROUTES
    assert "analyze" in ANALYZE_SCOPED_ROUTES
    assert "suggest" not in ANALYZE_SCOPED_ROUTES  # suggest only displays


def test_unknown_policy_fails_closed() -> None:
    # A typo'd or future policy value must demote everything, never label.
    retr = FakeRetrieval()
    engine = _engine(retr, FakeSidecar(), [_decision(method=METHOD_HASH)], policy="yolo")

    assert engine.analyze_item_early([_launch()]) == []
    assert retr.issue_updates == []
    assert len(retr.suggestions) == 1  # still stored + tagged


def test_other_routes_leave_source_untagged() -> None:
    # The suggest route must keep writing rows with source=None: NULL is the
    # launch-scoped/suggest provenance the training frame trusts.
    retr = FakeRetrieval()
    engine = _engine(retr, FakeSidecar(), [])
    engine._write_suggestion(PROJECT, ITEM, LAUNCH_ID, None, _decision())
    assert retr.suggestions[0].source is None


# ---- v2: pb-only GBM early labeling (kb_inherit_and_pb) ------------------- #
# Replay evidence (2026-07-25, project 7, 410 rows): every label flip at the
# singleton corner was si->pb; pb decisions held. So pb may auto-label early at
# a stricter-than-auto threshold, si/ab/nd never.


def test_pb_gbm_above_strict_threshold_is_applied() -> None:
    retr = FakeRetrieval()
    engine = _engine(
        retr,
        FakeSidecar(),
        [_decision(method=METHOD_GBM, issue_type="pb001", label="pb", confidence=0.9)],
        policy="kb_inherit_and_pb",
    )
    out = engine.analyze_item_early([_launch()])
    assert [(r.testItem, r.issueType) for r in out] == [(ITEM, "pb001")]
    assert retr.issue_updates == [(PROJECT, ITEM, "pb001", True)]


def test_pb_gbm_below_strict_threshold_is_demoted() -> None:
    retr = FakeRetrieval()
    engine = _engine(
        retr,
        FakeSidecar(),
        [_decision(method=METHOD_GBM, issue_type="pb001", label="pb", confidence=0.80)],
        policy="kb_inherit_and_pb",
    )
    # 0.80 clears the normal auto band but not the stricter early bar (0.85).
    assert engine.analyze_item_early([_launch()]) == []
    assert retr.issue_updates == []
    assert len(retr.suggestions) == 1


def test_si_gbm_never_applies_early_even_at_max_confidence() -> None:
    retr = FakeRetrieval()
    engine = _engine(
        retr,
        FakeSidecar(),
        [_decision(method=METHOD_GBM, issue_type="si001", label="si", confidence=0.99)],
        policy="kb_inherit_and_pb",
    )
    assert engine.analyze_item_early([_launch()]) == []
    assert retr.issue_updates == []


def test_kb_inherit_and_pb_still_applies_deterministic() -> None:
    retr = FakeRetrieval()
    engine = _engine(
        retr, FakeSidecar(), [_decision(method=METHOD_HASH)], policy="kb_inherit_and_pb"
    )
    assert len(engine.analyze_item_early([_launch()])) == 1


def test_default_policy_still_demotes_pb_gbm() -> None:
    retr = FakeRetrieval()
    engine = _engine(
        retr,
        FakeSidecar(),
        [_decision(method=METHOD_GBM, issue_type="pb001", label="pb", confidence=0.99)],
    )
    assert engine.analyze_item_early([_launch()]) == []


# ---- launch_fail_fraction wire (spec 6.4 #31 gap) ------------------------- #


def test_feature_ctx_reads_launch_items_count() -> None:
    from analyzer_ng.amqp.models import Launch as WireLaunch

    engine = AnalysisEngine(
        retrieval=SimpleNamespace(test_case_first_seen=lambda *a: None),  # type: ignore[arg-type]
        kb=object(),
        stats=SimpleNamespace(get_test_history=lambda *a: {}),
        pipeline=SimpleNamespace(template_id=lambda h: 0),  # type: ignore[arg-type]
    )
    launch = WireLaunch(launchId=LAUNCH_ID, project=PROJECT, launchItemsCount=50)
    rep = SimpleNamespace(
        item=SimpleNamespace(testItemId=ITEM, testCaseHash=0),
        launch=launch,
        signature=SimpleNamespace(
            signature_text="TEST: boom",
            exception_fp=1,
            error_hash=2,
            has_stacktrace=True,
            is_assertion=False,
            is_merged_small_logs=False,
            exc_classes=[],
            template_hashes=[],
            status_codes=[],
            msg_text="boom",
        ),
        emb=None,
        log_count=1,
    )
    group = SimpleNamespace(members=[1], si_prior=0.0)
    ctx = engine._feature_ctx(rep, group, total_failures=20)  # type: ignore[arg-type]
    assert ctx.launch_items == 50
    assert ctx.launch_failures == 20


def test_launch_model_carries_items_count_default_zero() -> None:
    from analyzer_ng.amqp.models import Launch as WireLaunch

    assert WireLaunch(launchId=1, project=2).launchItemsCount == 0
    assert WireLaunch(launchId=1, project=2, launchItemsCount=7).launchItemsCount == 7
