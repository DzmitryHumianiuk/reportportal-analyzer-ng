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
        "mode_id": 7, "status": "confirmed", "label": "si", "title": "t",
        "purity": 0.8, "support": 5, "seed_key": "conn_timeout",
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
