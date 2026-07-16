"""Testcontainers fixtures for integration tests.

Each fixture starts a real, throwaway Docker container for the session and tears
it down afterwards. These are the same backing services described in
``docker-compose.dev.yml`` but managed by the test run so CI needs no standing
infrastructure.

All tests in ``tests/integration`` are auto-marked ``integration`` (see the
``pytest_collection_modifyitems`` hook) so CI can select them with
``pytest -m integration``.
"""

from __future__ import annotations

import os

# Disable the testcontainers "Ryuk" reaper by default. Ryuk bind-mounts the
# Docker socket, which fails on socket-based providers such as Colima
# ("operation not supported"). Our fixtures use context managers, so containers
# are stopped deterministically without the reaper. CI on a stock Docker daemon
# can re-enable it by exporting TESTCONTAINERS_RYUK_DISABLED=false. Must run
# before importing testcontainers, which reads this at import time.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pika  # noqa: E402
import pytest  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402
from testcontainers.rabbitmq import RabbitMqContainer  # noqa: E402

_INTEGRATION_DIR = Path(__file__).parent

# pgvector/pgvector:pg16 ships PostgreSQL 16 with the `vector` extension
# preinstalled (spec 02 §1.1). rabbitmq:3-management gives us AMQP + rabbitmqctl
# for the readiness probe (spec 01 §7.2 / CONTEXT §4).
POSTGRES_IMAGE = "pgvector/pgvector:pg16"
RABBITMQ_IMAGE = "rabbitmq:3-management"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark tests living under tests/integration/ as ``integration``.

    This hook is loaded from tests/integration/conftest.py but pytest hands it
    the whole session's item list, so it must filter by path rather than mark
    everything (that would sweep up the unit suite too).
    """
    integration_marker = pytest.mark.integration
    for item in items:
        item_path = Path(str(item.fspath))
        if _INTEGRATION_DIR in item_path.parents:
            item.add_marker(integration_marker)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """A running PostgreSQL 16 + pgvector container."""
    container = PostgresContainer(
        image=POSTGRES_IMAGE,
        username="analyzer",
        password="analyzer",
        dbname="analyzer",
    )
    with container:
        yield container


@pytest.fixture
def postgres_dsn(postgres_container: PostgresContainer) -> str:
    """A libpq-style DSN (psycopg3-compatible, no SQLAlchemy driver suffix)."""
    return postgres_container.get_connection_url(driver=None)


@pytest.fixture(scope="session")
def rabbitmq_container() -> Iterator[RabbitMqContainer]:
    """A running RabbitMQ 3 (management) container."""
    container = RabbitMqContainer(
        image=RABBITMQ_IMAGE,
        username="analyzer",
        password="analyzer",
        vhost="analyzer",
    )
    with container:
        yield container


@pytest.fixture
def rabbitmq_params(
    rabbitmq_container: RabbitMqContainer,
) -> pika.ConnectionParameters:
    """pika connection parameters pointing at the running broker."""
    return rabbitmq_container.get_connection_params()
