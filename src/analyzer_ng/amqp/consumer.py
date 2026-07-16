"""AMQP consumer thread (spec 01 §1.1, §3.3, §8.3).

Each :class:`Consumer` owns its own :class:`~analyzer_ng.amqp.client.AmqpConnection`
(pika ``BlockingConnection`` is not thread-safe), binds one durable queue to the
fanout exchange, and consumes with ``prefetch_count`` QoS. Because the exchange is
fanout every queue receives every routing key, so each consumer applies a
routing-key filter (``all`` drops ``train_models``; ``train`` keeps only it).

Ack policy (spec §8.3): parse-time ack. Malformed JSON is dead-lettered and
``basic_nack(requeue=False)``-ed; parsed messages are ``basic_ack``-ed immediately
(on the consumer's own channel/thread) before the worker runs, then handled with
the in-process retry policy of §8.2.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable

from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker
from pika.spec import Basic, BasicProperties

from analyzer_ng.amqp.client import AmqpConnection, ReplyPublisher
from analyzer_ng.amqp.dispatcher import ProcessingItem, WorkerPool

logger = logging.getLogger(__name__)


def get_priority(props: BasicProperties) -> int:
    """Priority from the ``timestamp_in_ms`` header, else now (spec §4.1, §8.1).

    The header may be an int or a string with a trailing ``L`` (e.g.
    ``1749029201296L``) — strip it before ``int()``.
    """
    priority = int(time.time() * 1000)
    if props.headers and "timestamp_in_ms" in props.headers:
        raw = str(props.headers["timestamp_in_ms"])
        if raw.endswith("L"):
            raw = raw[:-1]
        try:
            priority = int(raw)
        except (ValueError, TypeError):
            logger.warning(
                "Failed to parse timestamp_in_ms header: %r", props.headers["timestamp_in_ms"]
            )
    return priority


class Consumer:
    """Consumes one queue in its own thread and feeds the worker pool."""

    def __init__(
        self,
        name: str,
        queue_name: str,
        connection: AmqpConnection,
        pool: WorkerPool,
        publisher: ReplyPublisher,
        route_filter: Callable[[str], bool],
        next_seq: Callable[[], int],
        *,
        prefetch: int = 1,
    ) -> None:
        self.name = name
        self._queue_name = queue_name
        self._conn = connection
        self._pool = pool
        self._publisher = publisher
        self._route_filter = route_filter
        self._next_seq = next_seq
        self._prefetch = prefetch
        self._thread: threading.Thread | None = None
        self._channel: BlockingChannel | None = None
        self._stop = threading.Event()
        self._alive = threading.Event()

    @property
    def alive(self) -> bool:
        return self._alive.is_set()

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"consumer-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        """Cancel consuming (``basic_cancel`` via a thread-safe callback) and join."""
        self._stop.set()
        connection = self._conn
        channel = self._channel
        try:
            if channel is not None and channel.is_open:
                connection.connection.add_callback_threadsafe(channel.stop_consuming)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not schedule stop_consuming for %s: %s", self.name, exc)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._conn.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._conn.ensure_exchange()
                channel = self._conn.connection.channel()
                self._conn.declare_and_bind_queue(channel, self._queue_name)
                channel.basic_qos(prefetch_count=self._prefetch, prefetch_size=0)
                channel.basic_consume(
                    queue=self._queue_name,
                    on_message_callback=self._on_message,
                    auto_ack=False,
                    exclusive=False,
                )
                self._channel = channel
                self._alive.set()
                logger.info("Consumer '%s' consuming queue '%s'", self.name, self._queue_name)
                channel.start_consuming()
            except (AMQPConnectionError, ChannelClosedByBroker) as exc:
                self._alive.clear()
                if self._stop.is_set():
                    break
                logger.warning("Consumer '%s' lost connection: %s; reconnecting", self.name, exc)
                self._conn.close()
            except Exception as exc:  # noqa: BLE001
                self._alive.clear()
                if self._stop.is_set():
                    break
                logger.exception("Consumer '%s' unexpected error: %s; reconnecting", self.name, exc)
                self._conn.close()
            else:
                # start_consuming returned (stop_consuming was called): exit loop.
                break
        self._alive.clear()

    # -- message callback (runs on this consumer's thread) ----------------- #
    def _on_message(
        self,
        channel: BlockingChannel,
        method: Basic.Deliver,
        props: BasicProperties,
        body: bytes,
    ) -> None:
        routing_key = method.routing_key or ""
        try:
            message = json.loads(body, strict=False)
        except Exception as exc:  # noqa: BLE001 — any parse failure is a bad message
            logger.error("Malformed JSON on '%s' (routing_key=%s): %s", self.name, routing_key, exc)
            self._publisher.dead_letter(
                body,
                {
                    "x-error": f"JSONDecodeError: {exc}",
                    "x-routing-key": routing_key,
                    "x-retries": 0,
                },
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        # Parse-time ack (spec §8.3): ack immediately, then process out of band.
        channel.basic_ack(delivery_tag=method.delivery_tag)

        if not self._route_filter(routing_key):
            logger.debug("Consumer '%s' dropping routing_key '%s'", self.name, routing_key)
            return

        item = ProcessingItem(
            priority=get_priority(props),
            number=self._next_seq(),
            routing_key=routing_key,
            reply_to=props.reply_to,
            correlation_id=props.correlation_id or "",
            body=message,
        )
        # Blocks while the dispatch queue is full — this is the backpressure path
        # (§8.1): the broker stops delivering because we stop reading.
        self._pool.submit(item)
