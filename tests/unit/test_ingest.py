"""Index-pipeline orchestration (spec 01 §4.4 index; spec 03 §1-§3).

Exercises :class:`IndexPipeline` against in-memory fakes so the ingest logic
(filter -> Drain -> signature -> upsert -> stats, plus BulkResponse shaping and
idempotent re-index) is provable without a database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analyzer_ng.amqp.models import Launch
from analyzer_ng.core.ingest import IndexPipeline
from analyzer_ng.db.repositories.models import SignatureIn, TestItemIn

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# TestItemIn is a pydantic DTO, not a test class — opt it out of collection.
TestItemIn.__test__ = False  # type: ignore[attr-defined]


def _launches() -> list[Launch]:
    data = json.loads((_FIXTURES / "rp_index_launches.json").read_text())
    return [Launch(**item) for item in data]


class FakeRetrieval:
    def __init__(self) -> None:
        self.items: dict[tuple[int, int], TestItemIn] = {}
        self.sigs: dict[tuple[int, int], SignatureIn] = {}

    def upsert_items(self, items: Any) -> int:
        for it in items:
            self.items[(it.project_id, it.item_id)] = it
        return len(list(items))

    def upsert_signatures(self, sigs: Any) -> int:
        for s in sigs:
            self.sigs[(s.project_id, s.item_id)] = s
        return len(list(sigs))


class FakeStats:
    def __init__(self) -> None:
        self.bumps: list[tuple[int, int, bool]] = []

    def bump_test_history(
        self, project_id: int, test_case_hash: int, failed: bool, ts: Any
    ) -> None:
        self.bumps.append((project_id, test_case_hash, failed))


class FakeDrainStore:
    """Minimal CAS-correct Drain3 state store."""

    def __init__(self) -> None:
        self.states: dict[int, tuple[bytes, int]] = {}
        self.templates: dict[int, list[dict]] = {}

    def load(self, project_id: int) -> tuple[bytes, int] | None:
        return self.states.get(project_id)

    def save(self, project_id: int, state: bytes, expected_version: int, config: dict) -> bool:
        current = self.states.get(project_id)
        current_version = current[1] if current else 0
        if current_version != expected_version:
            return False
        self.states[project_id] = (state, current_version + 1)
        return True

    def load_template_texts(self, project_id: int) -> list[str]:
        return [t["pattern"] for t in self.templates.get(project_id, [])]

    def upsert_templates(self, project_id: int, templates: Any) -> int:
        self.templates[project_id] = list(templates)
        return len(self.templates[project_id])


def _pipeline() -> tuple[IndexPipeline, FakeRetrieval, FakeStats, FakeDrainStore]:
    retrieval, stats, drain = FakeRetrieval(), FakeStats(), FakeDrainStore()
    return IndexPipeline(retrieval, stats, drain), retrieval, stats, drain


def test_index_filters_builds_signatures_and_persists() -> None:
    pipe, retrieval, stats, drain = _pipeline()
    resp = pipe.index_launches(_launches())

    assert resp.errors is False
    assert resp.took >= 0

    # foundExceptions per ERROR log for the "unique errors" UI.
    exc_by_log = {lr.logId: lr.foundExceptions for lr in resp.logResults}
    assert any("NullPointerException" in e for e in exc_by_log[3001])

    # Both items landed; issue_type is lowercased for issue_type_group extraction.
    assert set(retrieval.items) == {(123, 2001), (123, 2002)}
    assert retrieval.items[(123, 2001)].issue_type == "ab001"
    assert retrieval.items[(123, 2002)].issue_type == "pb002"

    # Signatures carry deterministic non-zero hashes + template ids.
    sig = retrieval.sigs[(123, 2001)]
    assert sig.exception_fp != 0
    assert sig.error_hash != 0
    assert sig.template_ids
    assert sig.exc_text  # NullPointerException chain

    # test_history_stats bumped once per item that has a test_case_hash.
    assert {b[1] for b in stats.bumps} == {1234567, 7654321}

    # Drain3 state persisted (CAS from version 0 -> 1) and templates mirrored.
    assert drain.states[123][1] == 1
    assert drain.templates.get(123)


def test_reindex_is_idempotent_same_counts_and_hashes() -> None:
    pipe, retrieval, _stats, drain = _pipeline()
    pipe.index_launches(_launches())
    first = {k: v.error_hash for k, v in retrieval.sigs.items()}

    pipe.index_launches(_launches())
    # No duplicate rows (upsert), and identical error_hash across runs (determinism).
    assert len(retrieval.items) == 2
    assert len(retrieval.sigs) == 2
    assert {k: v.error_hash for k, v in retrieval.sigs.items()} == first
    # CAS advanced by exactly one save per re-index.
    assert drain.states[123][1] == 2


def test_item_with_only_non_error_logs_gets_empty_signature() -> None:
    pipe, retrieval, _stats, _drain = _pipeline()
    launch = Launch(
        launchId=1,
        project=9,
        testItems=[
            {
                "testItemId": 1,
                "isAutoAnalyzed": False,
                "testItemName": "t",
                "logs": [{"logId": 1, "logLevel": 30000, "message": "just a warning"}],
            }
        ],
    )
    resp = pipe.index_launches([launch])
    assert resp.errors is False
    sig = retrieval.sigs[(9, 1)]
    # No ERROR logs survive filtering -> never-analyzed empty signature (§3.4).
    assert sig.exception_fp == 0
    assert sig.error_hash == 0
