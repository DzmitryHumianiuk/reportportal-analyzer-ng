"""Bounded single-worker LLM job queue (spec 04 §1.5).

An in-memory priority queue drained by exactly one worker thread (Ollama
serializes at ``NUM_PARALLEL=1``, so one worker avoids client-side queuing
distortion). Priorities: judge=0 > extractor=1 = coldstart=1 > explainer=2, FIFO
within a priority. Bounded at ``ANALYZER_LLM_QUEUE_MAX``; on overflow the **oldest
lowest-priority** job is dropped (counter ``analyzer_llm_dropped_total``). Jobs are
best-effort and lost on restart — nothing correctness-critical rides on the queue.
"""

from __future__ import annotations

import heapq
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# §1.5 static role priorities (lower = more urgent). The abstain explainer is UX
# enrichment like the match explainer, so it shares the least-urgent tier.
ROLE_PRIORITY = {
    "judge": 0,
    "extractor": 1,
    "coldstart": 1,
    "explainer": 2,
    "abstain_explainer": 2,
}


@dataclass
class LLMJob:
    role: str
    project_id: int
    item_id: int
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def priority(self) -> int:
        return ROLE_PRIORITY[self.role]


class LlmQueue:
    """A bounded priority queue with a single dedicated worker thread."""

    def __init__(
        self,
        process: Callable[[LLMJob], None],
        *,
        maxsize: int = 500,
        on_drop: Callable[[str], None] | None = None,
        thread_name: str = "llm-worker",
    ) -> None:
        self._process = process
        self._maxsize = maxsize
        self._on_drop = on_drop
        self._thread_name = thread_name
        self._heap: list[tuple[int, int, LLMJob]] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stopping = False
        self._thread: threading.Thread | None = None

    # -- production ------------------------------------------------------- #
    def enqueue(self, job: LLMJob) -> None:
        with self._cond:
            if len(self._heap) >= self._maxsize and self._reject_or_evict_locked(job):
                return  # incoming job was itself the worst candidate → dropped
            heapq.heappush(self._heap, (job.priority, self._seq, job))
            self._seq += 1
            self._cond.notify()

    def _reject_or_evict_locked(self, incoming: LLMJob) -> bool:
        """Make room for ``incoming`` by dropping the worst (oldest, least-urgent)
        job — or drop ``incoming`` itself when it is no better than that candidate.

        The eviction candidate is the oldest job among the **numerically-highest**
        priority value (= least urgent, since lower = more urgent, §1.5). If the
        incoming job is even less urgent (higher priority number), or equally urgent
        but newer, evicting a queued job to admit it would be strictly worse — so the
        incoming job is dropped instead. Either way one drop is counted. Returns True
        iff the incoming job was rejected.
        """
        worst_priority = max(entry[0] for entry in self._heap)
        # Oldest = smallest insertion sequence among that least-urgent band.
        idx = min(
            (i for i, entry in enumerate(self._heap) if entry[0] == worst_priority),
            key=lambda i: self._heap[i][1],
        )
        if self._on_drop is not None:
            self._on_drop("queue_full")
        # Incoming is the worst candidate: it is less urgent than the eviction
        # candidate, so keep the queue as-is and drop the newcomer.
        if incoming.priority > worst_priority:
            return True
        self._heap[idx] = self._heap[-1]
        self._heap.pop()
        heapq.heapify(self._heap)
        return False

    def qsize(self) -> int:
        with self._lock:
            return len(self._heap)

    # -- consumption ------------------------------------------------------ #
    def _pop_blocking(self) -> LLMJob | None:
        with self._cond:
            while not self._heap and not self._stopping:
                self._cond.wait()
            # On stop, return None immediately even with a non-empty backlog: jobs
            # are best-effort and lost on restart (§1.5), so we finish only the
            # in-flight job and abandon the queue rather than block shutdown draining
            # up to ANALYZER_LLM_QUEUE_MAX pending jobs.
            if self._stopping or not self._heap:
                return None
            return heapq.heappop(self._heap)[2]

    def drain_once(self) -> bool:
        """Process one job without blocking (test hook). Returns False if empty."""
        with self._lock:
            if not self._heap:
                return False
            job = heapq.heappop(self._heap)[2]
        self._safe_process(job)
        return True

    def _safe_process(self, job: LLMJob) -> None:
        try:
            self._process(job)
        except Exception:  # noqa: BLE001 — a role failure must never kill the worker
            logger.exception("LLM job crashed (role=%s item=%s)", job.role, job.item_id)

    def _run(self) -> None:
        while True:
            job = self._pop_blocking()
            if job is None:  # stopping and drained
                return
            self._safe_process(job)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._thread_name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
