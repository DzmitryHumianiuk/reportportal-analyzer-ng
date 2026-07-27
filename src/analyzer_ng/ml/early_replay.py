"""Offline replay: what would early per-item AA have decided? (docs/EARLY-ITEM-AA.md)

The early pass analyzes an item as a group of one, which freezes the
launch-context features at constants the model never chose: a singleton group
dominates its own one-failure launch completely, the burst rule cannot fire,
and the launch fraction is unknown. This module rewrites a stored launch-finish
feature snapshot into that singleton corner, scores both vectors with the same
shipped GBM, and reports how often the answer changes.

The flip rate this produces is the consilium's gate for ever widening the early
label policy beyond deterministic decisions. Pure functions here; the CLI in
``tools/replay-early-aa/`` does the database and model plumbing.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from analyzer_ng.core.decision import TAU_AUTO, TAU_SUGGEST

# The values every launch-context feature is forced to when an item is analyzed
# as a group of one (verified against core/features.py):
#   group_dominance       = group_size / launch_failures = 1/1
#   co_failure_group_size = log1p(1) / log1p(200)   (the size-1 point)
#   launch_fail_fraction  = 0.0  (mid-launch the total item count does not
#                                 exist, so the early route sends no count and
#                                 the fraction stays 0 by design; launch-finish
#                                 snapshots carry a live value now that the
#                                 patched service-api fills
#                                 Launch.launchItemsCount on the analyze route)
#   si_prior              = 0.0                      (burst needs >= 5 members)
SINGLETON_OVERRIDES: dict[str, float] = {
    "group_dominance": 1.0,
    "co_failure_group_size": math.log1p(1) / math.log1p(200),
    "launch_fail_fraction": 0.0,
    "si_prior": 0.0,
}


class Predicts(Protocol):
    """The one slice of GbmPredictor the replay needs."""

    def predict(self, features: dict[str, float], project_id: int) -> Any: ...


def singletonize(features: dict[str, float]) -> dict[str, float]:
    """A copy of the snapshot with the launch-context features forced to their
    singleton constants. Every other feature is left byte-identical: the item's
    own logs, history and retrieval do not change mid-launch."""
    out = dict(features)
    out.update(SINGLETON_OVERRIDES)
    return out


def band(confidence: float) -> str:
    """The policy band a confidence lands in (spec §6.6)."""
    if confidence >= TAU_AUTO:
        return "auto"
    if confidence >= TAU_SUGGEST:
        return "suggest"
    return "abstain"


@dataclass(frozen=True)
class ReplayRecord:
    """One item's finish-context versus singleton-context comparison."""

    item_id: int
    finish_label: str
    finish_conf: float
    early_label: str
    early_conf: float

    @property
    def label_flipped(self) -> bool:
        return self.finish_label != self.early_label

    @property
    def band_changed(self) -> bool:
        return band(self.finish_conf) != band(self.early_conf)


def replay_row(
    predictor: Predicts, project_id: int, item_id: int, features: dict[str, float]
) -> ReplayRecord | None:
    """Score the stored snapshot and its singletonized twin with one model.

    Returns None when the model is cold (no shipped GBM) — the caller reports
    that instead of fabricating numbers.
    """
    finish = predictor.predict(features, project_id)
    early = predictor.predict(singletonize(features), project_id)
    if finish is None or early is None:
        return None
    return ReplayRecord(
        item_id=item_id,
        finish_label=finish.label,
        finish_conf=finish.max_prob,
        early_label=early.label,
        early_conf=early.max_prob,
    )


def aggregate(records: list[ReplayRecord]) -> dict[str, Any]:
    """The report the consilium gate reads: flip rates by class, band moves,
    and the mean confidence shift."""
    n = len(records)
    if n == 0:
        return {"n": 0}
    flips = [r for r in records if r.label_flipped]
    band_moves = [r for r in records if r.band_changed]
    flip_pairs = Counter(f"{r.finish_label}->{r.early_label}" for r in flips)
    band_pairs = Counter(f"{band(r.finish_conf)}->{band(r.early_conf)}" for r in band_moves)
    return {
        "n": n,
        "label_flip_rate": round(len(flips) / n, 4),
        "label_flips_by_pair": dict(flip_pairs.most_common()),
        "band_change_rate": round(len(band_moves) / n, 4),
        "band_changes_by_pair": dict(band_pairs.most_common()),
        "mean_abs_conf_delta": round(
            sum(abs(r.finish_conf - r.early_conf) for r in records) / n, 4
        ),
        "max_abs_conf_delta": round(max(abs(r.finish_conf - r.early_conf) for r in records), 4),
    }
