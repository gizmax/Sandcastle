"""Tests for the EventBus real-time event system."""

from __future__ import annotations

import asyncio

import pytest

from sandcastle.engine.events import EventBus


class TestEventBusBasics:
    """Basic subscribe/publish/unsubscribe lifecycle."""

    @pytest.mark.asyncio
    async def test_subscribe_returns_queue(self):
        bus = EventBus()
        queue = await bus.subscribe()
        assert isinstance(queue, asyncio.Queue)
        assert bus.subscriber_count == 1
        await bus.unsubscribe(queue)
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self):
        bus = EventBus()
        queue = await bus.subscribe()
        bus.publish("run.started", {"run_id": "r1"})
        event = queue.get_nowait()
        assert event["type"] == "run.started"
        assert event["data"]["run_id"] == "r1"
        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_publish_delivers_to_multiple_subscribers(self):
        """Fan-out: each subscriber should receive every event."""
        bus = EventBus()
        q1 = await bus.subscribe()
        q2 = await bus.subscribe()
        q3 = await bus.subscribe()

        bus.publish("run.completed", {"run_id": "r2"})

        for q in (q1, q2, q3):
            event = q.get_nowait()
            assert event["type"] == "run.completed"
            assert event["data"]["run_id"] == "r2"

        for q in (q1, q2, q3):
            await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        queue = await bus.subscribe()
        await bus.unsubscribe(queue)

        bus.publish("run.started", {"run_id": "r3"})
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_double_unsubscribe_safe(self):
        """Calling unsubscribe twice should not raise."""
        bus = EventBus()
        queue = await bus.subscribe()
        await bus.unsubscribe(queue)
        await bus.unsubscribe(queue)  # Should not raise
        assert bus.subscriber_count == 0


class TestEventBusSequenceNumbers:
    """Events should have monotonically increasing sequence numbers."""

    @pytest.mark.asyncio
    async def test_events_have_seq_field(self):
        bus = EventBus()
        queue = await bus.subscribe()
        bus.publish("run.started", {"run_id": "r1"})
        event = queue.get_nowait()
        assert "seq" in event
        assert isinstance(event["seq"], int)
        assert event["seq"] >= 1
        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_seq_monotonically_increasing(self):
        bus = EventBus()
        queue = await bus.subscribe()

        bus.publish("run.started", {"run_id": "r1"})
        bus.publish("step.started", {"step_id": "s1"})
        bus.publish("step.completed", {"step_id": "s1"})
        bus.publish("run.completed", {"run_id": "r1"})

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        assert len(events) == 4
        seqs = [e["seq"] for e in events]
        # Each seq should be strictly greater than the previous
        for i in range(1, len(seqs)):
            assert seqs[i] > seqs[i - 1], f"seq[{i}]={seqs[i]} not > seq[{i-1}]={seqs[i-1]}"

        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_seq_unique_across_subscribers(self):
        """All subscribers should see the same seq number for the same event."""
        bus = EventBus()
        q1 = await bus.subscribe()
        q2 = await bus.subscribe()

        bus.publish("run.started", {"run_id": "r1"})

        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1["seq"] == e2["seq"]

        await bus.unsubscribe(q1)
        await bus.unsubscribe(q2)


class TestEventBusTimestamp:
    """Events should include an ISO timestamp."""

    @pytest.mark.asyncio
    async def test_events_have_timestamp(self):
        bus = EventBus()
        queue = await bus.subscribe()
        bus.publish("run.started", {"run_id": "r1"})
        event = queue.get_nowait()
        assert "timestamp" in event
        # Should be ISO format with timezone
        assert "T" in event["timestamp"]
        await bus.unsubscribe(queue)


class TestEventBusQueueFull:
    """Behavior when a subscriber queue is full."""

    @pytest.mark.asyncio
    async def test_drops_events_for_slow_subscriber(self):
        """If a subscriber queue is full, events should be dropped silently."""
        bus = EventBus()
        queue = await bus.subscribe()

        # Fill the queue (maxsize=256)
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        assert queue.full()

        # Next event should be dropped for this subscriber (no exception raised)
        bus.publish("step.completed", {"step_id": "overflow"})

        # Queue should still be full with the original 256 events
        assert queue.full()
        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_dead_subscriber_removed_after_consecutive_drops(self):
        """Subscribers with MAX_CONSECUTIVE_DROPS should be auto-removed."""
        bus = EventBus()
        queue = await bus.subscribe()

        # Fill the queue
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        assert queue.full()
        assert bus.subscriber_count == 1

        # Publish more events to trigger consecutive drops
        for i in range(bus.MAX_CONSECUTIVE_DROPS):
            bus.publish("step.completed", {"step_id": f"drop{i}"})

        # After MAX_CONSECUTIVE_DROPS, subscriber should be auto-removed
        assert bus.subscriber_count == 0


class TestEventBusSubscriberLimit:
    """Subscriber limit enforcement."""

    @pytest.mark.asyncio
    async def test_rejects_when_limit_reached(self):
        bus = EventBus()
        bus.MAX_SUBSCRIBERS = 3  # Lower limit for testing

        q1 = await bus.subscribe()
        q2 = await bus.subscribe()
        q3 = await bus.subscribe()

        with pytest.raises(RuntimeError, match="subscriber limit"):
            await bus.subscribe()

        # Cleanup
        for q in (q1, q2, q3):
            await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_allows_new_after_unsubscribe(self):
        bus = EventBus()
        bus.MAX_SUBSCRIBERS = 2

        q1 = await bus.subscribe()
        q2 = await bus.subscribe()

        with pytest.raises(RuntimeError):
            await bus.subscribe()

        await bus.unsubscribe(q1)

        # Now should work
        q3 = await bus.subscribe()
        assert bus.subscriber_count == 2

        await bus.unsubscribe(q2)
        await bus.unsubscribe(q3)


class TestEventBusUnknownEventType:
    """Unknown event types should still be published (with a warning)."""

    @pytest.mark.asyncio
    async def test_unknown_event_type_published(self):
        bus = EventBus()
        queue = await bus.subscribe()

        # Unknown event type - should still be delivered
        bus.publish("custom.event", {"data": "test"})

        event = queue.get_nowait()
        assert event["type"] == "custom.event"
        assert event["data"]["data"] == "test"
        await bus.unsubscribe(queue)


class TestSSEEventFormatter:
    """Tests for the _sse_event helper."""

    def test_basic_format(self):
        from sandcastle.api.routes import _sse_event

        result = _sse_event("status", {"run_id": "r1"})
        assert "event: status\n" in result
        assert "data: " in result
        assert result.endswith("\n\n")

    def test_with_event_id(self):
        from sandcastle.api.routes import _sse_event

        result = _sse_event("status", {"run_id": "r1"}, event_id=42)
        assert "id: 42\n" in result
        assert "event: status\n" in result
        assert result.endswith("\n\n")

    def test_without_event_id(self):
        from sandcastle.api.routes import _sse_event

        result = _sse_event("status", {"run_id": "r1"})
        assert "id:" not in result

    def test_event_id_zero(self):
        from sandcastle.api.routes import _sse_event

        result = _sse_event("status", {"run_id": "r1"}, event_id=0)
        assert "id: 0\n" in result

    def test_event_id_string(self):
        from sandcastle.api.routes import _sse_event

        result = _sse_event("status", {"run_id": "r1"}, event_id="abc-123")
        assert "id: abc-123\n" in result
