"""Launch grouping determinism + burst prior (spec 03 §5, acceptance §11)."""

from __future__ import annotations

import random

from analyzer_ng.core.grouping import GroupItem, group_launch


def _unit(*vals: float) -> list[float]:
    import numpy as np

    v = np.asarray(vals, dtype=np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _fp_items(n: int, fp: int, base_id: int = 0) -> list[GroupItem]:
    return [
        GroupItem(
            item_id=base_id + i,
            exception_fp=fp,
            error_hash=fp * 1000,
            emb=_unit(1.0, 0.0, 0.0),
            has_stacktrace=True,
            log_count=3,
            template_ids=(fp,),
        )
        for i in range(n)
    ]


def test_exact_fp_buckets_group_together():
    items = _fp_items(3, fp=11, base_id=1) + _fp_items(2, fp=22, base_id=100)
    groups = group_launch(items)
    sizes = sorted(len(g.members) for g in groups)
    assert sizes == [2, 3]


def test_shuffled_input_gives_identical_groups_and_representatives():
    items = _fp_items(3, fp=11, base_id=1) + _fp_items(2, fp=22, base_id=100)
    # Cosine leftover with fp==0.
    items.append(
        GroupItem(
            item_id=500,
            exception_fp=0,
            error_hash=7,
            emb=_unit(0.0, 1.0, 0.0),
            has_stacktrace=False,
            log_count=1,
            template_ids=(9,),
        )
    )

    def signature(groups):
        return sorted(
            (
                tuple(sorted(m.item_id for m in g.members)),
                g.representative.item_id,
                round(g.si_prior, 6),
            )
            for g in groups
        )

    baseline = signature(group_launch(items))
    rng = random.Random(1234)
    for _ in range(20):
        shuffled = items[:]
        rng.shuffle(shuffled)
        assert signature(group_launch(shuffled)) == baseline


def test_representative_prefers_stacktrace_then_logcount_then_lowest_id():
    members = [
        GroupItem(item_id=5, exception_fp=1, error_hash=1, has_stacktrace=False, log_count=9),
        GroupItem(item_id=8, exception_fp=1, error_hash=1, has_stacktrace=True, log_count=2),
        GroupItem(item_id=3, exception_fp=1, error_hash=1, has_stacktrace=True, log_count=2),
    ]
    (group,) = group_launch(members)
    # Both id 8 and 3 have stacktrace+log_count 2; lower id wins (−item_id max).
    assert group.representative.item_id == 3


def test_greedy_cosine_attaches_similar_leftovers():
    a = GroupItem(item_id=1, exception_fp=0, error_hash=1, emb=_unit(1, 0, 0), template_ids=(1,))
    b = GroupItem(
        item_id=2, exception_fp=0, error_hash=1, emb=_unit(0.99, 0.14, 0), template_ids=(1,)
    )
    c = GroupItem(item_id=3, exception_fp=0, error_hash=2, emb=_unit(0, 1, 0), template_ids=(2,))
    groups = group_launch([a, b, c])
    assert sorted(len(g.members) for g in groups) == [1, 2]


def test_burst_prior_fires_for_dominant_new_fingerprint():
    # 12 of 20 share a new fingerprint → single group, si_prior ≥ 0.5.
    burst = _fp_items(12, fp=77, base_id=1)
    rest = [
        GroupItem(
            item_id=200 + i,
            exception_fp=1000 + i,
            error_hash=5000 + i,
            emb=_unit(0, 0, 1),
            template_ids=(1000 + i,),
        )
        for i in range(8)
    ]
    groups = group_launch(burst + rest, is_error_hash_new=lambda _h: True)
    burst_group = max(groups, key=lambda g: len(g.members))
    assert len(burst_group.members) == 12
    assert burst_group.si_prior >= 0.5


def test_burst_prior_zero_below_threshold():
    burst = _fp_items(3, fp=77, base_id=1)
    rest = [
        GroupItem(
            item_id=200 + i,
            exception_fp=1000 + i,
            error_hash=5000 + i,
            emb=_unit(0, 0, 1),
            template_ids=(1000 + i,),
        )
        for i in range(17)
    ]
    groups = group_launch(burst + rest, is_error_hash_new=lambda _h: True)
    assert all(g.si_prior == 0.0 for g in groups)


def test_burst_prior_suppressed_when_fingerprint_seen_before():
    burst = _fp_items(12, fp=77, base_id=1)
    rest = _fp_items(8, fp=1, base_id=200)
    groups = group_launch(burst + rest, is_error_hash_new=lambda _h: False)
    assert all(g.si_prior == 0.0 for g in groups)


def test_lexical_only_degrade_no_embeddings():
    # No embeddings: fp==0 items each become their own group (no cosine attach).
    items = [
        GroupItem(item_id=i, exception_fp=0, error_hash=i, emb=None, template_ids=(i,))
        for i in range(1, 4)
    ]
    groups = group_launch(items)
    assert len(groups) == 3
