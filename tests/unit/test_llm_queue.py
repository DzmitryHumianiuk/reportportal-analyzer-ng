"""Bounded single-worker priority queue tests (spec 04 §1.5)."""

from __future__ import annotations

import threading
import time

from analyzer_ng.llm.queue import ROLE_PRIORITY, LLMJob, LlmQueue


def test_role_priorities() -> None:
    assert ROLE_PRIORITY["judge"] < ROLE_PRIORITY["extractor"]
    assert ROLE_PRIORITY["extractor"] == ROLE_PRIORITY["coldstart"]
    assert ROLE_PRIORITY["coldstart"] < ROLE_PRIORITY["explainer"]


def test_drain_orders_by_priority_then_fifo() -> None:
    order: list[str] = []
    q = LlmQueue(lambda job: order.append(f"{job.role}:{job.item_id}"), maxsize=100)
    q.enqueue(LLMJob("explainer", 1, 1))
    q.enqueue(LLMJob("judge", 1, 2))
    q.enqueue(LLMJob("extractor", 1, 3))
    q.enqueue(LLMJob("extractor", 1, 4))
    q.enqueue(LLMJob("judge", 1, 5))
    while q.drain_once():
        pass
    # judges first (FIFO among them), then extractors (FIFO), then explainer.
    assert order == ["judge:2", "judge:5", "extractor:3", "extractor:4", "explainer:1"]


def test_overflow_drops_oldest_lowest_priority() -> None:
    drops: list[str] = []
    q = LlmQueue(lambda job: None, maxsize=3, on_drop=drops.append)
    q.enqueue(LLMJob("explainer", 1, 1))  # oldest, lowest priority
    q.enqueue(LLMJob("explainer", 1, 2))
    q.enqueue(LLMJob("judge", 1, 3))
    q.enqueue(LLMJob("judge", 1, 4))  # full → evict oldest lowest (explainer item 1)
    assert drops == ["queue_full"]
    seen: list[int] = []
    while q.drain_once():
        pass

    def collect(job: LLMJob) -> None:
        seen.append(job.item_id)

    q2 = LlmQueue(collect, maxsize=3)
    q2.enqueue(LLMJob("explainer", 1, 1))
    q2.enqueue(LLMJob("explainer", 1, 2))
    q2.enqueue(LLMJob("judge", 1, 3))
    q2.enqueue(LLMJob("judge", 1, 4))
    while q2.drain_once():
        pass
    assert 1 not in seen  # oldest lowest-priority job was dropped
    assert set(seen) == {2, 3, 4}


def test_overflow_drops_incoming_when_it_is_the_worst() -> None:
    # A full queue of urgent jobs must not evict one of them to admit a less-urgent
    # newcomer — the incoming job is dropped instead.
    drops: list[str] = []
    seen: list[int] = []
    q = LlmQueue(lambda job: seen.append(job.item_id), maxsize=2, on_drop=drops.append)
    q.enqueue(LLMJob("judge", 1, 1))
    q.enqueue(LLMJob("judge", 1, 2))
    q.enqueue(LLMJob("explainer", 1, 3))  # full + least urgent → drop the newcomer
    assert drops == ["queue_full"]
    while q.drain_once():
        pass
    assert set(seen) == {1, 2}  # the two urgent jobs kept; item 3 rejected


def test_stop_does_not_drain_backlog() -> None:
    # On stop the worker returns None immediately even with queued jobs — only the
    # in-flight job finishes; the backlog is abandoned (best-effort, §1.5).
    q = LlmQueue(lambda job: None, maxsize=10)
    q.enqueue(LLMJob("judge", 1, 1))
    q.enqueue(LLMJob("judge", 1, 2))
    q._stopping = True
    assert q._pop_blocking() is None
    assert q.qsize() == 2  # backlog left untouched, not drained


def test_single_worker_thread_processes_async() -> None:
    processed = threading.Event()
    started = threading.Event()

    def slow(job: LLMJob) -> None:
        started.set()
        time.sleep(0.05)
        processed.set()

    q = LlmQueue(slow, maxsize=10)
    q.start()
    try:
        t0 = time.monotonic()
        q.enqueue(LLMJob("judge", 1, 1))
        # enqueue returns immediately (async), does not block on the 50 ms job.
        assert time.monotonic() - t0 < 0.02
        assert processed.wait(timeout=2.0)
    finally:
        q.stop()


def test_worker_survives_a_crashing_job() -> None:
    done = threading.Event()

    def handler(job: LLMJob) -> None:
        if job.item_id == 1:
            raise RuntimeError("boom")
        done.set()

    q = LlmQueue(handler, maxsize=10)
    q.start()
    try:
        q.enqueue(LLMJob("judge", 1, 1))  # crashes
        q.enqueue(LLMJob("judge", 1, 2))  # must still run
        assert done.wait(timeout=2.0)
    finally:
        q.stop()
