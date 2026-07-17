"""Circuit breaker wrapping every Ollama HTTP call (spec 04 §1.4).

- Closed → Open on 3 consecutive transport failures (timeout, 5xx, connect
  error, malformed body). While open, jobs complete immediately as
  ``breaker_open`` — no per-item error logs, one WARNING on the state change.
- Open → Half-open after a cooldown (60 s, doubling per re-open, cap 15 min):
  the next allowed job is the probe; success closes, failure re-opens.
- Schema/validation failures do **not** count — the sidecar is up, the output is
  bad (that is the per-role retry/drop policy). Callers signal those via
  :meth:`record_response` (server answered) rather than :meth:`record_failure`.

Thread-safe: the single LLM worker plus the periodic probe may touch it.
"""

from __future__ import annotations

import enum
import threading
import time
from collections.abc import Callable


class BreakerState(enum.StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        fail_threshold: int = 3,
        base_cooldown_s: float = 60.0,
        max_cooldown_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        on_state_change: Callable[[BreakerState, BreakerState], None] | None = None,
    ) -> None:
        self._fail_threshold = fail_threshold
        self._base_cooldown = base_cooldown_s
        self._max_cooldown = max_cooldown_s
        self._clock = clock
        self._on_state_change = on_state_change
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._cooldown = base_cooldown_s
        self._opened_at = 0.0

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state

    def _transition(self, new: BreakerState) -> None:
        old = self._state
        if old is new:
            return
        self._state = new
        if self._on_state_change is not None:
            self._on_state_change(old, new)

    def allow(self) -> bool:
        """True if a call may proceed now. Transitions OPEN → HALF_OPEN once the
        cooldown has elapsed (the caller's next call becomes the probe)."""
        with self._lock:
            if self._state is BreakerState.OPEN:
                if self._clock() - self._opened_at >= self._cooldown:
                    self._transition(BreakerState.HALF_OPEN)
                    return True
                return False
            # CLOSED or HALF_OPEN both permit a call.
            return True

    def record_success(self) -> None:
        """A well-formed, breaker-healthy response — reset and close."""
        with self._lock:
            self._consecutive_failures = 0
            self._cooldown = self._base_cooldown
            self._transition(BreakerState.CLOSED)

    def record_response(self) -> None:
        """The server answered (even if the body failed schema/post-validation).

        Treated as breaker-healthy: it clears the failure streak and closes a
        half-open probe, but schema failures still follow the role retry/drop
        path outside the breaker."""
        self.record_success()

    def record_failure(self) -> None:
        """A transport-level failure (timeout, 5xx, connect error, malformed body)."""
        with self._lock:
            if self._state is BreakerState.HALF_OPEN:
                # Probe failed → re-open with doubled backoff (capped).
                self._cooldown = min(self._cooldown * 2, self._max_cooldown)
                self._opened_at = self._clock()
                self._transition(BreakerState.OPEN)
                return
            self._consecutive_failures += 1
            if (
                self._state is BreakerState.CLOSED
                and self._consecutive_failures >= self._fail_threshold
            ):
                self._cooldown = self._base_cooldown
                self._opened_at = self._clock()
                self._transition(BreakerState.OPEN)
