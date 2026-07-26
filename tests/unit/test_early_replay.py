"""Replay harness for early per-item AA (docs/EARLY-ITEM-AA.md).

Pins the singleton rewrite (exactly the four launch-context features move, to
exactly the constants the degenerate group-of-one produces) and the flip-rate
aggregation the consilium gate reads.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from analyzer_ng.core.decision import TAU_AUTO, TAU_SUGGEST
from analyzer_ng.ml.early_replay import (
    SINGLETON_OVERRIDES,
    ReplayRecord,
    aggregate,
    band,
    replay_row,
    singletonize,
)


def test_singletonize_moves_exactly_the_launch_context_features() -> None:
    snapshot = {
        "top1_cosine": 0.93,
        "hist_pb": 0.8,
        "group_dominance": 0.35,
        "co_failure_group_size": 0.61,
        "launch_fail_fraction": 0.4,
        "si_prior": 0.9,
    }
    out = singletonize(snapshot)
    assert out["group_dominance"] == 1.0
    assert out["co_failure_group_size"] == math.log1p(1) / math.log1p(200)
    assert out["launch_fail_fraction"] == 0.0
    assert out["si_prior"] == 0.0
    # Everything else byte-identical, input untouched.
    assert out["top1_cosine"] == 0.93
    assert out["hist_pb"] == 0.8
    assert snapshot["si_prior"] == 0.9
    assert set(out) == set(snapshot)


def test_singleton_overrides_is_the_complete_documented_set() -> None:
    assert set(SINGLETON_OVERRIDES) == {
        "group_dominance",
        "co_failure_group_size",
        "launch_fail_fraction",
        "si_prior",
    }


def test_band_boundaries_match_policy() -> None:
    assert band(TAU_AUTO) == "auto"
    assert band(TAU_AUTO - 1e-9) == "suggest"
    assert band(TAU_SUGGEST) == "suggest"
    assert band(TAU_SUGGEST - 1e-9) == "abstain"


class _FakePredictor:
    """Flips si to pb when si_prior is zeroed — the structural early bias."""

    def predict(self, features: dict, project_id: int) -> SimpleNamespace | None:
        if features.get("si_prior", 0.0) > 0.5:
            return SimpleNamespace(label="si", max_prob=0.8)
        return SimpleNamespace(label="pb", max_prob=0.5)


def test_replay_row_detects_the_si_flip() -> None:
    rec = replay_row(_FakePredictor(), 1, 42, {"si_prior": 0.9, "top1_cosine": 0.9})
    assert rec is not None
    assert (rec.finish_label, rec.early_label) == ("si", "pb")
    assert rec.label_flipped
    assert rec.band_changed  # auto (0.8) -> suggest (0.5)


def test_replay_row_none_when_model_cold() -> None:
    class Cold:
        def predict(self, features: dict, project_id: int) -> None:
            return None

    assert replay_row(Cold(), 1, 42, {"si_prior": 0.9}) is None


def test_aggregate_reports_rates_and_pairs() -> None:
    records = [
        ReplayRecord(1, "si", 0.8, "pb", 0.5),  # label flip + band move
        ReplayRecord(2, "pb", 0.9, "pb", 0.9),  # unchanged
        ReplayRecord(3, "pb", 0.5, "pb", 0.44),  # band move only
    ]
    report = aggregate(records)
    assert report["n"] == 3
    assert report["label_flip_rate"] == round(1 / 3, 4)
    assert report["label_flips_by_pair"] == {"si->pb": 1}
    assert report["band_change_rate"] == round(2 / 3, 4)
    assert report["band_changes_by_pair"] == {"auto->suggest": 1, "suggest->abstain": 1}
    assert report["max_abs_conf_delta"] == 0.3


def test_aggregate_empty() -> None:
    assert aggregate([]) == {"n": 0}
