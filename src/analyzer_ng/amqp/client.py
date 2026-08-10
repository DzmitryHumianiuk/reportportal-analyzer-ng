"""AMQP connection management, exchange/queue declaration, and the reply
publisher (spec 01 §3.1–§3.4, §8.2).

``pika``'s ``BlockingConnection`` is not thread-safe, so every thread that talks
to the broker owns its own :class:`AmqpConnection`. This module provides:

* :class:`AmqpConnection` — lazy connect with exponential backoff, the exact
  capability-advertising exchange declaration (with the 406 redeclare fallback),
  queue/DLQ declaration, and low-level publish helpers.
* :class:`ReplyPublisher` — a thread owning a dedicated connection that drains an
  in-memory outbound queue and publishes RPC replies (default exchange) and
  dead-letters (spec §1.1, §8.2).
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlparse

import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker
from pika.spec import BasicProperties

from analyzer_ng.config import AppConfig

logger = logging.getLogger(__name__)

ONE_MINUTE: Final[int] = 60
_MAX_BACKOFF: Final[int] = ONE_MINUTE
_PRECONDITION_TYPE_MISMATCH: Final[str] = (
    "PRECONDITION_FAILED - inequivalent arg 'type' for exchange"
)


class AmqpConnectionError(Exception):
    """Raised when a connection could not be established within the retry budget."""


def remove_credentials_from_url(url: str) -> str:
    """Strip ``user:pass@`` userinfo from a URL before logging (spec §9.1)."""
    parsed = urlparse(url)
    new_netloc = re.sub("^[^:]+:[^@]*@", "", parsed.netloc)
    if parsed.netloc == new_netloc:
        return url
    return url.replace(parsed.netloc, new_netloc)


def build_amqp_url(config: AppConfig) -> str:
    """Compose the pika URL: ``<AMQP_URL>/<vhost>?heartbeat=<n>`` (spec §3.1)."""
    base = config.amqp_url.rstrip("\\/")
    return f"{base}/{config.amqp_virtual_host}?heartbeat={config.amqp_heartbeat_interval}"


class AmqpConnection:
    """A single-threaded owner of one pika ``BlockingConnection`` (spec §3.1)."""

    def __init__(self, config: AppConfig, app_version: str) -> None:
        self._config = config
        self._app_version = app_version
        self._url = build_amqp_url(config)
        self._safe_url = remove_credentials_from_url(self._url)
        self._connection: BlockingConnection | None = None
        self._exchange_declared_at: float = 0.0

    @property
    def safe_url(self) -> str:
        return self._safe_url

    def _connect(self) -> BlockingConnection:
        logger.info("Connecting to AMQP broker %s", self._safe_url)
        return pika.BlockingConnection(pika.URLParameters(self._url))

    def _connect_with_retry(self) -> BlockingConnection:
        """Exponential backoff: initial -> factor -> cap 60 s, give up after
        ``AMQP_MAX_RETRY_TIME`` (spec §3.1)."""
        start = time.monotonic()
        interval: float = self._config.amqp_initial_retry_interval
        while True:
            try:
                connection = self._connect()
                logger.info("AMQP connection established")
                return connection
            except Exception as exc:  # noqa: BLE001 — any connect failure retries
                elapsed = time.monotonic() - start
                if elapsed >= self._config.amqp_max_retry_time:
                    raise AmqpConnectionError(
                        f"could not establish AMQP connection within "
                        f"{self._config.amqp_max_retry_time}s"
                    ) from exc
                logger.warning("AMQP connect failed (%s); retrying in %.0fs", exc, interval)
                time.sleep(interval)
                interval = min(interval * self._config.amqp_backoff_factor, _MAX_BACKOFF)

    @property
    def connection(self) -> BlockingConnection:
        if self._connection is None or self._connection.is_closed:
            self._connection = self._connect_with_retry()
            self._exchange_declared_at = 0.0  # force re-declare on a fresh connection
        return self._connection

    def close(self) -> None:
        try:
            if self._connection is not None and self._connection.is_open:
                self._connection.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to close AMQP connection cleanly: %s", exc)
        self._connection = None
        self._exchange_declared_at = 0.0

    # -- topology ---------------------------------------------------------- #
    def _exchange_arguments(self) -> dict[str, Any]:
        cfg = self._config
        return {
            "analyzer": cfg.amqp_exchange_name,
            "analyzer_index": cfg.analyzer_index,
            "analyzer_priority": cfg.analyzer_priority,
            "analyzer_log_search": cfg.analyzer_log_search,
            "analyzer_suggest": cfg.analyzer_suggest,
            "analyzer_cluster": cfg.analyzer_cluster,
            "version": self._app_version,
        }

    def _do_declare_exchange(self, channel: BlockingChannel) -> None:
        channel.exchange_declare(
            exchange=self._config.amqp_exchange_name,
            exchange_type="fanout",
            durable=False,
            auto_delete=True,
            internal=False,
            arguments=self._exchange_arguments(),
        )

    def ensure_exchange(self, update_interval: int = ONE_MINUTE) -> None:
        """Declare the capability exchange, throttled to once per ``update_interval``.

        On a 406 ``inequivalent arg 'type'`` the pre-existing exchange (a leftover
        legacy exchange of a different type) is deleted and re-declared (spec §3.2).
        """
        now = time.monotonic()
        if self._exchange_declared_at and (now - self._exchange_declared_at) < update_interval:
            return
        try:
            with self.connection.channel() as channel:
                self._do_declare_exchange(channel)
        except ChannelClosedByBroker as exc:
            if exc.reply_code == 406 and _PRECONDITION_TYPE_MISMATCH in (exc.reply_text or ""):
                logger.warning(
                    "Exchange '%s' exists with a different type; deleting and re-declaring",
                    self._config.amqp_exchange_name,
                )
                with self.connection.channel() as channel:
                    channel.exchange_delete(self._config.amqp_exchange_name)
                with self.connection.channel() as channel:
                    self._do_declare_exchange(channel)
            else:
                raise
        self._exchange_declared_at = time.monotonic()
        logger.info("Exchange '%s' declared", self._config.amqp_exchange_name)

    def declare_and_bind_queue(self, channel: BlockingChannel, queue_name: str) -> None:
        """Declare a durable queue and bind it to the fanout exchange (spec §3.3)."""
        channel.queue_declare(
            queue=queue_name, durable=True, exclusive=False, auto_delete=False, arguments=None
        )
        channel.queue_bind(
            exchange=self._config.amqp_exchange_name, queue=queue_name, routing_key=None
        )

    def declare_dlq(self, channel: BlockingChannel, dlq_name: str) -> None:
        """Declare the durable dead-letter queue (spec §8.2). It is reachable via
        the default exchange by its own name, so no binding is needed."""
        channel.queue_declare(
            queue=dlq_name, durable=True, exclusive=False, auto_delete=False, arguments=None
        )


# --------------------------------------------------------------------------- #
# Outbound messages + reply publisher thread.
# --------------------------------------------------------------------------- #
@dataclass
class _Reply:
    reply_to: str
    correlation_id: str
    body: str


@dataclass
class _ResultPublish:
    """An analysis result published to the service-api reply exchange.

    The 5.15.3+ service-api sends ``analyze`` fire-and-forget and consumes the
    results from a queue bound to a direct exchange it declares
    (``rp.amqp.analyzerResponseExchange`` / ``...Queue``, defaults
    ``analyzer-reply`` / ``analysis.matches``). The exchange is re-declared here
    with the same durable-direct shape before every publish, so a result sent
    while the service-api has not started yet cannot close the channel with a
    404 (idempotent declare; unroutable results are dropped, matching the
    at-most-once semantics the stock analyzer has on this path).
    """

    exchange: str
    routing_key: str
    body: str
    headers: dict[str, Any] = field(default_factory=dict)


@dataclass
class _DeadLetter:
    body: bytes
    headers: dict[str, Any] = field(default_factory=dict)


class ReplyPublisher:
    """Owns a dedicated connection; drains an outbound queue and publishes replies
    to the default exchange and dead-letters to the DLQ (spec §1.1, §3.4, §8.2)."""

    def __init__(self, connection: AmqpConnection, dlq_name: str) -> None:
        self._conn = connection
        self._dlq_name = dlq_name
        self._outbox: queue.Queue[_Reply | _ResultPublish | _DeadLetter | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- producer API (called from worker/consumer threads) ---------------- #
    def reply(self, reply_to: str, correlation_id: str, body: str) -> None:
        self._outbox.put(_Reply(reply_to, correlation_id, body))

    def publish_result(
        self, exchange: str, routing_key: str, body: str, headers: dict[str, Any] | None = None
    ) -> None:
        self._outbox.put(_ResultPublish(exchange, routing_key, body, headers or {}))

    def dead_letter(self, body: bytes, headers: dict[str, Any]) -> None:
        self._outbox.put(_DeadLetter(body, headers))

    @property
    def pending(self) -> int:
        return self._outbox.qsize()

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        # Declare the DLQ up front so dead-letters have a home immediately.
        with self._conn.connection.channel() as channel:
            self._conn.declare_dlq(channel, self._dlq_name)
        self._thread = threading.Thread(target=self._run, name="reply-publisher", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._outbox.put(None)  # unblock the drain
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._conn.close()

    def _run(self) -> None:
        while True:
            try:
                message = self._outbox.get(timeout=0.1)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            if message is None:
                if self._stop.is_set() and self._outbox.empty():
                    return
                continue
            try:
                self._publish(message)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to publish outbound AMQP message: %s", exc)

    def _publish(self, message: _Reply | _ResultPublish | _DeadLetter) -> None:
        """Publish with one reconnect-and-retry on a transient connection loss."""
        for attempt in (1, 2):
            try:
                channel = self._conn.connection.channel()
                try:
                    if isinstance(message, _Reply):
                        channel.basic_publish(
                            exchange="",
                            routing_key=message.reply_to,
                            properties=BasicProperties(
                                correlation_id=message.correlation_id,
                                content_type="application/json",
                            ),
                            mandatory=False,
                            body=message.body.encode("utf-8"),
                        )
                    elif isinstance(message, _ResultPublish):
                        # Same durable-direct shape the service-api declares; an
                        # idempotent re-declare so publishing never races its start.
                        channel.exchange_declare(
                            exchange=message.exchange, exchange_type="direct", durable=True
                        )
                        channel.basic_publish(
                            exchange=message.exchange,
                            routing_key=message.routing_key,
                            properties=BasicProperties(
                                content_type="application/json",
                                headers=message.headers,
                            ),
                            mandatory=False,
                            body=message.body.encode("utf-8"),
                        )
                    else:
                        self._conn.declare_dlq(channel, self._dlq_name)
                        channel.basic_publish(
                            exchange="",
                            routing_key=self._dlq_name,
                            properties=BasicProperties(headers=message.headers),
                            mandatory=False,
                            body=message.body,
                        )
                finally:
                    if channel.is_open:
                        channel.close()
                return
            except (AMQPConnectionError, ChannelClosedByBroker) as exc:
                logger.warning("Publish failed (attempt %d): %s; reconnecting", attempt, exc)
                self._conn.close()
                if attempt == 2:
                    raise
