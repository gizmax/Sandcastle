"""Global event broadcast for real-time dashboard updates."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """In-memory publish/subscribe event bus for real-time SSE streaming.

    Subscribers receive events via asyncio.Queue instances. This is designed
    for local mode (pure in-memory); production deployments can extend this
    to fan-out via Redis Pub/Sub if needed.
    """

    # Valid event types
    EVENT_TYPES = {
        "run.started",
        "run.completed",
        "run.failed",
        "step.started",
        "step.completed",
        "step.failed",
        "dlq.new",
    }

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue and register it.

        Returns an asyncio.Queue that will receive all published events.
        The caller must call unsubscribe() when done.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        logger.debug(
            "EventBus: new subscriber (total=%d)", len(self._subscribers)
        )
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers.discard(queue)
        logger.debug(
            "EventBus: subscriber removed (total=%d)", len(self._subscribers)
        )

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to all subscribers (fire-and-forget).

        This method is synchronous and non-blocking. Events are pushed
        into subscriber queues without awaiting - if a queue is full the
        event is silently dropped for that subscriber.
        """
        if event_type not in self.EVENT_TYPES:
            logger.warning("EventBus: unknown event type '%s'", event_type)

        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug(
                    "EventBus: dropping event for slow subscriber "
                    "(type=%s)", event_type
                )

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)


# Singleton instance used across the application
event_bus = EventBus()
