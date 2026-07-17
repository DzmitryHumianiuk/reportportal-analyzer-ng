"""Prometheus metrics registry (spec 01 §9.2).

Exposes the ``analyzer_*`` series consumed by ``GET /metrics``. Each instance owns
a private :class:`~prometheus_client.CollectorRegistry` so constructing more than
one (e.g. across tests) never double-registers on the global default registry.

Phase 2 wires the suggestion/abstain counters; they are declared here so the
series are exposed from day one.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


class Metrics:
    """The service's metric collectors."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests_total = Counter(
            "analyzer_requests_total",
            "Total AMQP requests processed, by routing key and outcome.",
            ["routing_key", "outcome"],
            registry=self.registry,
        )
        self.request_seconds = Histogram(
            "analyzer_request_seconds",
            "Request processing time in seconds, by routing key.",
            ["routing_key"],
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "analyzer_queue_depth",
            "Current depth of the in-memory dispatch queue.",
            registry=self.registry,
        )
        self.suggest_shown_total = Counter(
            "analyzer_suggest_shown_total",
            "Suggestions shown to users.",
            ["project"],
            registry=self.registry,
        )
        self.suggest_accepted_total = Counter(
            "analyzer_suggest_accepted_total",
            "Suggestions accepted by users.",
            ["project"],
            registry=self.registry,
        )
        self.suggest_corrected_total = Counter(
            "analyzer_suggest_corrected_total",
            "Suggestions corrected by users.",
            ["project"],
            registry=self.registry,
        )
        self.abstain_total = Counter(
            "analyzer_abstain_total",
            "Auto-analysis abstentions (below the confidence threshold).",
            ["project"],
            registry=self.registry,
        )
        self.pg_pool_in_use = Gauge(
            "analyzer_pg_pool_in_use",
            "Connections currently checked out of the PostgreSQL pool.",
            registry=self.registry,
        )
        # ---- Optional LLM sidecar (spec 04 §6.3) ---- #
        self.llm_calls_total = Counter(
            "analyzer_llm_calls_total",
            "LLM role calls by role and outcome.",
            ["role", "outcome"],
            registry=self.registry,
        )
        self.llm_latency_ms = Histogram(
            "analyzer_llm_latency_ms",
            "LLM call latency in milliseconds, by role.",
            ["role"],
            buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 20000, 40000),
            registry=self.registry,
        )
        self.llm_breaker_state = Gauge(
            "analyzer_llm_breaker_state",
            "LLM circuit-breaker state (0=closed, 1=open, 2=half_open).",
            registry=self.registry,
        )
        self.llm_breaker_open_total = Counter(
            "analyzer_llm_breaker_open_total",
            "Number of times the LLM circuit breaker transitioned to open.",
            registry=self.registry,
        )
        self.llm_dropped_total = Counter(
            "analyzer_llm_dropped_total",
            "LLM jobs dropped before execution, by reason.",
            ["reason"],
            registry=self.registry,
        )
        self.llm_role_disabled = Gauge(
            "analyzer_llm_role_disabled",
            "Number of projects with this LLM role auto-disabled by the nightly eval.",
            ["role"],
            registry=self.registry,
        )

    _BREAKER_STATE_VALUE = {"closed": 0, "open": 1, "half_open": 2}

    def observe_llm_role_disabled(self, counts: dict[str, int]) -> None:
        """Set the per-role auto-disabled-project gauge (spec 04 §6.3 admin view).

        Called by the nightly eval job with the full role→count map so a role that
        was re-enabled resets to 0 rather than sticking at its last value.
        """
        for role, count in counts.items():
            self.llm_role_disabled.labels(role=role).set(count)

    def observe_llm_call(self, role: str, outcome: str, latency_ms: int | None) -> None:
        """Record one LLM role call (satisfies the engine's metrics port)."""
        self.llm_calls_total.labels(role=role, outcome=outcome).inc()
        if latency_ms is not None:
            self.llm_latency_ms.labels(role=role).observe(latency_ms)

    def observe_llm_drop(self, reason: str) -> None:
        self.llm_dropped_total.labels(reason=reason).inc()

    def set_breaker_state(self, state: str) -> None:
        """Set the breaker gauge; count transitions into 'open'."""
        value = self._BREAKER_STATE_VALUE.get(state, 0)
        self.llm_breaker_state.set(value)
        if state == "open":
            self.llm_breaker_open_total.inc()

    def observe_request(self, routing_key: str, outcome: str, seconds: float) -> None:
        """Record one processed request (satisfies ``dispatcher.MetricsSink``)."""
        self.requests_total.labels(routing_key=routing_key, outcome=outcome).inc()
        self.request_seconds.labels(routing_key=routing_key).observe(seconds)

    def render(self) -> tuple[bytes, str]:
        """Return ``(body, content_type)`` for the ``/metrics`` response."""
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
