"""Daily metrics aggregation + reporting hooks (spec 03 §10.3).

The nightly reporting job rolls up each day's ``suggestion`` outcomes into
``metrics_daily`` (spec 02 §2.12), one row per ``(project, day)``. It reports the
counts the safety story needs — how many suggestions were shown, accepted,
corrected, ignored, or abstained, and, split out, how many were **auto-labeled**
and how many of those were later **auto-corrected** (a wrong auto-label that a user
had to fix — the key safety metric, §10.3).

Everything is aggregated by the suggestion's *shown* day (``created_at``): "of the
suggestions shown on day D, how many were ultimately accepted / corrected", which
is stable and reproducible from the ``suggestion`` rows alone (outcomes are stamped
onto the same row by ``defect_update``). The four base groups' breakdown is stored
in the ``per_label`` jsonb exactly as spec 02 §2.12 documents.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from analyzer_ng.core.decision import TAU_AUTO
from analyzer_ng.core.features import BASE_LABELS

logger = logging.getLogger(__name__)


def _base(locator: str | None) -> str | None:
    """Base issue-type group of a predicted locator ('pb001'→'pb'); None if abstain."""
    if not locator:
        return None
    prefix = "".join(c for c in locator[:2] if c.isalpha()).lower()
    return prefix if prefix in BASE_LABELS else None


@dataclass(frozen=True)
class DailyMetrics:
    """One ``(project, day)`` rollup of suggestion outcomes (spec §10.3)."""

    project_id: int
    day: date
    suggestions: int = 0
    accepted: int = 0
    corrected: int = 0
    ignored: int = 0
    abstained: int = 0
    auto_labeled: int = 0
    auto_corrected: int = 0
    per_label: dict[str, dict[str, int]] = field(default_factory=dict)
    model_ver: str | None = None
    emb_model_ver: str | None = None

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "day": self.day.isoformat(),
            "suggestions": self.suggestions,
            "accepted": self.accepted,
            "corrected": self.corrected,
            "ignored": self.ignored,
            "abstained": self.abstained,
            "auto_labeled": self.auto_labeled,
            "auto_corrected": self.auto_corrected,
            "per_label": self.per_label,
            "model_ver": self.model_ver,
            "emb_model_ver": self.emb_model_ver,
        }


@dataclass
class _Acc:
    """Mutable per-(project,day) accumulator."""

    suggestions: int = 0
    accepted: int = 0
    corrected: int = 0
    ignored: int = 0
    abstained: int = 0
    auto_labeled: int = 0
    auto_corrected: int = 0
    per_label: dict[str, dict[str, int]] = field(default_factory=dict)
    model_ver: str | None = None

    def _bucket(self, base: str) -> dict[str, int]:
        return self.per_label.setdefault(base, {"suggested": 0, "accepted": 0, "corrected": 0})


def aggregate_daily(
    suggestions: list[dict],
    *,
    tau_auto: float = TAU_AUTO,
    emb_model_ver: str | None = None,
) -> list[DailyMetrics]:
    """Roll ``suggestion`` rows up into per-(project, day) :class:`DailyMetrics`.

    Each row is ``{project_id, created_at (datetime), predicted_label (locator or
    'ti'), confidence, outcome, model_ver}``. ``emb_model_ver`` is the install's
    active embedding version stamped onto every rollup (§10.3; suggestion rows do not
    carry it). Deterministic: rows are grouped and output sorted by ``(project_id, day)``.
    """
    accs: dict[tuple[int, date], _Acc] = defaultdict(_Acc)
    for s in suggestions:
        pid = int(s["project_id"])
        day = s["created_at"].date()
        acc = accs[(pid, day)]
        acc.suggestions += 1
        acc.model_ver = s.get("model_ver") or acc.model_ver

        outcome = s.get("outcome")
        base = _base(s.get("predicted_label"))
        is_abstain = base is None
        is_auto = (not is_abstain) and float(s.get("confidence", 0.0)) >= tau_auto

        if is_abstain:
            acc.abstained += 1
        if outcome == "accepted":
            acc.accepted += 1
        elif outcome == "corrected":
            acc.corrected += 1
        elif outcome == "ignored":
            acc.ignored += 1
        if is_auto:
            acc.auto_labeled += 1
            if outcome == "corrected":
                acc.auto_corrected += 1
        if base is not None:
            bucket = acc._bucket(base)
            bucket["suggested"] += 1
            if outcome == "accepted":
                bucket["accepted"] += 1
            elif outcome == "corrected":
                bucket["corrected"] += 1

    out = [
        DailyMetrics(
            project_id=pid,
            day=day,
            suggestions=a.suggestions,
            accepted=a.accepted,
            corrected=a.corrected,
            ignored=a.ignored,
            abstained=a.abstained,
            auto_labeled=a.auto_labeled,
            auto_corrected=a.auto_corrected,
            per_label=a.per_label,
            model_ver=a.model_ver,
            emb_model_ver=emb_model_ver,
        )
        for (pid, day), a in accs.items()
    ]
    return sorted(out, key=lambda d: (d.project_id, d.day))


class _MetricsSource(Protocol):
    """The store surface the nightly job needs (spec 02 §4.4)."""

    def fetch_suggestions_for_day(self, day: date) -> list[dict]: ...
    def upsert_daily_metrics(self, dm: DailyMetrics) -> None: ...


class MetricsDailyJob:
    """Nightly rollup: fetch a day's suggestions → aggregate → upsert (spec §10.3)."""

    def __init__(self, store: _MetricsSource, *, emb_model_ver: str | None = None) -> None:
        self._store = store
        self._emb_model_ver = emb_model_ver

    def run(self, day: date) -> list[DailyMetrics]:
        suggestions = self._store.fetch_suggestions_for_day(day)
        metrics = aggregate_daily(suggestions, emb_model_ver=self._emb_model_ver)
        for dm in metrics:
            self._store.upsert_daily_metrics(dm)
        auto_corrected = sum(dm.auto_corrected for dm in metrics)
        logger.info(
            "metrics_daily %s: %d project(s), %d suggestions, %d auto-labeled, "
            "%d auto-corrected",
            day.isoformat(),
            len(metrics),
            sum(dm.suggestions for dm in metrics),
            sum(dm.auto_labeled for dm in metrics),
            auto_corrected,
        )
        if auto_corrected:
            logger.warning(
                "metrics_daily %s: %d auto-labeled suggestion(s) were later corrected",
                day.isoformat(),
                auto_corrected,
            )
        return metrics
