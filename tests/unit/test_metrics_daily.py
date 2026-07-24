"""Daily metrics aggregation over suggestion outcomes (spec 03 §10.3)."""

from __future__ import annotations

from datetime import date

from _ml_synth import synth_suggestions

from analyzer_ng.ml.reporting import DailyMetrics, MetricsDailyJob, aggregate_daily


def test_aggregate_counts_suggestions_outcomes_and_abstain():
    rows = synth_suggestions(
        [
            (1, 0, "pb001", 0.90, "accepted"),
            (1, 0, "ab001", 0.80, "corrected"),
            (1, 0, "si001", 0.50, "ignored"),
            (1, 0, "ti", 0.10, "ignored"),  # abstained (predicted ti)
        ]
    )
    [dm] = aggregate_daily(rows)
    assert dm.project_id == 1
    assert dm.suggestions == 4
    assert dm.accepted == 1
    assert dm.corrected == 1
    assert dm.ignored == 2
    assert dm.abstained == 1


def test_auto_labeled_and_auto_corrected_track_the_auto_band():
    rows = synth_suggestions(
        [
            (1, 0, "pb001", 0.90, "accepted"),  # auto-labeled, accepted
            (1, 0, "ab001", 0.80, "corrected"),  # auto-labeled, later corrected (safety!)
            (1, 0, "si001", 0.50, "accepted"),  # suggested-only (below τ_auto)
        ]
    )
    [dm] = aggregate_daily(rows)
    assert dm.auto_labeled == 2
    assert dm.auto_corrected == 1


def test_per_label_breakdown_and_model_ver():
    rows = synth_suggestions(
        [
            (1, 0, "pb001", 0.90, "accepted"),
            (1, 0, "pb001", 0.80, "corrected"),
            (1, 0, "ab001", 0.90, "accepted"),
        ],
        model_ver="gbm-X",
    )
    [dm] = aggregate_daily(rows, emb_model_ver="e5s-int8-rABC")
    assert dm.per_label["pb"] == {"suggested": 2, "accepted": 1, "corrected": 1}
    assert dm.per_label["ab"] == {"suggested": 1, "accepted": 1, "corrected": 0}
    assert dm.model_ver == "gbm-X"
    assert dm.emb_model_ver == "e5s-int8-rABC"
    assert dm.to_dict()["emb_model_ver"] == "e5s-int8-rABC"


def test_aggregation_splits_by_project_and_day():
    rows = synth_suggestions(
        [
            (1, 0, "pb001", 0.9, "accepted"),
            (1, 1, "ab001", 0.9, "accepted"),  # next day
            (2, 0, "si001", 0.9, "accepted"),  # other project
        ]
    )
    out = {(dm.project_id, dm.day): dm for dm in aggregate_daily(rows)}
    assert len(out) == 3
    assert all(dm.suggestions == 1 for dm in out.values())


def test_aggregation_is_reproducible():
    rows = synth_suggestions([(1, 0, "pb001", 0.9, "accepted")] * 5)
    assert [d.to_dict() for d in aggregate_daily(rows)] == [
        d.to_dict() for d in aggregate_daily(rows)
    ]


# --------------------------------------------------------------------------- #
# Job wiring: fetch → aggregate → upsert (spec §10.3)
# --------------------------------------------------------------------------- #
class _FakeStatsStore:
    def __init__(self, suggestions):
        self._suggestions = suggestions
        self.upserted: list[DailyMetrics] = []

    def fetch_suggestions_for_day(self, day):
        return [s for s in self._suggestions if s["created_at"].date() == day]

    def upsert_daily_metrics(self, dm):
        self.upserted.append(dm)


def test_job_populates_metrics_daily_after_accept_correct_cycle():
    rows = synth_suggestions(
        [
            (1, 0, "pb001", 0.90, "accepted"),
            (1, 0, "ab001", 0.80, "corrected"),
        ]
    )
    day = rows[0]["created_at"].date()
    store = _FakeStatsStore(rows)
    job = MetricsDailyJob(store)
    written = job.run(day)
    assert len(written) == 1
    assert store.upserted == written
    dm = store.upserted[0]
    assert dm.day == day
    assert dm.accepted == 1 and dm.corrected == 1


def test_job_on_empty_day_writes_nothing():
    store = _FakeStatsStore([])
    job = MetricsDailyJob(store)
    assert job.run(date(2026, 1, 1)) == []
    assert store.upserted == []


def test_job_stamps_active_emb_model_ver():
    rows = synth_suggestions([(1, 0, "pb001", 0.9, "accepted")])
    store = _FakeStatsStore(rows)
    written = MetricsDailyJob(store, emb_model_ver="e5s-int8-rZZ").run(rows[0]["created_at"].date())
    assert written[0].emb_model_ver == "e5s-int8-rZZ"
