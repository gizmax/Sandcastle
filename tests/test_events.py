"""Tests for EventBus pub/sub system."""

import asyncio

import pytest

from sandcastle.engine.events import EventBus


class TestEventBus:

    @pytest.mark.asyncio
    async def test_subscribe_creates_queue(self):
        bus = EventBus()
        q = await bus.subscribe()
        assert isinstance(q, asyncio.Queue)
        assert bus.subscriber_count == 1
        await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_queue(self):
        bus = EventBus()
        q = await bus.subscribe()
        assert bus.subscriber_count == 1
        await bus.unsubscribe(q)
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_idempotent(self):
        bus = EventBus()
        q = await bus.subscribe()
        await bus.unsubscribe(q)
        await bus.unsubscribe(q)
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self):
        bus = EventBus()
        q = await bus.subscribe()
        bus.publish("run.started", {"run_id": "abc"})
        event = q.get_nowait()
        assert event["type"] == "run.started"
        assert event["data"]["run_id"] == "abc"
        assert "timestamp" in event
        await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self):
        bus = EventBus()
        q1 = await bus.subscribe()
        q2 = await bus.subscribe()
        bus.publish("run.completed", {"status": "ok"})
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1["type"] == "run.completed"
        assert e2["type"] == "run.completed"
        await bus.unsubscribe(q1)
        await bus.unsubscribe(q2)

    @pytest.mark.asyncio
    async def test_publish_drops_on_full_queue(self):
        bus = EventBus()
        q = await bus.subscribe()
        # Fill the queue (maxsize=256)
        for i in range(260):
            bus.publish("run.started", {"i": i})
        assert q.qsize() == 256
        await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_publish_unknown_event_type(self):
        bus = EventBus()
        q = await bus.subscribe()
        bus.publish("unknown.event", {"foo": "bar"})
        event = q.get_nowait()
        assert event["type"] == "unknown.event"
        await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_no_delivery_after_unsubscribe(self):
        bus = EventBus()
        q = await bus.subscribe()
        await bus.unsubscribe(q)
        bus.publish("run.started", {"run_id": "abc"})
        assert q.empty()

    def test_valid_event_types(self):
        assert "run.started" in EventBus.EVENT_TYPES
        assert "run.completed" in EventBus.EVENT_TYPES
        assert "run.failed" in EventBus.EVENT_TYPES
        assert "step.started" in EventBus.EVENT_TYPES
        assert "step.completed" in EventBus.EVENT_TYPES
        assert "step.failed" in EventBus.EVENT_TYPES
        assert "dlq.new" in EventBus.EVENT_TYPES

    @pytest.mark.asyncio
    async def test_subscriber_count_tracks_correctly(self):
        bus = EventBus()
        assert bus.subscriber_count == 0
        q1 = await bus.subscribe()
        assert bus.subscriber_count == 1
        q2 = await bus.subscribe()
        assert bus.subscriber_count == 2
        await bus.unsubscribe(q1)
        assert bus.subscriber_count == 1
        await bus.unsubscribe(q2)
        assert bus.subscriber_count == 0
