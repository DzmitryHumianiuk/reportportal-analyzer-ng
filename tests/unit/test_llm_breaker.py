"""Circuit-breaker tests (spec 04 §1.4)."""

from __future__ import annotations

from analyzer_ng.llm.breaker import BreakerState, CircuitBreaker


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_opens_after_three_consecutive_failures() -> None:
    b = CircuitBreaker(clock=FakeClock())
    assert b.state is BreakerState.CLOSED
    b.record_failure()
    b.record_failure()
    assert b.allow() is True
    b.record_failure()
    assert b.state is BreakerState.OPEN
    assert b.allow() is False  # jobs complete immediately as breaker_open


def test_success_resets_failure_streak() -> None:
    b = CircuitBreaker(clock=FakeClock())
    b.record_failure()
    b.record_failure()
    b.record_success()
    b.record_failure()
    b.record_failure()
    assert b.state is BreakerState.CLOSED  # streak was reset


def test_half_open_after_cooldown_then_close_on_success() -> None:
    clock = FakeClock()
    b = CircuitBreaker(clock=clock, base_cooldown_s=60.0)
    for _ in range(3):
        b.record_failure()
    assert b.allow() is False
    clock.advance(60.0)
    assert b.allow() is True  # transitions to half-open, probe permitted
    assert b.state is BreakerState.HALF_OPEN
    b.record_success()
    assert b.state is BreakerState.CLOSED


def test_half_open_failure_reopens_with_doubled_backoff() -> None:
    clock = FakeClock()
    b = CircuitBreaker(clock=clock, base_cooldown_s=60.0)
    for _ in range(3):
        b.record_failure()
    clock.advance(60.0)
    assert b.allow() is True  # half-open probe
    b.record_failure()  # probe fails
    assert b.state is BreakerState.OPEN
    clock.advance(60.0)
    assert b.allow() is False  # cooldown doubled to 120s
    clock.advance(60.0)
    assert b.allow() is True


def test_schema_failures_do_not_count_toward_breaker() -> None:
    b = CircuitBreaker(clock=FakeClock())
    for _ in range(10):
        b.record_response()  # server up, body bad — must not open
    assert b.state is BreakerState.CLOSED


def test_state_change_callback_fires() -> None:
    seen: list[tuple[str, str]] = []
    b = CircuitBreaker(
        clock=FakeClock(),
        on_state_change=lambda o, n: seen.append((o.value, n.value)),
    )
    for _ in range(3):
        b.record_failure()
    assert ("closed", "open") in seen
