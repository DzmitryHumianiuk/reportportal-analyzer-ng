"""Unit tests for pure payload-shaping logic (no database required)."""

from __future__ import annotations

from typing import Any

from backend import features_meta, payloads
from backend.payloads import _grp, _matching_decision


class _FakeDB:
    """Returns canned rows; only used for the matched-mode lookup path here."""

    def __init__(self, one_result: dict[str, Any] | None = None) -> None:
        self._one = one_result

    def one(self, *_a: Any, **_k: Any) -> dict[str, Any] | None:
        return self._one

    def rows(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return []


def _sug(**over: Any) -> dict[str, Any]:
    base = {
        "suggestion_id": 1,
        "predicted_label": "si001",
        "confidence": 0.9,
        "matched_mode_id": None,
        "matched_item_id": None,
        "features": {},
        "model_ver": "gbm-x",
        "llm_used": False,
        "explanation": None,
        "outcome": "pending",
        "outcome_ts": None,
        "created_at": None,
    }
    base.update(over)
    return base


def test_label_grouping():
    assert _grp("pb001") == "pb"
    assert _grp("ab") == "ab"
    assert _grp("si999") == "si"
    assert _grp("ti001") == "ti"
    assert _grp(None) == "none"
    assert _grp("zz") == "other"


def test_stage_a_hash_inherit():
    m, _ = _matching_decision(_FakeDB(), 1, _sug(matched_item_id=42))
    assert m["stage"] == "A"
    assert "hash" in m["stage_label"].lower()
    assert m["matched_item_id"] == "42"


def test_stage_ab_mode_match():
    mode = {
        "mode_id": 7,
        "status": "confirmed",
        "label": "si",
        "title": "t",
        "purity": 0.8,
        "support": 5,
        "seed_key": "conn_timeout",
    }
    m, _ = _matching_decision(_FakeDB(mode), 1, _sug(matched_mode_id=7))
    assert m["stage"] == "AB"
    assert m["matched_mode"]["mode_id"] == 7
    assert m["matched_mode"]["label_group"] == "si"


def test_abstain():
    m, dec = _matching_decision(_FakeDB(), 1, _sug(predicted_label="ti", confidence=0.1))
    assert m["stage"] == "abstain"
    assert dec["band"] == "abstain"


def test_stage_c_and_bands():
    _, dec = _matching_decision(_FakeDB(), 1, _sug(confidence=0.80))
    assert dec["band"] == "auto"
    _, dec2 = _matching_decision(_FakeDB(), 1, _sug(confidence=0.50))
    assert dec2["band"] == "suggest"


def test_features_sorted_by_magnitude_and_enriched():
    feats = {"top1_cosine": 0.1, "si_prior": 0.9, "kb_purity": -0.5, "junk": "x"}
    _, dec = _matching_decision(_FakeDB(), 1, _sug(matched_item_id=1, features=feats))
    keys = [f["key"] for f in dec["features"]]
    # 'junk' (non-numeric) dropped; sorted by |value| desc.
    assert "junk" not in keys
    assert keys[0] == "si_prior"
    assert keys[1] == "kb_purity"
    # metadata attached from the schema
    top = dec["features"][0]
    assert top["index"] == 32
    assert "burst" in top["definition"]


def test_feature_total_is_data_driven():
    # The decision payload carries the real schema width (not a UI magic number).
    _, dec = _matching_decision(_FakeDB(), 1, _sug())
    assert dec["feature_total"] == len(features_meta.FEATURE_DEFS) == 47


def test_no_suggestion():
    m, dec = _matching_decision(_FakeDB(), 1, None)
    assert m["has_suggestion"] is False
    assert dec is None


def test_iso_helper():
    assert payloads._iso(None) is None
    assert payloads._iso("2026-01-01") == "2026-01-01"


# --------------------------------------------------------------------------- #
# Explanation carry-forward (every Make Decision open writes a fresh, still
# unexplained suggestion row that shadows the explained one; when the decision
# itself is unchanged the old explanation is still true, so it is carried over)
# --------------------------------------------------------------------------- #


def _row(**over: Any) -> dict[str, Any]:
    base = {
        "suggestion_id": 10,
        "predicted_label": "ti",
        "confidence": 0.32142857,
        "matched_item_id": None,
        "model_ver": "gbm-20260721T175504Z",
        "abstain_reason": "gbm_below_suggest",
        "features": {"top1_cosine": 0.9},
        "explanation": "because the probability stayed below the suggest line",
        "llm_used": True,
        "created_at": "2026-07-24T19:47:54+00:00",
    }
    base.update(over)
    return base


def test_carry_forward_reuses_explanation_when_decision_identical():
    latest = _row(suggestion_id=11, explanation=None, llm_used=False)
    carried = payloads._carried_explanation(latest, _row())
    assert carried is not None
    assert carried["explanation"].startswith("because the probability")
    assert carried["carried_from_suggestion_id"] == 10


def test_carry_forward_ignores_judge_only_feature_change():
    # The judge only reorders candidates after the fact; it never moves the label
    # or the confidence, so it must not invalidate an otherwise identical decision.
    latest = _row(
        suggestion_id=11, explanation=None, features={"top1_cosine": 0.9, "judge": {"x": 1}}
    )
    assert payloads._carried_explanation(latest, _row()) is not None


def test_carry_forward_refuses_on_exact_confidence_drift():
    # 0.3214 and 0.3221 both round to 0.32, but the prose quotes an exact number,
    # so the carried text would state a value the new decision no longer holds.
    latest = _row(suggestion_id=11, explanation=None, confidence=0.32210000)
    assert payloads._carried_explanation(latest, _row()) is None


def test_carry_forward_refuses_after_retrain():
    latest = _row(suggestion_id=11, explanation=None, model_ver="gbm-20260725T000000Z")
    assert payloads._carried_explanation(latest, _row()) is None


def test_carry_forward_refuses_on_different_decision():
    latest = _row(suggestion_id=11, explanation=None, predicted_label="pb001")
    assert payloads._carried_explanation(latest, _row()) is None
    other_neighbour = _row(suggestion_id=11, explanation=None, matched_item_id=999)
    assert payloads._carried_explanation(other_neighbour, _row()) is None


def test_carry_forward_refuses_when_feature_vector_changed():
    latest = _row(suggestion_id=11, explanation=None, features={"top1_cosine": 0.4})
    assert payloads._carried_explanation(latest, _row()) is None


def test_carry_forward_ignores_decayed_history_drift():
    # Measured on item 5917 over three consecutive opens of one unchanged
    # decision: test_age_days, hist_pb and hist_nd all crept, the last two down
    # at 2.7e-14 (numerically zero, still moving), because hist_* is a ratio of
    # decay-weighted candidate mass and the candidates age at different rates.
    explained = _row(
        features={
            "top1_cosine": 0.9,
            "test_age_days": 0.2488993405813739,
            "hist_pb": 2.681210876544236e-14,
            "hist_nd": 2.7652124682585877e-14,
        }
    )
    latest = _row(
        suggestion_id=11,
        explanation=None,
        features={
            "top1_cosine": 0.9,
            "test_age_days": 0.2490183744991811,
            "hist_pb": 2.6812739485949365e-14,
            "hist_nd": 2.76527751633911e-14,
        },
    )
    assert payloads._carried_explanation(latest, explained) is not None


def test_carry_forward_still_refuses_a_real_history_shift():
    # A genuine change in what history votes for is not drift, and it moves the
    # confidence too, which the key compares exactly.
    explained = _row(features={"top1_cosine": 0.9, "hist_pb": 0.1})
    latest = _row(
        suggestion_id=11,
        explanation=None,
        confidence=0.71,
        features={"top1_cosine": 0.9, "hist_pb": 0.8},
    )
    assert payloads._carried_explanation(latest, explained) is None


def test_carry_forward_ignores_clock_drift_features():
    # Measured on the live stand: two runs of the SAME decision (same label,
    # same exact confidence, same model, same neighbour) still differ, because
    # test_age_days and recency_top1 decay with wall-clock time alone. Counting
    # them would make carry-forward impossible by construction.
    explained = _row(features={"top1_cosine": 0.9, "test_age_days": 0.3629580144398265})
    latest = _row(
        suggestion_id=11,
        explanation=None,
        features={"top1_cosine": 0.9, "test_age_days": 0.3642368646301906},
    )
    assert payloads._carried_explanation(latest, explained) is not None


def test_carry_forward_never_mixes_rubric_and_classical():
    # A rubric row is a provisional hypothesis, never an explanation of a
    # classical decision (its model_ver format keeps the two apart).
    latest = _row(suggestion_id=11, explanation=None)
    rubric = _row(model_ver="rubric+qwen3:4b-q4_K_M")
    assert payloads._carried_explanation(latest, rubric) is None


def test_carry_forward_needs_a_non_empty_source():
    latest = _row(suggestion_id=11, explanation=None)
    assert payloads._carried_explanation(latest, _row(explanation="   ")) is None
    assert payloads._carried_explanation(latest, None) is None


def test_below_band_abstain_detection():
    # Only a below-band abstain may resurface the cold-start hypothesis.
    assert payloads._is_below_band_abstain(_row(confidence=0.32)) is True
    assert payloads._is_below_band_abstain(_row(predicted_label="pb001", confidence=0.20)) is True
    assert payloads._is_below_band_abstain(_row(predicted_label="pb001", confidence=0.80)) is False
    assert payloads._is_below_band_abstain(_row(model_ver="rubric+x", confidence=0.1)) is False
    assert payloads._is_below_band_abstain(None) is False
