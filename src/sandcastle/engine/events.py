"""Global event broadcast for real-time dashboard updates.

Architecture note - event ordering in multi-worker deployments
--------------------------------------------------------------
The EventBus is an *in-process* pub/sub system.  In a deployment with
multiple worker processes (e.g. behind gunicorn/uvicorn with ``--workers
N``), each worker maintains its own independent EventBus.  SSE clients
connected to different workers will therefore see **different** event
streams.  This is acceptable for local/single-process mode which is the
default; production deployments requiring cross-worker event delivery
should front the bus with Redis Pub/Sub or a similar broker.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """In-memory publish/subscribe event bus for real-time SSE streaming.

    Subscribers receive events via asyncio.Queue instances. This is designed
    for local mode (pure in-memory); production deployments can extend this
    to fan-out via Redis Pub/Sub if needed.

    Each event has a monotonically increasing ``seq`` field that consumers can
    use to detect gaps (e.g. after SSE reconnection via ``Last-Event-ID``).

    Leak detection
    ~~~~~~~~~~~~~~
    Subscribers that stop consuming events (e.g. SSE connections closed
    without a proper ``unsubscribe`` call) are detected in two ways:

    1. **Consecutive drop eviction** - if a subscriber's queue is full for
       ``MAX_CONSECUTIVE_DROPS`` consecutive publishes, it is automatically
       removed during the next ``publish()`` call.
    2. **Staleness sweep** - ``sweep_stale_subscribers()`` evicts any
       subscriber whose queue has been full (not drained) for longer than
       ``STALE_SUBSCRIBER_TTL_SECONDS``.  Call this periodically (e.g.
       every 60 s from a background task) to catch leaked subscribers
       that receive events too infrequently to trigger the drop counter.
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

    MAX_SUBSCRIBERS = 500
    MAX_CONSECUTIVE_DROPS = 50
    # Subscribers whose queue has been full for this many seconds are
    # considered stale and eligible for eviction by sweep.
    STALE_SUBSCRIBER_TTL_SECONDS = 300  # 5 minutes

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        # Keyed by queue object (not id()) to avoid id reuse after GC.
        self._drop_counts: dict[asyncio.Queue, int] = {}
        # Timestamp of first consecutive full-queue drop for a subscriber.
        self._first_full_ts: dict[asyncio.Queue, float] = {}
        # Monotonically increasing sequence counter for event ordering
        self._seq_counter = itertools.count(1)

    async def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue and register it.

        Returns an asyncio.Queue that will receive all published events.
        The caller must call unsubscribe() when done.
        Raises RuntimeError if the subscriber limit is reached.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            if len(self._subscribers) >= self.MAX_SUBSCRIBERS:
                raise RuntimeError(
                    f"EventBus subscriber limit reached ({self.MAX_SUBSCRIBERS})"
                )
            self._subscribers.add(queue)
        logger.debug(
            "EventBus: new subscriber (total=%d)", len(self._subscribers)
        )
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers.discard(queue)
            self._drop_counts.pop(queue, None)
            self._first_full_ts.pop(queue, None)
        logger.debug(
            "EventBus: subscriber removed (total=%d)", len(self._subscribers)
        )

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to all subscribers (fire-and-forget).

        This method is synchronous and non-blocking. Events are pushed
        into subscriber queues without awaiting - if a queue is full the
        event is silently dropped for that subscriber.

        Each event is tagged with a monotonic ``seq`` number so consumers
        can detect ordering and gaps.
        """
        if event_type not in self.EVENT_TYPES:
            logger.warning("EventBus: unknown event type '%s'", event_type)

        event = {
            "type": event_type,
            "data": data,
            "seq": next(self._seq_counter),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        now = time.monotonic()
        dead_queues: list[asyncio.Queue] = []
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
                # Successful delivery - reset drop tracking for this subscriber
                self._drop_counts.pop(queue, None)
                self._first_full_ts.pop(queue, None)
            except asyncio.QueueFull:
                count = self._drop_counts.get(queue, 0) + 1
                self._drop_counts[queue] = count
                if queue not in self._first_full_ts:
                    self._first_full_ts[queue] = now
                if count >= self.MAX_CONSECUTIVE_DROPS:
                    dead_queues.append(queue)
                    logger.warning(
                        "EventBus: removing unresponsive subscriber "
                        "after %d consecutive drops", self.MAX_CONSECUTIVE_DROPS,
                    )
                else:
                    logger.debug(
                        "EventBus: dropping event for slow subscriber "
                        "(type=%s)", event_type
                    )

        # Remove dead subscribers outside the iteration
        if dead_queues:
            for dq in dead_queues:
                self._subscribers.discard(dq)
                self._drop_counts.pop(dq, None)
                self._first_full_ts.pop(dq, None)

    async def sweep_stale_subscribers(self) -> int:
        """Remove subscribers whose queues have been full beyond the TTL.

        Returns the number of subscribers evicted.  This should be called
        periodically (e.g. every 60 s) to clean up leaked connections.
        """
        now = time.monotonic()
        stale: list[asyncio.Queue] = []
        async with self._lock:
            for queue in list(self._subscribers):
                first_ts = self._first_full_ts.get(queue)
                if first_ts is not None and (now - first_ts) >= self.STALE_SUBSCRIBER_TTL_SECONDS:
                    stale.append(queue)
            for queue in stale:
                self._subscribers.discard(queue)
                self._drop_counts.pop(queue, None)
                self._first_full_ts.pop(queue, None)
        if stale:
            logger.info(
                "EventBus: swept %d stale subscriber(s) (total=%d)",
                len(stale),
                len(self._subscribers),
            )
        return len(stale)

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)

    @property
    def drop_counts(self) -> dict[int, int]:
        """Snapshot of per-subscriber consecutive drop counts (keyed by id)."""
        return {id(q): c for q, c in self._drop_counts.items()}


# Singleton instance used across the application
event_bus = EventBus()
