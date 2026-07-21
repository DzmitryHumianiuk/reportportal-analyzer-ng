"""Role row-update application (spec 04 §4 "Row update" clauses).

Pure functions that translate a validated role output into ``suggestion`` writes,
expressed against a narrow :class:`SuggestionOps` port so they unit-test against a
fake. The invariants the spec makes load-bearing live here:

- Explainer only fills ``explanation`` + ``llm_used`` (§4.1).
- Judge only reorders/annotates suggest-band suggestions and **never** changes
  ``predicted_label``/``confidence`` or touches an auto-band item (§4.3).
- Cold-start inserts a suggest-band suggestion (``confidence < τ_auto``,
  ``llm_used=true``, surfaced as ``methodName='llm_coldstart'``) (§4.4).
"""

from __future__ import annotations

from typing import Any, Protocol

from analyzer_ng.llm.roles.coldstart import CONFIDENCE_SCORE

# Cold-start suggestions are surfaced to RP flagged as AI-suggested (§4.4).
COLDSTART_METHOD_NAME = "llm_coldstart"


class SuggestionOps(Protocol):
    def set_explanation(self, project_id: int, suggestion_id: int, explanation: str) -> None: ...
    def annotate_judge(
        self,
        project_id: int,
        item_id: int,
        *,
        chosen_item_id: int | None,
        features_patch: dict[str, Any],
    ) -> None: ...
    def insert_coldstart(
        self,
        *,
        project_id: int,
        item_id: int,
        launch_id: int,
        predicted_label: str,
        confidence: float,
        model_ver: str,
        features: dict[str, Any],
    ) -> int: ...


def apply_explainer(
    ops: SuggestionOps, project_id: int, suggestion_id: int, output: dict[str, Any]
) -> None:
    ops.set_explanation(project_id, suggestion_id, output["explanation"])


def apply_judge(
    ops: SuggestionOps,
    project_id: int,
    item_id: int,
    *,
    candidates: list[dict[str, Any]],
    output: dict[str, Any],
    model: str,
    prompt_hash: str,
) -> None:
    """Annotate the item's suggestion with the judge verdict (§4.3).

    ``abstain`` is a no-op. ``candidate_k`` records the chosen candidate's item id
    (its RP ``relevantItem``) so the suggest read path can promote it to
    ``resultPosition`` 0; ``none`` demotes all (``chosen_item_id=None``). Only
    ``features['judge']`` + ``llm_used`` change — never ``predicted_label``/
    ``confidence``/band membership (an auto-band decision is untouchable).
    """
    choice = output["choice"]
    if choice == "abstain":
        return
    chosen_item_id: int | None = None
    if choice != "none":
        idx = int(choice.split("_", 1)[1]) - 1
        chosen_item_id = candidates[idx]["id"]
    patch = {
        "judge": {
            "choice": choice,
            "chosen_item_id": chosen_item_id,
            "model": model,
            "prompt_hash": prompt_hash,
        }
    }
    ops.annotate_judge(
        project_id, item_id, chosen_item_id=chosen_item_id, features_patch=patch
    )


def apply_coldstart(
    ops: SuggestionOps,
    project_id: int,
    item_id: int,
    launch_id: int,
    *,
    output: dict[str, Any],
    group_locator: str,
    model_tag: str,
) -> int:
    """Insert a cold-start AI suggestion, always inside the suggest band (§4.4).

    Extension 2026-07-20: the rubric ``reason`` sentence the model already produced
    (and which is audited verbatim in ``llm_event.output``) is copied straight onto
    the inserted suggestion's ``explanation`` — no second LLM call, provenance stays
    ``rubric+<model>``. Cheap and deterministic: the sentence already exists.
    """
    confidence = CONFIDENCE_SCORE[output["confidence"]]
    features = {
        "coldstart": {
            "rule": output["rubric_rule_matched"],
            "confidence": output["confidence"],
        }
    }
    suggestion_id = ops.insert_coldstart(
        project_id=project_id,
        item_id=item_id,
        launch_id=launch_id,
        predicted_label=group_locator,
        confidence=confidence,
        model_ver=f"rubric+{model_tag}",
        features=features,
    )
    reason = output.get("reason")
    if reason:
        ops.set_explanation(project_id, suggestion_id, reason)
    return suggestion_id
