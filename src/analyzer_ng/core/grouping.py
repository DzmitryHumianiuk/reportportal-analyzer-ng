"""Launch grouping (spec 03 §5).

Groups failures *within one launch* so a single diagnosis fans out to all members
of a group. Three deterministic passes:

1. exact ``exception_fp`` buckets (fp != 0), sorted by fp;
2. greedy cosine agglomeration of the ``fp == 0`` leftovers (θ = 0.83), items
   visited in ascending ``item_id`` order, centroid = re-normalized running mean;
3. a single merge pass over groups whose centroids are close (cos ≥ θ) *and*
   share templates (Jaccard ≥ 0.5), ordered by group index.

Then a representative and a burst / system-issue prior (``si_prior``) per group.
Fixed iteration orders + float32 running means + no RNG make the output identical
under any input permutation (acceptance §11 "shuffled input order → identical
groups and representatives").
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

# spec 03 §5 parameters.
THETA_GROUP = 0.83
BURST_X = 0.40
BURST_N = 5
JACCARD_MIN = 0.5
SI_PRIOR_CAP = 0.9
SI_PRIOR_BASE = 0.5


@dataclass(frozen=True)
class GroupItem:
    """One analyzed test item eligible for launch grouping."""

    item_id: int
    exception_fp: int
    error_hash: int
    emb: Sequence[float] | None = None
    has_stacktrace: bool = False
    log_count: int = 0
    template_ids: tuple[int, ...] = ()


@dataclass
class LaunchGroup:
    """A group of co-failing items with its representative and burst prior."""

    members: list[GroupItem]
    centroid: np.ndarray | None
    template_ids: set[int]
    representative: GroupItem | None = None
    si_prior: float = 0.0

    @property
    def fingerprint(self) -> int:
        """Group signature hash = representative's ``error_hash`` (spec 02 §2.9)."""
        return self.representative.error_hash if self.representative is not None else 0


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return vec if norm < 1e-12 else (vec / norm).astype(np.float32)


def _emb_array(item: GroupItem) -> np.ndarray | None:
    if item.emb is None:
        return None
    return np.asarray(item.emb, dtype=np.float32)


def _new_group(members: Sequence[GroupItem]) -> LaunchGroup:
    vecs = [v for v in (_emb_array(m) for m in members) if v is not None]
    centroid = _normalize(np.mean(np.stack(vecs), axis=0)) if vecs else None
    templates: set[int] = set()
    for m in members:
        templates.update(m.template_ids)
    return LaunchGroup(members=list(members), centroid=centroid, template_ids=templates)


def _add_member(group: LaunchGroup, item: GroupItem) -> None:
    """Attach an item, updating the re-normalized running-mean centroid (float32)."""
    vec = _emb_array(item)
    if vec is not None:
        n = sum(1 for m in group.members if m.emb is not None)
        if group.centroid is None or n == 0:
            group.centroid = _normalize(vec)
        else:
            running = group.centroid * n + vec
            group.centroid = _normalize(running / (n + 1))
    group.members.append(item)
    group.template_ids.update(item.template_ids)


def _cosine(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None:
        return -1.0
    return float(np.dot(a, b))


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _merge_close(groups: list[LaunchGroup], theta: float, jaccard_min: float) -> list[LaunchGroup]:
    """Single ordered pass merging centroid-close, template-overlapping groups."""
    merged: list[LaunchGroup] = []
    for group in groups:
        target: LaunchGroup | None = None
        for existing in merged:
            if (
                _cosine(existing.centroid, group.centroid) >= theta
                and _jaccard(existing.template_ids, group.template_ids) >= jaccard_min
            ):
                target = existing
                break
        if target is None:
            merged.append(group)
            continue
        for item in group.members:
            _add_member(target, item)
    return merged


def _representative(members: Sequence[GroupItem]) -> GroupItem:
    """max by (has_stacktrace, log_count, -item_id) — a deterministic tie-break (§5)."""
    return max(members, key=lambda i: (i.has_stacktrace, i.log_count, -i.item_id))


def group_launch(
    items: Sequence[GroupItem],
    *,
    theta: float = THETA_GROUP,
    burst_x: float = BURST_X,
    burst_n: int = BURST_N,
    is_error_hash_new: Callable[[int], bool] | None = None,
) -> list[LaunchGroup]:
    """Group one launch's analyzed items (spec 03 §5). Deterministic.

    ``is_error_hash_new(error_hash)`` reports whether a group's representative
    failure has *not* been seen in prior history (spec 02 history query); it
    gates the burst ``si_prior``. Defaults to "always new" (cold project).
    """
    is_new = is_error_hash_new if is_error_hash_new is not None else (lambda _h: True)

    groups: list[LaunchGroup] = []

    # Pass 1: exact exception_fp buckets (skip fp == 0), deterministic fp order.
    by_fp: dict[int, list[GroupItem]] = {}
    leftovers: list[GroupItem] = []
    for item in items:
        if item.exception_fp == 0:
            leftovers.append(item)
        else:
            by_fp.setdefault(item.exception_fp, []).append(item)
    for fp in sorted(by_fp):
        members = sorted(by_fp[fp], key=lambda i: i.item_id)
        groups.append(_new_group(members))

    # Pass 2: greedy cosine attach of leftovers, ascending item_id.
    for item in sorted(leftovers, key=lambda i: i.item_id):
        vec = _emb_array(item)
        best: LaunchGroup | None = None
        best_sim = theta
        if vec is not None:
            for group in groups:
                sim = _cosine(vec, group.centroid)
                if sim >= best_sim:
                    best_sim = sim
                    best = group
        if best is not None:
            _add_member(best, item)
        else:
            groups.append(_new_group([item]))

    # Pass 3: merge close groups (centroid cos ≥ θ AND template Jaccard ≥ 0.5).
    groups = _merge_close(groups, theta, JACCARD_MIN)

    # Representatives + burst / system-issue prior.
    total = len(items)
    for group in groups:
        group.representative = _representative(group.members)
        group.si_prior = 0.0
        rep = group.representative
        if (
            total > 0
            and is_new(rep.error_hash)
            and len(group.members) >= burst_n
            and len(group.members) / total > burst_x
        ):
            group.si_prior = min(SI_PRIOR_CAP, SI_PRIOR_BASE + len(group.members) / total)
    return groups
