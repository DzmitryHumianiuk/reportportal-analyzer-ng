"""G1 verification gate — RP-compatibility integration suite (MASTER_PLAN G1).

Proves Phase 1 is a faithful drop-in AMQP shell for the legacy ReportPortal
``analyzer``. Every test runs the fully assembled :class:`AnalyzerService` over
real RabbitMQ + pgvector (testcontainers) and asserts wire-level compatibility:

* **RP-compat round-trips** — sample ``index`` / ``analyze`` / ``suggest`` /
  ``delete`` / ``defect_update`` payloads adapted from the legacy repo's
  ``test_res`` fixtures round-trip through the real service over real AMQP and
  produce **schema-valid** replies in the legacy wire format. Phase 1 handlers
  are stubs, so the replies are spec-correct empty/no-op values — this suite
  validates *structure and types*, never analysis content.
* **406 redeclare fallback** (spec 01 §3.2) — a conflicting exchange declared
  before the service starts forces the ``PRECONDITION_FAILED - inequivalent arg
  'type'`` path; the service must delete + re-declare and end healthy with the
  exact capability arguments.
* **Cold-start end-to-end** (deferred G0 clause) — from an empty PostgreSQL and a
  fresh broker the assembled service creates the DB, runs migrations, and
  ``/health`` reports ``pg`` + ``amqp`` healthy.

The advertised-exchange-args acceptance (spec 01 §10.2) is already covered by
``test_amqp_contract.py::test_exchange_visible_with_capability_args`` for the
production ``analyzer`` exchange; the 406 test below re-verifies the exact args
land after the redeclare, but the plain "args are correct" case is not
duplicated here. Each service in this module uses its own exchange name + queue
prefix so the tests stay isolated and order-independent on the shared broker.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pika
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer

from analyzer_ng.amqp.models import (
    AnalysisResult,
    BulkResponse,
    SuggestAnalysisResult,
)
from analyzer_ng.api.http import HttpServer
from analyzer_ng.config import AppConfig
from analyzer_ng.db.pool import open_pool
from analyzer_ng.db.startup import bootstrap_and_migrate, bootstrap_and_migrate_or_exit
from analyzer_ng.service import AnalyzerService

APP_VERSION = "0.1.0-g1"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _load_fixture(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _amqp_url(container: RabbitMqContainer) -> str:
    params = container.get_connection_params()
    creds = params.credentials
    return f"amqp://{creds.username}:{creds.password}@{params.host}:{params.port}"


def _mgmt_base(container: RabbitMqContainer) -> str:
    host = container.get_container_host_ip()
    port = container.get_exposed_port(15672)
    return f"http://{host}:{port}"


def _force_ipv4(params: dict[str, str]) -> dict[str, str]:
    # testcontainers hands back host=localhost (both 127.0.0.1 and ::1); the PG
    # container listens on IPv4 only. Pin to 127.0.0.1 so missing-DB detection is
    # unambiguous (mirrors tests/integration/test_db_migrations.py).
    if params.get("host") in (None, "localhost"):
        params["host"] = "127.0.0.1"
    return params


def _dsn_for_db(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    params["dbname"] = dbname
    return make_conninfo(**_force_ipv4(params))


def _drop_db(base_dsn: str, dbname: str) -> None:
    maintenance = _dsn_for_db(base_dsn, "postgres")
    with psycopg.connect(maintenance, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
        )


@dataclass
class ServiceHandle:
    params: pika.ConnectionParameters
    http_base: str
    mgmt_base: str
    exchange: str
    service: AnalyzerService


@contextmanager
def _assembled_service(
    *,
    dsn: str,
    rabbitmq_container: RabbitMqContainer,
    exchange: str,
    queue_prefix: str,
    create_db: bool = False,
) -> Iterator[ServiceHandle]:
    """Bootstrap the DB, assemble the real service, start it, yield a handle.

    Uses a distinct ``exchange`` + ``queue_prefix`` per caller so concurrent
    services on the shared broker never steal each other's messages.
    """
    config = AppConfig(
        amqp_url=_amqp_url(rabbitmq_container),
        amqp_virtual_host="analyzer",
        amqp_exchange_name=exchange,
        analyzer_ng_queue_prefix=queue_prefix,
        analyzer_pg_dsn=dsn,
        analyzer_pg_create_db=create_db,
        analyzer_ng_workers=2,
    )
    bootstrap_and_migrate_or_exit(dsn, schema=config.analyzer_pg_schema, create_db=create_db)
    pool = open_pool(config)
    service = AnalyzerService(config, APP_VERSION, pg_pool=pool)
    http_port = _free_port()
    http = HttpServer(service, port=http_port, host="127.0.0.1")
    http.start()
    service.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not service.amqp_ok():
            time.sleep(0.1)
        assert service.amqp_ok(), "consumers did not start"
        yield ServiceHandle(
            params=rabbitmq_container.get_connection_params(),
            http_base=f"http://127.0.0.1:{http_port}",
            mgmt_base=_mgmt_base(rabbitmq_container),
            exchange=exchange,
            service=service,
        )
    finally:
        service.shutdown(drain_timeout=5)
        http.shutdown()
        pool.close()


def _rpc(
    params: pika.ConnectionParameters,
    exchange: str,
    routing_key: str,
    body: bytes,
    *,
    timeout: float = 20.0,
) -> tuple[bytes, pika.BasicProperties]:
    """Publish ``body`` with a private reply queue; return (reply, props).

    Fails the test if no reply arrives within ``timeout`` (an RPC route must
    always answer — spec 01 §3.4).
    """
    connection = pika.BlockingConnection(params)
    try:
        channel = connection.channel()
        reply_queue = channel.queue_declare(queue="", exclusive=True).method.queue
        correlation_id = str(uuid.uuid4())
        channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            properties=pika.BasicProperties(
                reply_to=reply_queue, correlation_id=correlation_id
            ),
            body=body,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            method, props, reply = channel.basic_get(queue=reply_queue, auto_ack=True)
            if method is not None:
                assert props.correlation_id == correlation_id
                assert props.content_type == "application/json"
                return reply, props
            connection.process_data_events(time_limit=0.2)
        raise AssertionError(f"no reply for routing_key={routing_key!r} within {timeout}s")
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Fixtures.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def rp_service(
    postgres_container: PostgresContainer,
    rabbitmq_container: RabbitMqContainer,
) -> Iterator[ServiceHandle]:
    """A running service on an isolated exchange for the round-trip tests."""
    dsn = postgres_container.get_connection_url(driver=None)
    with _assembled_service(
        dsn=dsn,
        rabbitmq_container=rabbitmq_container,
        exchange="analyzer_rpcompat",
        queue_prefix="rpcompat.",
    ) as handle:
        yield handle


# --------------------------------------------------------------------------- #
# 1. RP-compat round-trips with legacy-derived payloads.
# --------------------------------------------------------------------------- #
def test_index_round_trips_to_schema_valid_bulk_response(rp_service: ServiceHandle) -> None:
    launches = _load_fixture("rp_index_launches.json")
    reply, _ = _rpc(
        rp_service.params, rp_service.exchange, "index", json.dumps(launches).encode()
    )
    parsed = json.loads(reply)
    # Legacy wire shape: a single BulkResponse object (model_dump_json).
    assert isinstance(parsed, dict)
    result = BulkResponse.model_validate(parsed)
    assert isinstance(result.took, int)
    assert isinstance(result.errors, bool)
    assert isinstance(result.items, list)
    assert isinstance(result.logResults, list)
    assert isinstance(result.status, int)


def test_analyze_round_trips_to_json_array_of_results(rp_service: ServiceHandle) -> None:
    launches = _load_fixture("rp_index_launches.json")
    reply, _ = _rpc(
        rp_service.params, rp_service.exchange, "analyze", json.dumps(launches).encode()
    )
    parsed = json.loads(reply)
    # Legacy wire shape: a JSON array of AnalysisResult dumps. Phase 1 abstains on
    # everything (stub) so the array is empty, but every element that IS present
    # must validate against the model and carry exactly the three legacy fields.
    assert isinstance(parsed, list)
    for element in parsed:
        result = AnalysisResult.model_validate(element)
        assert set(element) == {"testItem", "issueType", "relevantItem"}
        assert isinstance(result.testItem, int)
        assert isinstance(result.relevantItem, int)


def test_suggest_round_trips_to_json_array_of_suggestions(rp_service: ServiceHandle) -> None:
    info = _load_fixture("rp_suggest_test_item_info.json")
    reply, _ = _rpc(
        rp_service.params, rp_service.exchange, "suggest", json.dumps(info).encode()
    )
    parsed = json.loads(reply)
    # Legacy wire shape: a JSON array of SuggestAnalysisResult dumps (empty in
    # Phase 1). Any present element must expose ALL UI-required legacy fields.
    assert isinstance(parsed, list)
    for element in parsed:
        SuggestAnalysisResult.model_validate(element)
        assert {
            "esScore",
            "esPosition",
            "modelInfo",
            "usedLogLines",
            "minShouldMatch",
            "processedTime",
            "methodName",
        } <= set(element)


def test_delete_round_trips_to_stringified_count(rp_service: ServiceHandle) -> None:
    project = _load_fixture("rp_delete_project.json")  # raw JSON number (project id)
    reply, _ = _rpc(
        rp_service.params, rp_service.exchange, "delete", json.dumps(project).encode()
    )
    # Legacy wire shape: a plain stringified int (count of affected entities).
    text = reply.decode()
    assert text == str(int(text))  # a base-10 integer literal, no envelope
    assert int(text) >= 0  # real pipeline: count of entities actually removed


def test_defect_update_round_trips_to_json_int_list(rp_service: ServiceHandle) -> None:
    payload = _load_fixture("rp_defect_update.json")
    reply, _ = _rpc(
        rp_service.params, rp_service.exchange, "defect_update", json.dumps(payload).encode()
    )
    parsed = json.loads(reply)
    # Legacy wire shape: json.dumps(list_of_ints) — the ids NOT found/updated.
    # With the real feedback pipeline the not-found set depends on whether the
    # items were indexed/deleted by earlier round-trips in this module, so assert
    # the wire shape (a JSON int array) and that it only ever names the referenced
    # ids. Behavioral proof (events appended, labels overwritten) lives in
    # tests/integration/test_index_pipeline.py.
    assert isinstance(parsed, list)
    assert all(isinstance(x, int) for x in parsed)
    assert set(parsed) <= {2001, 2002}


# --------------------------------------------------------------------------- #
# 3. 406 redeclare fallback (spec 01 §3.2).
# --------------------------------------------------------------------------- #
def _predeclare_conflicting_exchange(
    params: pika.ConnectionParameters, exchange: str
) -> None:
    """Declare ``exchange`` with a conflicting type BEFORE the service starts.

    Only the ``type`` differs from the service's declaration (durable/auto_delete
    are deliberately left to persist the exchange) so the broker raises exactly
    ``PRECONDITION_FAILED - inequivalent arg 'type'`` — the case §3.2 handles.
    """
    connection = pika.BlockingConnection(params)
    try:
        channel = connection.channel()
        channel.exchange_declare(
            exchange=exchange,
            exchange_type="direct",  # service wants "fanout"
            durable=True,  # persist so the conflict is guaranteed at service start
            auto_delete=False,
        )
    finally:
        connection.close()


def test_406_conflicting_exchange_is_deleted_and_redeclared(
    postgres_container: PostgresContainer,
    rabbitmq_container: RabbitMqContainer,
) -> None:
    exchange = "analyzer_g1_406"
    params = rabbitmq_container.get_connection_params()

    # A leftover legacy exchange of the wrong type exists before we start.
    _predeclare_conflicting_exchange(params, exchange)
    pre = httpx.get(
        f"{_mgmt_base(rabbitmq_container)}/api/exchanges/analyzer/{exchange}",
        auth=("analyzer", "analyzer"),
        timeout=10,
    )
    assert pre.status_code == 200
    assert pre.json()["type"] == "direct", "precondition: conflicting exchange must exist"

    dsn = postgres_container.get_connection_url(driver=None)
    with _assembled_service(
        dsn=dsn,
        rabbitmq_container=rabbitmq_container,
        exchange=exchange,
        queue_prefix="g1-406.",
    ) as handle:
        # The service hit the 406, deleted the old exchange, and re-declared it.
        assert handle.service.amqp_ok()

        resp = httpx.get(
            f"{handle.mgmt_base}/api/exchanges/analyzer/{exchange}",
            auth=("analyzer", "analyzer"),
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "fanout"
        assert data["durable"] is False
        assert data["auto_delete"] is True
        args = data["arguments"]
        assert args["analyzer"] == exchange
        assert args["analyzer_index"] is True
        assert args["analyzer_priority"] == 1
        assert args["analyzer_log_search"] is True
        assert args["analyzer_suggest"] is True
        assert args["analyzer_cluster"] is True
        assert args["version"] == APP_VERSION

        # And the healthy service still round-trips a message end-to-end.
        reply, _ = _rpc(handle.params, exchange, "noop_echo", json.dumps("after-406").encode())
        assert reply.decode() == "after-406"


# --------------------------------------------------------------------------- #
# 4. Deferred G0 clause: cold empty PG + fresh Rabbit → migrate → healthy.
# --------------------------------------------------------------------------- #
def test_cold_start_migrates_and_reports_healthy(
    postgres_container: PostgresContainer,
    rabbitmq_container: RabbitMqContainer,
) -> None:
    base_dsn = postgres_container.get_connection_url(driver=None)
    dbname = f"anz_g0_{uuid4().hex[:10]}"
    fresh_dsn = _dsn_for_db(base_dsn, dbname)
    try:
        # Sanity: the database really does not exist yet (truly cold).
        maintenance = _dsn_for_db(base_dsn, "postgres")
        with psycopg.connect(maintenance, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
            ).fetchone()
        assert exists is None, "precondition: database must not exist yet"

        # The assembled service creates the DB, runs migrations, and starts.
        with _assembled_service(
            dsn=fresh_dsn,
            rabbitmq_container=rabbitmq_container,
            exchange="analyzer_g0",
            queue_prefix="g0.",
            create_db=True,
        ) as handle:
            # Migrations actually ran on the freshly created database.
            with psycopg.connect(fresh_dsn) as conn:
                applied = conn.execute(
                    "SELECT count(*) FROM analyzer.schema_migrations"
                ).fetchone()[0]
            assert applied >= 1, "no migrations recorded on the cold database"

            # /health reports amqp + pg healthy and ready (HTTP 200).
            health = httpx.get(f"{handle.http_base}/health", timeout=10)
            assert health.status_code == 200
            body = health.json()
            assert body["live"] is True
            assert body["ready"] is True
            assert body["pg"] is True
            assert body["amqp"] is True
            assert body["version"] == APP_VERSION

            # Legacy-compatible root endpoint is healthy too.
            root = httpx.get(f"{handle.http_base}/", timeout=10)
            assert root.status_code == 200
            assert root.json()["status"] == "healthy"
    finally:
        _drop_db(base_dsn, dbname)


def test_bootstrap_and_migrate_on_cold_db_applies_initial_schema(
    postgres_container: PostgresContainer,
) -> None:
    """Focused check that a cold DB gets the initial migration applied exactly once."""
    base_dsn = postgres_container.get_connection_url(driver=None)
    dbname = f"anz_g0m_{uuid4().hex[:10]}"
    fresh_dsn = _dsn_for_db(base_dsn, dbname)
    try:
        applied = bootstrap_and_migrate(fresh_dsn, create_db=True, attempts=5, delay=0.2)
        assert applied == [1, 2]
        # Idempotent restart applies nothing further.
        assert bootstrap_and_migrate(fresh_dsn, create_db=True, attempts=5, delay=0.2) == []
    finally:
        _drop_db(base_dsn, dbname)
