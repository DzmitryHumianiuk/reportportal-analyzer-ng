"""Trivial connectivity smoke tests for the dev/CI harness (task T0.2).

These prove the testcontainers fixtures bring up real Postgres+pgvector and
RabbitMQ containers and that the pinned client libraries (psycopg3, pika) can
talk to them. They are the acceptance criterion for T0.2 -- no analyzer logic
is exercised here.
"""

from __future__ import annotations

import pika
import psycopg


def test_postgres_connectivity(postgres_dsn: str) -> None:
    """psycopg3 can connect and run a trivial query."""
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 1


def test_pgvector_extension_available(postgres_dsn: str) -> None:
    """The `vector` extension can be created and used (spec 02 §1.1)."""
    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.commit()

        cur.execute("SELECT installed_version FROM pg_available_extensions WHERE name = 'vector'")
        row = cur.fetchone()
        assert row is not None, "pgvector extension not available on the server"
        installed_version = row[0]
        assert installed_version, "pgvector reports no installed version"

        # A round-trip through the vector type proves the extension is live.
        cur.execute("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector")
        distance = cur.fetchone()
        assert distance is not None
        assert distance[0] > 0


def test_rabbitmq_publish_consume(rabbitmq_params: pika.ConnectionParameters) -> None:
    """A message published to a queue can be read back (pika round-trip)."""
    connection = pika.BlockingConnection(rabbitmq_params)
    try:
        channel = connection.channel()
        queue = channel.queue_declare(queue="analyzer-ng.connectivity", durable=False)
        assert queue.method.queue == "analyzer-ng.connectivity"

        channel.basic_publish(exchange="", routing_key="analyzer-ng.connectivity", body=b"ping")

        method, _properties, body = channel.basic_get(
            queue="analyzer-ng.connectivity", auto_ack=True
        )
        assert method is not None, "no message delivered from the queue"
        assert body == b"ping"
    finally:
        connection.close()
