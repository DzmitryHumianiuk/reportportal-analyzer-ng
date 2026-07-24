"""Below-suggest band emission on the suggest read path (Bench dock contract).

Follow-up to the Bench declined dock (README-bench "Follow-up", 2026-07-22):
candidates the analyzer looked at and declined — per-row score in
[SUGGEST_BELOW_FLOOR, τ_suggest) — can now ship in the suggest reply with an
explicit ``band=below_suggest`` token instead of being silently dropped, so the
Make Decision modal renders "The analyzer said no to these". Anything under the
0.30 floor stays dropped as noise. Every suggest row now carries ``ng=1`` + an
explicit ``band=`` token (the UI's parseBand never guesses below_suggest without
them); the decision's own row carries ``conf=``, and the first dock row of an
abstained reply narrates the abstain as ``ek=decline;why=…`` (why last). Dock
rows are gated behind ANALYZER_SUGGEST_BELOW_ENABLED (default OFF — a
band-unaware UI would render a declined candidate as an endorsed card) and
capped at ANALYZER_SUGGEST_BELOW_MAX (hard cap 3).
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from analyzer_ng.amqp.models import AnalyzerConf, Log, TestItemInfo
from analyzer_ng.core.analysis import (
    BAND_AUTO,
    BAND_BELOW_SUGGEST,
    BAND_SUGGEST,
    SUGGEST_BELOW_FLOOR,
    AnalysisEngine,
)
from analyzer_ng.core.decision import ACTION_AUTO, ACTION_SUGGEST, DecisionResult
from analyzer_ng.db.repositories.models import Candidate

TestItemInfo.__test__ = False  # type: ignore[attr-defined]

PROJECT = 77
ITEM = 2001
LAUNCH = 555


def _engine(**kwargs: Any) -> AnalysisEngine:
    return AnalysisEngine(
        retrieval=object(),  # type: ignore[arg-type]
        kb=object(),
        stats=object(),
        pipeline=object(),  # type: ignore[arg-type]
        sidecar=None,
        **kwargs,
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


def _rep() -> Any:
    return SimpleNamespace(signature=SimpleNamespace(is_merged_small_logs=False))


def _cand(item_id: int, cosine: float, issue_type: str = "ab001") -> Candidate:
    return Candidate(
        item_id=item_id, mode_id=None, cosine=cosine, issue_type=issue_type,
        label_source="human",
    )


def _decision(
    *,
    label: str = "ti",
    issue_type: str = "ti",
    confidence: float = 0.0,
    action: str = ACTION_SUGGEST,
    abstain_reason: str | None = None,
    relevant_item_id: int | None = None,
    stage_c: list[Candidate] | None = None,
) -> DecisionResult:
    return DecisionResult(
        label=label,
        issue_type=issue_type,
        confidence=confidence,
        method="gbm",
        action=action,
        abstain_reason=abstain_reason,
        relevant_item_id=relevant_item_id,
        matched_mode_id=None,
        features={},
        stage_c=stage_c or [],
    )


def _band_of(row: Any) -> str:
    m = re.search(r"(?:^|;)band=([a-z_]+)(?:;|$)", row.modelInfo)
    assert m, f"no band token in modelInfo: {row.modelInfo}"
    return m.group(1)


# --------------------------------------------------------------------------- #
# Flag OFF (default): wire-compatible with the pre-contract build + new tokens
# --------------------------------------------------------------------------- #


def test_flag_off_below_rows_still_dropped() -> None:
    """Default build ships no dock rows and keeps the τ_suggest reply gate."""
    decision = _decision(stage_c=[_cand(41, 0.44), _cand(42, 0.43)])
    out = _engine()._render_suggestions(_info(), _rep(), decision, elapsed=0.1)

    assert out == []


def test_flag_off_rows_carry_ng_and_band_tokens() -> None:
    """Contract v1 tokens (ng=1, band=) ride on every row even with the dock off."""
    decision = _decision(
        label="ab",
        issue_type="ab001",
        confidence=0.60,
        action=ACTION_SUGGEST,
        relevant_item_id=41,
        stage_c=[_cand(41, 0.60)],
    )
    out = _engine()._render_suggestions(_info(), _rep(), decision, elapsed=0.1)

    assert len(out) == 1
    assert ";ng=1;" in f";{out[0].modelInfo};"
    assert _band_of(out[0]) == BAND_SUGGEST
    assert ";conf=0.6000" in out[0].modelInfo


# --------------------------------------------------------------------------- #
# Flag ON: the declined dock fills
# --------------------------------------------------------------------------- #


def test_below_band_rows_ship_with_explicit_tokens() -> None:
    """[floor, τ_suggest) candidates ship as band=below_suggest with ng=1."""
    decision = _decision(stage_c=[_cand(41, 0.44), _cand(42, 0.43)])
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )

    assert [r.relevantItem for r in out] == [41, 42]
    for row in out:
        assert ";ng=1;" in f";{row.modelInfo};"
        assert _band_of(row) == BAND_BELOW_SUGGEST
    assert [r.matchScore for r in out] == [44.0, 43.0]


def test_abstained_reply_carries_no_conf_token() -> None:
    """An abstain has no calibrated answer: conf= must not leak onto a mere
    stage-C neighbour row (whatever its cosine)."""
    decision = _decision(confidence=0.32, stage_c=[_cand(41, 0.98), _cand(42, 0.44)])
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )

    assert len(out) == 2
    assert all(";conf=" not in r.modelInfo for r in out)
    assert _band_of(out[0]) == BAND_SUGGEST
    assert _band_of(out[1]) == BAND_BELOW_SUGGEST


def test_floor_cuts_rows_below_030() -> None:
    """A 0.29 candidate is noise: dropped, not docked."""
    decision = _decision(stage_c=[_cand(41, 0.44), _cand(42, 0.29)])
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )

    assert [r.relevantItem for r in out] == [41]


def test_reply_empty_when_top_is_below_floor() -> None:
    """Best candidate under the floor → the whole reply stays empty."""
    decision = _decision(stage_c=[_cand(41, SUGGEST_BELOW_FLOOR - 0.01)])
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )

    assert out == []


def test_below_rows_capped_separately_from_suggest_max() -> None:
    """Dock rows never crowd out real answers: real capped by suggest_max, dock
    by suggest_below_max (default 2, hard cap 3)."""
    decision = _decision(
        stage_c=[_cand(40 + i, 0.44 - i * 0.01) for i in range(5)]  # 0.44 … 0.40
    )
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )
    assert [r.relevantItem for r in out] == [40, 41]  # default max 2

    out = _engine(suggest_below_enabled=True, suggest_below_max=99)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )
    assert len(out) == 3  # hard cap


def test_abstain_narration_on_first_dock_row() -> None:
    """A dock-only (abstained) reply narrates why on its first row, why= last."""
    decision = _decision(
        abstain_reason="gbm_below_suggest",
        stage_c=[_cand(41, 0.44), _cand(42, 0.43)],
    )
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )

    assert out[0].modelInfo.endswith(
        ";ek=decline;why=model probability stayed below the suggest threshold"
    )
    assert ";ek=" not in out[1].modelInfo


def test_auto_row_and_secondary_bands() -> None:
    """The auto decision's own answer is band=auto (+conf=); ≥ τ_suggest
    neighbors are band=suggest; sub-τ neighbors are band=below_suggest — in one
    reply. No ek=decline anywhere (the decision did not abstain)."""
    decision = _decision(
        label="pb",
        issue_type="pb001",
        confidence=0.95,
        action=ACTION_AUTO,
        relevant_item_id=40,
        stage_c=[_cand(41, 0.46), _cand(42, 0.40)],
    )
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )

    bands = {r.relevantItem: _band_of(r) for r in out}
    assert bands == {40: BAND_AUTO, 41: BAND_SUGGEST, 42: BAND_BELOW_SUGGEST}
    assert all(r.methodName == "auto_analysis" for r in out)
    assert ";conf=0.9500" in out[0].modelInfo
    # suggest/auto rows carry NO ek= token (the deterministic match why= was rolled
    # back); ek=decline stays exclusive to the first dock row of an abstained reply.
    assert all(";ek=" not in r.modelInfo for r in out)


def test_abstain_probs_fill_dock_on_high_cosine_stand() -> None:
    """The 4998 case: neighbors are 0.9x-cosine (nothing in the cosine window),
    the GBM declined at p*=0.32 — the declined group hypothesis ships as a dock
    row anchored to that group's best NOT-yet-shown candidate, scored by the
    declined probability, with the abstain narration on it."""
    decision = _decision(
        confidence=0.3214,  # calibrated p* — the decision scale
        abstain_reason="gbm_below_suggest",
        stage_c=[
            _cand(4929, 0.98, "pb001"),
            _cand(4205, 0.98, "pb001"),
            _cand(3694, 0.97, "pb001"),
            _cand(3111, 0.96, "pb001"),
        ],
    )
    # Raw (uncalibrated) GBM distribution: argmax names the declined group, but
    # the window and the dock score use the calibrated p*, never these values.
    decision.probs.update({"pb": 0.6387, "ab": 0.19, "si": 0.03, "nd": 0.14})
    out = _engine(suggest_below_enabled=True)._render_suggestions(
        _info(), _rep(), decision, elapsed=0.1
    )

    # RP serves at most ~suggest_max rows, so the dock takes its slot from the
    # shared budget: 2 neighbours + 1 declined hypothesis = 3 rows total.
    assert [(_band_of(r), r.relevantItem) for r in out] == [
        (BAND_SUGGEST, 4929),
        (BAND_SUGGEST, 4205),
        (BAND_BELOW_SUGGEST, 3111),
    ]
    dock = out[2]
    assert dock.matchScore == 32.14
    assert dock.modelInfo.endswith(
        ";ek=decline;why=model probability stayed below the suggest threshold"
    )


def test_rubric_fallback_fires_over_below_only_reply() -> None:
    """A dock-only classical reply still gets the rubric hypothesis appended
    (the Bench shows the AI guess and the declined dock side by side)."""

    class FakeRetrieval:
        def latest_rubric_provisional(self, project_id: int, item_id: int) -> dict:
            return {
                "predicted_label": "si001",
                "confidence": 0.65,
                "explanation": "why-text",
                "model_ver": "rubric+m",
            }

    class FakeSidecar:
        enabled = True

        def role_enabled(self, role: str) -> bool:
            return role == "coldstart"

    engine = AnalysisEngine(
        retrieval=FakeRetrieval(),  # type: ignore[arg-type]
        kb=object(),
        stats=object(),
        pipeline=object(),  # type: ignore[arg-type]
        sidecar=FakeSidecar(),
        suggest_below_enabled=True,
    )
    decision = _decision(stage_c=[_cand(41, 0.44)])
    classical = engine._render_suggestions(_info(), _rep(), decision, elapsed=0.1)
    out = engine._maybe_rubric_fallback(classical, _info(), _rep(), elapsed=0.1)

    assert len(out) == 2
    assert _band_of(out[0]) == BAND_BELOW_SUGGEST
    assert out[1].methodName == "coldstart_rubric"
