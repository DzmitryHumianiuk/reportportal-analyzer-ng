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

    def observe_request(self, routing_key: str, outcome: str, seconds: float) -> None:
        """Record one processed request (satisfies ``dispatcher.MetricsSink``)."""
        self.requests_total.labels(routing_key=routing_key, outcome=outcome).inc()
        self.request_seconds.labels(routing_key=routing_key).observe(seconds)

    def render(self) -> tuple[bytes, str]:
        """Return ``(body, content_type)`` for the ``/metrics`` response."""
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
