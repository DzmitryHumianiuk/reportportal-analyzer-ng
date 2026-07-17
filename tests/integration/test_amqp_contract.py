"""AMQP contract integration tests (spec 01 §10, T1.2 acceptance subset).

Runs the real :class:`AnalyzerService` against dockerized RabbitMQ + pgvector
(testcontainers) and verifies:

* the capability exchange is visible via the management HTTP API with the exact
  advertised arguments (how the RP backend discovers analyzers, §3.2);
* both durable queues and the DLQ exist;
* an RPC round-trip (``noop_echo``, ``index``) replies on the caller's
  ``reply_to`` with the echoed ``correlation_id`` and ``application/json``;
* malformed JSON is dead-lettered and the consumer keeps running;
* the health endpoints report ``amqp`` + ``pg`` status and ``/metrics`` exposes
  the ``analyzer_*`` series.
"""

from __future__ import annotations

import contextlib
import json
import socket
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pika
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer

from analyzer_ng.api.http import HttpServer
from analyzer_ng.config import AppConfig
from analyzer_ng.db.pool import open_pool
from analyzer_ng.db.startup import bootstrap_and_migrate_or_exit
from analyzer_ng.service import AnalyzerService

EXCHANGE = "analyzer"
APP_VERSION = "0.1.0-test"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _amqp_url(container: RabbitMqContainer) -> str:
    params = container.get_connection_params()
    creds = params.credentials
    return f"amqp://{creds.username}:{creds.password}@{params.host}:{params.port}"


@dataclass
class RunningService:
    params: pika.ConnectionParameters
    http_base: str
    mgmt_base: str
    service: AnalyzerService


@pytest.fixture(scope="module")
def running_service(
    postgres_container: PostgresContainer,
    rabbitmq_container: RabbitMqContainer,
) -> Iterator[RunningService]:
    dsn = postgres_container.get_connection_url(driver=None)
    config = AppConfig(
        amqp_url=_amqp_url(rabbitmq_container),
        amqp_virtual_host="analyzer",
        amqp_exchange_name=EXCHANGE,
        analyzer_pg_dsn=dsn,
        analyzer_pg_create_db=False,
        analyzer_ng_workers=2,
    )
    bootstrap_and_migrate_or_exit(dsn, schema=config.analyzer_pg_schema, create_db=False)
    pool = open_pool(config)

    service = AnalyzerService(config, APP_VERSION, pg_pool=pool)
    http_port = _free_port()
    http = HttpServer(service, port=http_port, host="127.0.0.1")
    http.start()
    service.start()

    # Wait for both consumers to be actively consuming.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not service.amqp_ok():
        time.sleep(0.1)
    assert service.amqp_ok(), "consumers did not start"

    host = rabbitmq_container.get_container_host_ip()
    mgmt_port = rabbitmq_container.get_exposed_port(15672)
    ctx = RunningService(
        params=rabbitmq_container.get_connection_params(),
        http_base=f"http://127.0.0.1:{http_port}",
        mgmt_base=f"http://{host}:{mgmt_port}",
        service=service,
    )
    try:
        yield ctx
    finally:
        service.shutdown(drain_timeout=5)
        http.shutdown()
        pool.close()


# --------------------------------------------------------------------------- #
# RPC helper.
# --------------------------------------------------------------------------- #
def _rpc(
    params: pika.ConnectionParameters,
    routing_key: str,
    body: bytes,
    *,
    timeout: float = 20.0,
) -> tuple[bytes, pika.BasicProperties] | None:
    """Publish ``body`` to the exchange with a reply queue; return (reply, props)."""
    connection = pika.BlockingConnection(params)
    try:
        channel = connection.channel()
        reply_queue = channel.queue_declare(queue="", exclusive=True).method.queue
        correlation_id = str(uuid.uuid4())
        channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=routing_key,
            properties=pika.BasicProperties(reply_to=reply_queue, correlation_id=correlation_id),
            body=body,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            method, props, reply = channel.basic_get(queue=reply_queue, auto_ack=True)
            if method is not None:
                assert props.correlation_id == correlation_id
                return reply, props
            connection.process_data_events(time_limit=0.2)
        return None
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #
def test_exchange_visible_with_capability_args(running_service: RunningService) -> None:
    resp = httpx.get(
        f"{running_service.mgmt_base}/api/exchanges/analyzer/{EXCHANGE}",
        auth=("analyzer", "analyzer"),
        timeout=10,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "fanout"
    assert data["durable"] is False
    assert data["auto_delete"] is True
    args = data["arguments"]
    assert args["analyzer"] == EXCHANGE
    assert args["analyzer_index"] is True
    assert args["analyzer_priority"] == 1
    assert args["analyzer_log_search"] is True
    assert args["analyzer_suggest"] is True
    assert args["analyzer_cluster"] is True
    assert args["version"] == APP_VERSION


def test_queues_exist(running_service: RunningService) -> None:
    for name in ("analyzer-ng.all", "analyzer-ng.train", "analyzer-ng.dlq"):
        resp = httpx.get(
            f"{running_service.mgmt_base}/api/queues/analyzer/{name}",
            auth=("analyzer", "analyzer"),
            timeout=10,
        )
        assert resp.status_code == 200, name
        assert resp.json()["durable"] is True


def test_noop_echo_round_trip(running_service: RunningService) -> None:
    result = _rpc(running_service.params, "noop_echo", json.dumps("ping").encode())
    assert result is not None, "no reply received"
    reply, props = result
    assert reply.decode() == "ping"
    assert props.content_type == "application/json"


def test_index_rpc_returns_bulk_response(running_service: RunningService) -> None:
    launch = {"launchId": 1, "project": 2, "testItems": []}
    result = _rpc(running_service.params, "index", json.dumps([launch]).encode())
    assert result is not None
    reply, _ = result
    parsed = json.loads(reply)
    assert set(parsed) >= {"took", "errors", "items", "logResults", "status"}


def test_analyze_rpc_returns_json_array(running_service: RunningService) -> None:
    launch = {"launchId": 1, "project": 2, "testItems": []}
    result = _rpc(running_service.params, "analyze", json.dumps([launch]).encode())
    assert result is not None
    reply, _ = result
    assert json.loads(reply) == []


def test_deprecated_update_suggest_info_replies_one(running_service: RunningService) -> None:
    result = _rpc(running_service.params, "update_suggest_info", json.dumps({"x": 1}).encode())
    assert result is not None
    reply, _ = result
    assert reply.decode() == "1"


def test_malformed_json_dead_lettered_and_consumer_survives(
    running_service: RunningService,
) -> None:
    # Publish a malformed body (no reply_to: it is nacked, not answered).
    connection = pika.BlockingConnection(running_service.params)
    try:
        channel = connection.channel()
        channel.basic_publish(exchange=EXCHANGE, routing_key="noop_echo", body=b"{not valid json")

        # The bad message lands in the DLQ verbatim with post-mortem headers.
        deadline = time.monotonic() + 20
        dlq_message = None
        while time.monotonic() < deadline:
            method, props, dead = channel.basic_get(queue="analyzer-ng.dlq", auto_ack=True)
            if method is not None:
                dlq_message = (props, dead)
                break
            connection.process_data_events(time_limit=0.2)
        assert dlq_message is not None, "malformed message was not dead-lettered"
        props, dead = dlq_message
        assert dead == b"{not valid json"
        assert props.headers["x-routing-key"] == "noop_echo"
    finally:
        with contextlib.suppress(Exception):
            connection.close()

    # The consumer survived: a subsequent valid RPC still round-trips.
    result = _rpc(running_service.params, "noop_echo", json.dumps("alive").encode())
    assert result is not None
    assert result[0].decode() == "alive"


def test_health_endpoints_report_amqp_and_pg(running_service: RunningService) -> None:
    health = httpx.get(f"{running_service.http_base}/health", timeout=10)
    assert health.status_code == 200
    body = health.json()
    assert body["live"] is True
    assert body["ready"] is True
    assert body["pg"] is True
    assert body["amqp"] is True
    assert body["version"] == APP_VERSION

    root = httpx.get(f"{running_service.http_base}/", timeout=10)
    assert root.status_code == 200
    assert root.json()["status"] == "healthy"

    metrics = httpx.get(f"{running_service.http_base}/metrics", timeout=10)
    assert metrics.status_code == 200
    text = metrics.text
    for series in (
        "analyzer_requests_total",
        "analyzer_request_seconds",
        "analyzer_queue_depth",
        "analyzer_pg_pool_in_use",
    ):
        assert series in text
