"""Row-update applier tests (spec 04 §4 row-update clauses + invariants)."""

from __future__ import annotations

from typing import Any

from analyzer_ng.llm.apply import (
    COLDSTART_METHOD_NAME,
    apply_coldstart,
    apply_explainer,
    apply_judge,
)
from analyzer_ng.llm.roles.coldstart import CONFIDENCE_SCORE


class FakeOps:
    """Records suggestion writes. Deliberately exposes NO way to change
    predicted_label/confidence/band on an existing row — proving the judge/explainer
    appliers cannot (spec §4.3)."""

    def __init__(self) -> None:
        self.explanations: list[tuple[int, int, str]] = []
        self.judge_calls: list[dict[str, Any]] = []
        self.coldstart_inserts: list[dict[str, Any]] = []
        self._next_id = 900

    def set_explanation(self, project_id: int, suggestion_id: int, explanation: str) -> None:
        self.explanations.append((project_id, suggestion_id, explanation))

    def annotate_judge(
        self,
        project_id: int,
        item_id: int,
        *,
        chosen_item_id: int | None,
        features_patch: dict[str, Any],
    ) -> None:
        self.judge_calls.append(
            {
                "project_id": project_id,
                "item_id": item_id,
                "chosen": chosen_item_id,
                "patch": features_patch,
            }
        )

    def insert_coldstart(self, **kwargs: Any) -> int:
        self.coldstart_inserts.append(kwargs)
        self._next_id += 1
        return self._next_id


def test_explainer_apply_sets_explanation() -> None:
    ops = FakeOps()
    apply_explainer(ops, 1, 55, {"explanation": "because DNS", "quoted_lines": []})
    assert ops.explanations == [(1, 55, "because DNS")]


def _candidates() -> list[dict[str, Any]]:
    # Real candidate refs: item id (relevantItem) + label + similarity.
    return [
        {"id": 201, "label": "pb001", "similarity": 0.9},
        {"id": 202, "label": "ab001", "similarity": 0.8},
        {"id": 203, "label": "si001", "similarity": 0.7},
    ]


def test_judge_candidate_records_real_chosen_item() -> None:
    ops = FakeOps()
    apply_judge(
        ops,
        1,
        10,
        candidates=_candidates(),
        output={"choice": "candidate_2", "reason": "same"},
        model="qwen3:4b-q4_K_M",
        prompt_hash="abc",
    )
    call = ops.judge_calls[-1]
    assert call["chosen"] == 202  # candidate_2's real item id (relevantItem)
    assert call["patch"]["judge"]["choice"] == "candidate_2"
    assert call["patch"]["judge"]["chosen_item_id"] == 202
    assert call["patch"]["judge"]["prompt_hash"] == "abc"


def test_judge_none_demotes_all() -> None:
    ops = FakeOps()
    apply_judge(
        ops,
        1,
        10,
        candidates=_candidates(),
        output={"choice": "none", "reason": "nope"},
        model="m",
        prompt_hash="p",
    )
    assert ops.judge_calls[-1]["chosen"] is None


def test_judge_abstain_is_noop() -> None:
    ops = FakeOps()
    apply_judge(
        ops,
        1,
        10,
        candidates=_candidates(),
        output={"choice": "abstain", "reason": "unsure"},
        model="m",
        prompt_hash="p",
    )
    assert ops.judge_calls == []


def test_judge_never_mutates_label_or_confidence_over_all_choices() -> None:
    # The ops port has no label/confidence setter, so no choice value can reach one.
    for choice in ("candidate_1", "candidate_2", "candidate_3", "none", "abstain"):
        ops = FakeOps()
        apply_judge(
            ops,
            1,
            10,
            candidates=_candidates(),
            output={"choice": choice, "reason": "r"},
            model="m",
            prompt_hash="p",
        )
        assert not hasattr(ops, "set_confidence")
        assert not hasattr(ops, "set_predicted_label")


def test_coldstart_inserts_suggest_band_ai_suggestion() -> None:
    ops = FakeOps()
    out = {"label": "si", "confidence": "high", "rubric_rule_matched": "R6", "reason": "dns"}
    apply_coldstart(ops, 1, 10, 500, output=out, group_locator="si001", model_tag="qwen3:4b-q4_K_M")
    ins = ops.coldstart_inserts[-1]
    assert ins["confidence"] == CONFIDENCE_SCORE["high"]
    assert ins["confidence"] < 0.75  # always below τ_auto
    assert ins["predicted_label"] == "si001"
    assert ins["model_ver"] == "rubric+qwen3:4b-q4_K_M"
    assert ins["features"]["coldstart"] == {"rule": "R6", "confidence": "high"}


def test_coldstart_method_name_constant() -> None:
    assert COLDSTART_METHOD_NAME == "llm_coldstart"
