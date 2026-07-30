"""Cold-start rubric provisional surfacing on the suggest read path.

Product extension 2026-07-20: when the classical suggest reply is empty (no
evidence-backed candidate), the Make Decision modal should show the LLM cold-start
rubric hypothesis the ``coldstart`` role already persisted — read-only, on the
synchronous suggest budget (no LLM call). These tests pin the gate (only when
classical empty; only when the role is on; only when the latest suggestion row is a
rubric provisional) and the exact RP field mapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from analyzer_ng.amqp.models import AnalyzerConf, Log, SuggestAnalysisResult, TestItemInfo
from analyzer_ng.core.analysis import AnalysisEngine

# Pydantic DTO whose name starts with "Test" — opt it out of pytest collection.
TestItemInfo.__test__ = False  # type: ignore[attr-defined]

PROJECT = 77
ITEM = 2001
LAUNCH = 555


class FakeSidecar:
    def __init__(self, *, enabled: bool = True, coldstart: bool = True) -> None:
        self.enabled = enabled
        self._coldstart = coldstart

    def role_enabled(self, role: str) -> bool:
        return self.enabled and role == "coldstart" and self._coldstart


class FakeRetrieval:
    def __init__(self, row: dict | None) -> None:
        self._row = row
        self.calls: list[tuple[int, int]] = []

    def latest_rubric_provisional(self, project_id: int, item_id: int) -> dict | None:
        self.calls.append((project_id, item_id))
        return self._row


def _rubric_row(**overrides: Any) -> dict:
    row = {
        "predicted_label": "si001",
        "confidence": 0.65,
        "explanation": "Connection pool exhausted under load — a system issue.",
        "model_ver": "rubric+qwen3:4b-q4_K_M",
    }
    row.update(overrides)
    return row


def _engine(retrieval: FakeRetrieval, sidecar: FakeSidecar | None) -> AnalysisEngine:
    return AnalysisEngine(
        retrieval=retrieval,  # type: ignore[arg-type]
        kb=object(),
        stats=object(),
        pipeline=object(),  # type: ignore[arg-type]
        sidecar=sidecar,
    )


def _info() -> TestItemInfo:
    return TestItemInfo(
        testItemId=ITEM,
        launchId=LAUNCH,
        launchName="nightly",
        launchNumber=12,
        clusterId=999,
        project=PROJECT,
        analyzerConfig=AnalyzerConf(numberOfLogLines=7, minShouldMatch=42),
        logs=[Log(logId=6001, message="boom")],
    )


def _rep(*, merged: bool = False) -> Any:
    return SimpleNamespace(signature=SimpleNamespace(is_merged_small_logs=merged))


# --------------------------------------------------------------------------- #


def test_rubric_surfaces_when_classical_empty() -> None:
    """Empty classical reply + a persisted rubric provisional → one rubric row."""
    retr = FakeRetrieval(_rubric_row())
    engine = _engine(retr, FakeSidecar())

    out = engine._maybe_rubric_fallback([], _info(), _rep(), elapsed=0.01234)

    assert len(out) == 1
    assert retr.calls == [(PROJECT, ITEM)]


def _classical_row(*, score: float, src: str) -> SuggestAnalysisResult:
    return SuggestAnalysisResult(
        project=PROJECT,
        testItem=ITEM,
        testItemLogId=6001,
        launchId=LAUNCH,
        launchName="nightly",
        launchNumber=12,
        issueType="pb001",
        relevantItem=42,
        relevantLogId=7,
        matchScore=score,
        resultPosition=0,
        modelInfo=f"analyzer-ng;ng=1;band=suggest;src={src}",
        usedLogLines=7,
        minShouldMatch=42,
        processedTime=0.1,
        methodName="suggestion",
    )


def test_rubric_not_surfaced_when_classical_has_vouched_result() -> None:
    """A VOUCHED suggestion (human-confirmed neighbour) is never displaced — no read."""
    retr = FakeRetrieval(_rubric_row())
    engine = _engine(retr, FakeSidecar())
    existing = _classical_row(score=88.0, src="human-confirmed")

    out = engine._maybe_rubric_fallback([existing], _info(), _rep(), elapsed=0.1)

    assert out == [existing]
    assert retr.calls == []  # a vouched reply short-circuits before any read


def test_rubric_surfaced_over_unlabeled_lookalikes() -> None:
    """The 4998 case: strong-cosine but src=unlabeled twins do NOT suppress the
    cold-start rubric (they are look-alikes nobody labelled)."""
    retr = FakeRetrieval(_rubric_row())
    engine = _engine(retr, FakeSidecar())
    twins = [
        _classical_row(score=98.0, src="unlabeled"),
        _classical_row(score=93.0, src="unlabeled"),
    ]

    out = engine._maybe_rubric_fallback(twins, _info(), _rep(), elapsed=0.1)

    assert len(out) == 3  # the two twins + the appended rubric row
    assert out[-1].methodName == "coldstart_rubric"
    assert retr.calls == [(PROJECT, ITEM)]  # the read DID happen


def test_rubric_not_surfaced_when_seed_or_auto_vouches() -> None:
    """seed and auto-analyzed are grounded sources too — they suppress the rubric."""
    retr = FakeRetrieval(_rubric_row())
    for src in ("seed", "auto-analyzed", "kb-mode"):
        engine = _engine(retr, FakeSidecar())
        out = engine._maybe_rubric_fallback(
            [_classical_row(score=80.0, src=src)], _info(), _rep(), elapsed=0.1
        )
        assert len(out) == 1, f"{src} should suppress the rubric"


def test_disabled_master_switch_no_rubric() -> None:
    """ANALYZER_LLM_ENABLED off (no sidecar) → nothing surfaced."""
    retr = FakeRetrieval(_rubric_row())
    engine = _engine(retr, sidecar=None)

    assert engine._maybe_rubric_fallback([], _info(), _rep(), elapsed=0.0) == []
    assert retr.calls == []


def test_coldstart_role_off_no_rubric() -> None:
    """Master switch on but ANALYZER_LLM_COLDSTART off → nothing surfaced."""
    retr = FakeRetrieval(_rubric_row())
    engine = _engine(retr, FakeSidecar(enabled=True, coldstart=False))

    assert engine._maybe_rubric_fallback([], _info(), _rep(), elapsed=0.0) == []
    assert retr.calls == []


def test_no_rubric_row_no_result() -> None:
    """Role on, classical empty, but the latest row is not a rubric provisional."""
    retr = FakeRetrieval(None)
    engine = _engine(retr, FakeSidecar())

    assert engine._maybe_rubric_fallback([], _info(), _rep(), elapsed=0.0) == []
    assert retr.calls == [(PROJECT, ITEM)]


def test_rubric_field_mapping_exact() -> None:
    """Every RP field maps per the delivery contract."""
    retr = FakeRetrieval(_rubric_row())
    engine = _engine(retr, FakeSidecar())

    (r,) = engine._rubric_provisional_suggestions(_info(), _rep(merged=True), elapsed=0.01234)

    assert r.methodName == "coldstart_rubric"  # explicit, greppable; UI ignores it
    assert r.issueType == "si001"  # rubric locator = proposed defect type
    assert r.matchScore == 65.0  # confidence 0.65 × 100 (a rubric confidence)
    assert r.relevantItem == ITEM  # SELF-reference — the loadable id, never fabricated
    assert r.relevantLogId == 6001  # highlights the item's own first log
    assert r.testItemLogId == 6001
    assert r.testItem == ITEM
    assert r.project == PROJECT
    assert r.launchId == LAUNCH
    assert r.launchName == "nightly"
    assert r.launchNumber == 12
    assert r.clusterId == 999
    assert r.isMergedLog is True
    assert r.resultPosition == 0
    assert r.esScore == 0.0
    assert r.esPosition == 0
    assert r.usedLogLines == 7
    assert r.minShouldMatch == 42
    assert r.processedTime == 0.0123
    # The "why" rides in modelInfo (the UI renders no analyzer text field).
    assert r.modelInfo is not None
    assert r.modelInfo.startswith("coldstart_rubric;rubric+qwen3:4b-q4_K_M;why=")
    assert "Connection pool exhausted under load" in r.modelInfo


def test_matchscore_clamped_and_rounded() -> None:
    """Confidence is clamped to 1.0 and rounded to 2 dp before ×100."""
    retr = FakeRetrieval(_rubric_row(confidence=1.5))
    engine = _engine(retr, FakeSidecar())

    (r,) = engine._rubric_provisional_suggestions(_info(), _rep(), elapsed=0.0)

    assert r.matchScore == 100.0


def test_missing_logs_zero_log_id() -> None:
    """No logs on the request → log ids default to 0 (no crash)."""
    retr = FakeRetrieval(_rubric_row())
    engine = _engine(retr, FakeSidecar())
    info = _info()
    info.logs = []

    (r,) = engine._rubric_provisional_suggestions(info, _rep(), elapsed=0.0)

    assert r.testItemLogId == 0
    assert r.relevantLogId == 0
