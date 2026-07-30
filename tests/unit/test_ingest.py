"""Index-pipeline orchestration (spec 01 §4.4 index; spec 03 §1-§3).

Exercises :class:`IndexPipeline` against in-memory fakes so the ingest logic
(filter -> Drain -> signature -> upsert -> stats, plus BulkResponse shaping and
idempotent re-index) is provable without a database.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def upsert_items(self, items: Any, *, conn: object | None = None) -> int:
        for it in items:
            self.items[(it.project_id, it.item_id)] = it
        return len(list(items))

    def upsert_signatures(self, sigs: Any, *, conn: object | None = None) -> int:
        for s in sigs:
            self.sigs[(s.project_id, s.item_id)] = s
        return len(list(sigs))


class FakeStats:
    def __init__(self) -> None:
        self.bumps: list[tuple[int, int, bool]] = []

    def bump_test_history(
        self, project_id: int, test_case_hash: int, failed: bool, ts: Any, *, conn: object = None
    ) -> None:
        self.bumps.append((project_id, test_case_hash, failed))


class FakeDrainStore:
    """Minimal CAS-correct Drain3 state store."""

    def __init__(self) -> None:
        self.states: dict[int, tuple[bytes, int]] = {}
        self.templates: dict[int, list[dict]] = {}
        # The real Postgres CAS is atomic; model that so a check-then-set race in
        # the fake can't spuriously let two writers both "win".
        self._save_lock = threading.Lock()

    def load(self, project_id: int) -> tuple[bytes, int] | None:
        return self.states.get(project_id)

    def save(self, project_id: int, state: bytes, expected_version: int, config: dict) -> bool:
        with self._save_lock:
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


# --------------------------------------------------------------------------- #
# Concurrent burst-index for ONE project (the live CAS-collision defect).
#
# RP's project-wide "Generate index" fires a burst of `index` AMQP messages for
# one project; the worker pool processes them on parallel threads. Each does
# load-version -> mine -> CAS-save. Without serialization, concurrent batches read
# the same version and collide on the optimistic-concurrency save; a batch that
# loses every attempt raises and its launches are silently dropped.
# --------------------------------------------------------------------------- #


class _BarrierRaceDrainStore(FakeDrainStore):
    """Forces two index threads to mine the SAME CAS version (no serialization).

    ``load`` rendezvouses both threads on a barrier before returning, so absent a
    per-project lock both mine version 0 and exactly one loses the atomic CAS —
    the race that drops a launch. (Used with ``cas_retries=1`` so each thread
    loads exactly once; no reload → no second barrier party → no deadlock.)
    """

    def __init__(self, parties: int = 2) -> None:
        super().__init__()
        self._barrier = threading.Barrier(parties, timeout=10)

    def load(self, project_id: int) -> tuple[bytes, int] | None:
        result = super().load(project_id)
        self._barrier.wait()
        return result


class _LockingDrainStore(FakeDrainStore):
    """CAS-correct store exposing a real per-project advisory-lock stand-in.

    ``project_lock`` is a genuine per-project mutex, and ``load`` sleeps to widen
    the load->save window so that WITHOUT the lock the two threads would collide;
    the lock is what makes them serialize and both succeed.
    """

    def __init__(self, load_delay: float = 0.05) -> None:
        super().__init__()
        self._load_delay = load_delay
        self._locks: dict[int, threading.Lock] = defaultdict(threading.Lock)

    def load(self, project_id: int) -> tuple[bytes, int] | None:
        result = super().load(project_id)
        time.sleep(self._load_delay)
        return result

    @contextmanager
    def project_lock(self, project_id: int) -> Iterator[None]:
        with self._locks[project_id]:
            yield


def _launch_for(project: int, launch_id: int, item_id: int) -> Launch:
    return Launch(
        launchId=launch_id,
        launchName="L",
        launchNumber=launch_id,
        project=project,
        testItems=[
            {
                "testItemId": item_id,
                "isAutoAnalyzed": False,
                "testItemName": "t",
                "testCaseHash": item_id,
                "issueType": "ti001",
                "logs": [
                    {
                        "logId": item_id * 10,
                        "logLevel": 40000,
                        "message": "NullPointerException at Foo.bar(Foo.java:1)",
                    }
                ],
            }
        ],
    )


def _run_concurrent(pipe: IndexPipeline, launches: list[Launch]) -> list[Any]:
    results: dict[int, Any] = {}

    def worker(idx: int, launch: Launch) -> None:
        # index_launches catches per-project failures internally (errors flag),
        # so this never raises; capture the BulkResponse.
        results[idx] = pipe.index_launches([launch])

    threads = [
        threading.Thread(target=worker, args=(i, launch)) for i, launch in enumerate(launches)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads), "index worker deadlocked"
    return [results[i] for i in range(len(launches))]


def test_concurrent_index_without_lock_drops_a_batch() -> None:
    # Baseline: no per-project serialization + a single CAS attempt reproduces the
    # live defect — two concurrent batches for project 7 collide and one is lost.
    drain = _BarrierRaceDrainStore(parties=2)
    retrieval, stats = FakeRetrieval(), FakeStats()
    pipe = IndexPipeline(retrieval, stats, drain, cas_retries=1)

    responses = _run_concurrent(pipe, [_launch_for(7, 1, 2001), _launch_for(7, 2, 2002)])

    # Exactly one batch lost the CAS and raised -> its launch dropped.
    assert sum(1 for r in responses if r.errors) == 1
    present = [(7, 2001) in retrieval.items, (7, 2002) in retrieval.items]
    assert present.count(True) == 1, "expected exactly one batch to survive the race"
    assert drain.states[7][1] == 1  # only the winner's save landed


def test_concurrent_index_with_advisory_lock_persists_both() -> None:
    # Fix: the per-project advisory lock serializes load->mine->CAS, so both
    # concurrent batches for project 7 persist and neither is lost — even with a
    # single CAS attempt and a widened race window.
    drain = _LockingDrainStore()
    retrieval, stats = FakeRetrieval(), FakeStats()
    pipe = IndexPipeline(retrieval, stats, drain, cas_retries=1)

    responses = _run_concurrent(pipe, [_launch_for(7, 1, 2001), _launch_for(7, 2, 2002)])

    assert all(r.errors is False for r in responses), "no batch should fail under the lock"
    assert (7, 2001) in retrieval.items and (7, 2002) in retrieval.items
    assert drain.states[7][1] == 2  # two serialized saves, version advanced by exactly 2
