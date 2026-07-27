"""Eager extractor cache warmup: stale template-set enumeration (issue #5).

Items analyzed before commit ba23301 (extractor ``post_validate`` dropped the
whole output when one component failed the identifier regex) ended
``validation_fail``, so their template sets have no cached extractor output and
the 19 one-hot ``llm_failing_layer_*`` / ``llm_error_class_*`` GBM columns fall
back to the ``unknown`` sentinel. The cache heals lazily on first touch; the
warmup tool replays those template sets eagerly instead.

Pure logic lives here — the verbatim enumeration SQL plus the Python selection
of stale template sets — so it unit-tests without a database (the
``ml/early_replay.py`` + ``tools/replay-early-aa/`` split). The CLI in
``tools/warm-extractor-cache/`` does the pool/client/engine plumbing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from analyzer_ng.llm.roles.extractor import extractor_template_hash

# One row per distinct template set (spec 04 §4.2: the extractor runs once per
# novel template combination per project, not per item), carrying the newest
# item as the replay representative — its facts feed the fact loader exactly
# like a production enqueue. The test_item join mirrors
# ``PgLlmFacts.load_signature``: a signature whose item is gone cannot be
# replayed, so it is not enumerated. Newest-first so a ``--limit`` cut warms the
# template sets most likely to recur.
TEMPLATE_SETS_SQL = """
SELECT fs.exception_fp, fs.template_ids, max(fs.item_id) AS item_id
FROM analyzer.failure_signature fs
JOIN analyzer.test_item ti USING (project_id, item_id)
WHERE fs.project_id = %(project)s
GROUP BY fs.exception_fp, fs.template_ids
ORDER BY item_id DESC
"""

# Template hashes that already have a fresh extractor cache row. Freshness is
# the same ``created_at`` window as the read paths (``get_fresh`` /
# ``get_extractor_by_template``, spec 04 §3.0): a stale row misses there, so it
# must not suppress a warmup either.
FRESH_EXTRACTOR_HASHES_SQL = """
SELECT DISTINCT template_hash
FROM analyzer.llm_cache
WHERE project_id = %(project)s
  AND role = 'extractor'
  AND template_hash IS NOT NULL
  AND created_at >= now() - make_interval(days => %(ttl_days)s)
"""


@dataclass(frozen=True)
class TemplateSet:
    """One distinct template set to warm, with its replay representative item."""

    template_hash: int
    exception_fp: int
    template_ids: tuple[int, ...]
    item_id: int


def stale_template_sets(
    rows: Iterable[Sequence[Any]],
    fresh_hashes: Iterable[int],
    limit: int | None = None,
) -> list[TemplateSet]:
    """Select the template sets lacking a fresh extractor cache row.

    ``rows`` are ``(exception_fp, template_ids, item_id)`` tuples from
    :data:`TEMPLATE_SETS_SQL` (newest representative first). The hash is
    computed with :func:`extractor_template_hash` — the single source of truth
    shared with the cache write and the feature-time lookup — which sorts
    ``template_ids``, so two signatures holding the same set in a different
    order collapse to one entry here (the first, i.e. newest, representative
    wins). Anything whose hash appears in ``fresh_hashes`` is already cached
    and skipped. ``limit`` bounds the returned list (``None`` = unbounded).
    """
    fresh = set(fresh_hashes)
    seen: set[int] = set()
    out: list[TemplateSet] = []
    for exception_fp, template_ids, item_id in rows:
        if limit is not None and len(out) >= limit:
            break
        fp = int(exception_fp or 0)
        ids = tuple(int(t) for t in (template_ids or []))
        thash = extractor_template_hash(fp, ids)
        if thash in fresh or thash in seen:
            continue
        seen.add(thash)
        out.append(
            TemplateSet(
                template_hash=thash,
                exception_fp=fp,
                template_ids=ids,
                item_id=int(item_id),
            )
        )
    return out
