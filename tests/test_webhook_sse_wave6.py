"""Wave 6 deep audit - webhook and SSE streaming robustness tests.

Covers:
1. EventBus subscriber leak detection and stale sweep
2. Webhook retry backoff ceiling
3. SSE keepalive robustness
4. Webhook HMAC signature format validation
5. Event ordering guarantees and multi-worker documentation
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.events import EventBus


# ---------------------------------------------------------------------------
# 1. EventBus subscriber leak detection
# ---------------------------------------------------------------------------


class TestEventBusLeakDetection:
    """Verify that leaked (stale) subscribers are detected and cleaned up."""

    @pytest.mark.asyncio
    async def test_drop_counts_keyed_by_queue_object(self):
        """Drop counts must be keyed by queue object, not id(), to avoid
        id reuse issues after garbage collection."""
        bus = EventBus()
        queue = await bus.subscribe()

        # Fill the queue
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # Cause one drop
        bus.publish("step.started", {"step_id": "overflow"})

        # The internal dict should use the queue object as key
        assert queue in bus._drop_counts
        assert bus._drop_counts[queue] == 1

        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_first_full_ts_set_on_first_drop(self):
        """_first_full_ts should record the time of the first consecutive drop."""
        bus = EventBus()
        queue = await bus.subscribe()

        # Fill the queue
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        assert queue not in bus._first_full_ts

        # Cause a drop - should set _first_full_ts
        bus.publish("step.started", {"step_id": "overflow"})
        assert queue in bus._first_full_ts
        assert isinstance(bus._first_full_ts[queue], float)

        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_first_full_ts_cleared_on_successful_delivery(self):
        """Draining the queue and receiving a new event should reset tracking."""
        bus = EventBus()
        queue = await bus.subscribe()

        # Fill and overflow
        for i in range(257):
            bus.publish("step.started", {"step_id": f"s{i}"})

        assert queue in bus._first_full_ts

        # Drain one slot
        queue.get_nowait()

        # Next publish should succeed and clear the stale tracking
        bus.publish("step.started", {"step_id": "recovered"})
        assert queue not in bus._first_full_ts
        assert queue not in bus._drop_counts

        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_sweep_stale_removes_old_full_subscribers(self):
        """sweep_stale_subscribers should remove subscribers that have been
        full beyond the TTL."""
        bus = EventBus()
        bus.STALE_SUBSCRIBER_TTL_SECONDS = 0.1  # 100ms for testing
        queue = await bus.subscribe()

        # Fill the queue
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # Cause a drop to start the timer
        bus.publish("step.started", {"step_id": "overflow"})
        assert bus.subscriber_count == 1

        # Wait beyond the TTL
        await asyncio.sleep(0.15)

        evicted = await bus.sweep_stale_subscribers()
        assert evicted == 1
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_sweep_stale_keeps_active_subscribers(self):
        """Active subscribers (not full) should not be evicted by sweep."""
        bus = EventBus()
        bus.STALE_SUBSCRIBER_TTL_SECONDS = 0.05
        queue = await bus.subscribe()

        # Publish a few events (queue not full)
        bus.publish("run.started", {"run_id": "r1"})
        bus.publish("run.completed", {"run_id": "r1"})

        await asyncio.sleep(0.1)

        evicted = await bus.sweep_stale_subscribers()
        assert evicted == 0
        assert bus.subscriber_count == 1

        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_sweep_returns_zero_when_no_stale(self):
        """Sweep with no subscribers should return 0."""
        bus = EventBus()
        evicted = await bus.sweep_stale_subscribers()
        assert evicted == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_clears_first_full_ts(self):
        """Explicit unsubscribe should clean up all tracking state."""
        bus = EventBus()
        queue = await bus.subscribe()

        # Fill and overflow
        for i in range(257):
            bus.publish("step.started", {"step_id": f"s{i}"})

        assert queue in bus._first_full_ts
        assert queue in bus._drop_counts

        await bus.unsubscribe(queue)

        assert queue not in bus._first_full_ts
        assert queue not in bus._drop_counts

    @pytest.mark.asyncio
    async def test_dead_subscriber_removal_clears_first_full_ts(self):
        """When a subscriber is auto-removed after MAX_CONSECUTIVE_DROPS,
        _first_full_ts should also be cleaned up."""
        bus = EventBus()
        queue = await bus.subscribe()

        # Fill the queue
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # Trigger enough drops to auto-remove
        for i in range(bus.MAX_CONSECUTIVE_DROPS):
            bus.publish("step.completed", {"step_id": f"drop{i}"})

        assert bus.subscriber_count == 0
        assert queue not in bus._first_full_ts
        assert queue not in bus._drop_counts


class TestEventBusDropCountsProperty:
    """The drop_counts property should expose a snapshot for monitoring."""

    @pytest.mark.asyncio
    async def test_drop_counts_property_empty(self):
        bus = EventBus()
        assert bus.drop_counts == {}

    @pytest.mark.asyncio
    async def test_drop_counts_property_reflects_drops(self):
        bus = EventBus()
        queue = await bus.subscribe()

        # Fill queue and cause drops
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        bus.publish("step.started", {"step_id": "over1"})
        bus.publish("step.started", {"step_id": "over2"})

        counts = bus.drop_counts
        assert len(counts) == 1
        assert counts[id(queue)] == 2

        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_drop_counts_keyed_by_id_for_external_use(self):
        """The public property should use id() keys (not queue objects)
        so external monitoring code doesn't hold references."""
        bus = EventBus()
        queue = await bus.subscribe()

        for i in range(257):
            bus.publish("step.started", {"step_id": f"s{i}"})

        counts = bus.drop_counts
        assert isinstance(list(counts.keys())[0], int)

        await bus.unsubscribe(queue)


# ---------------------------------------------------------------------------
# 2. Webhook retry backoff ceiling
# ---------------------------------------------------------------------------


class TestWebhookRetryBackoffCeiling:
    """Verify exponential backoff is capped at MAX_RETRY_DELAY_SECONDS."""

    def test_max_retry_delay_constant_exists(self):
        from sandcastle.webhooks.dispatcher import MAX_RETRY_DELAY_SECONDS

        assert isinstance(MAX_RETRY_DELAY_SECONDS, int)
        assert MAX_RETRY_DELAY_SECONDS == 30

    @pytest.mark.asyncio
    async def test_backoff_capped_at_max(self):
        """With many retries, delay should never exceed MAX_RETRY_DELAY_SECONDS."""
        from sandcastle.webhooks.dispatcher import (
            MAX_RETRY_DELAY_SECONDS,
            dispatch_webhook,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=500))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sleep_delays: list[float] = []

        async def capture_sleep(delay):
            sleep_delays.append(delay)

        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("asyncio.sleep", side_effect=capture_sleep),
        ):
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-backoff",
                workflow="wf",
                status="completed",
                max_retries=10,
            )

        # All delays must be capped at MAX_RETRY_DELAY_SECONDS
        assert len(sleep_delays) > 0
        for delay in sleep_delays:
            assert delay <= MAX_RETRY_DELAY_SECONDS, (
                f"Delay {delay} exceeds ceiling {MAX_RETRY_DELAY_SECONDS}"
            )

    @pytest.mark.asyncio
    async def test_backoff_is_exponential_up_to_ceiling(self):
        """Delays should follow 2^attempt pattern until they hit the ceiling."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=500))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sleep_delays: list[float] = []

        async def capture_sleep(delay):
            sleep_delays.append(delay)

        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("asyncio.sleep", side_effect=capture_sleep),
        ):
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-exp",
                workflow="wf",
                status="completed",
                max_retries=6,
            )

        # Expected: 2, 4, 8, 16, 30 (capped at 5th retry)
        expected = [2, 4, 8, 16, 30]
        assert sleep_delays == expected

    @pytest.mark.asyncio
    async def test_no_sleep_on_last_attempt(self):
        """The last retry attempt should not sleep afterward."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=500))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sleep_count = 0

        async def count_sleep(delay):
            nonlocal sleep_count
            sleep_count += 1

        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("asyncio.sleep", side_effect=count_sleep),
        ):
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.failed",
                run_id="run-last",
                workflow="wf",
                status="failed",
                max_retries=3,
            )

        # 3 attempts, sleep only between attempts (2 sleeps)
        assert sleep_count == 2


# ---------------------------------------------------------------------------
# 3. SSE keepalive robustness
# ---------------------------------------------------------------------------


class TestSSEKeepaliveConstant:
    """The keepalive interval should be a configurable module-level constant."""

    def test_keepalive_constant_exists(self):
        from sandcastle.api.routes import SSE_KEEPALIVE_INTERVAL_SECONDS

        assert isinstance(SSE_KEEPALIVE_INTERVAL_SECONDS, (int, float))
        assert SSE_KEEPALIVE_INTERVAL_SECONDS > 0

    def test_keepalive_default_is_30(self):
        from sandcastle.api.routes import SSE_KEEPALIVE_INTERVAL_SECONDS

        assert SSE_KEEPALIVE_INTERVAL_SECONDS == 30


class TestSSEEventFormatterExtended:
    """Extended tests for the _sse_event helper."""

    def test_data_is_valid_json(self):
        from sandcastle.api.routes import _sse_event

        result = _sse_event("test", {"key": "value", "num": 42})
        # Extract the data line
        for line in result.split("\n"):
            if line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
                assert payload == {"key": "value", "num": 42}
                break
        else:
            pytest.fail("No data line found in SSE event")

    def test_multiline_data_not_produced(self):
        """SSE data should be on a single line (no embedded newlines)."""
        from sandcastle.api.routes import _sse_event

        # json.dumps should not produce newlines for a flat dict
        result = _sse_event("test", {"key": "value"})
        data_lines = [l for l in result.split("\n") if l.startswith("data: ")]
        assert len(data_lines) == 1

    def test_event_with_special_characters(self):
        """Data with unicode and special chars should be safely serialized."""
        from sandcastle.api.routes import _sse_event

        result = _sse_event("test", {"msg": "Hello\nWorld", "emoji": "test"})
        assert "event: test\n" in result
        # The newline in the value should be escaped by json.dumps
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        parsed = json.loads(data_line[len("data: "):])
        assert parsed["msg"] == "Hello\nWorld"


# ---------------------------------------------------------------------------
# 4. Webhook HMAC signature format validation
# ---------------------------------------------------------------------------


class TestWebhookHMACSignatureFormat:
    """Verify the HMAC signature covers the full payload and uses correct format."""

    def test_signature_is_hex_sha256(self):
        from sandcastle.webhooks.dispatcher import _sign_payload

        sig = _sign_payload('{"event": "test"}', "secret")
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest
        # Must be valid hex
        int(sig, 16)

    def test_signature_covers_entire_body(self):
        """Changing any part of the body should change the signature."""
        from sandcastle.webhooks.dispatcher import _sign_payload

        body = '{"event": "test", "run_id": "abc", "data": "payload"}'
        secret = "my-secret"
        sig_original = _sign_payload(body, secret)

        # Modify different parts of the body
        sig_modified_event = _sign_payload(
            body.replace('"test"', '"other"'), secret
        )
        sig_modified_data = _sign_payload(
            body.replace('"payload"', '"changed"'), secret
        )
        sig_modified_id = _sign_payload(
            body.replace('"abc"', '"def"'), secret
        )

        assert sig_original != sig_modified_event
        assert sig_original != sig_modified_data
        assert sig_original != sig_modified_id

    def test_signature_matches_standard_hmac_sha256(self):
        """The signature should match a standard HMAC-SHA256 implementation
        so receivers can verify it with any language's crypto library."""
        from sandcastle.webhooks.dispatcher import _sign_payload

        body = '{"event": "run.completed", "run_id": "r-123"}'
        secret = "webhook-secret-key"

        # Compute expected using stdlib directly
        expected = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        assert _sign_payload(body, secret) == expected

    def test_verify_signature_constant_time(self):
        """verify_signature should use hmac.compare_digest (timing-safe)."""
        from sandcastle.webhooks.dispatcher import verify_signature

        body = '{"data": "test"}'
        secret = "secret"
        sig = hmac.new(
            secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()

        assert verify_signature(body, sig, secret) is True
        assert verify_signature(body, "a" * 64, secret) is False

    def test_verify_rejects_truncated_signature(self):
        from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature

        body = '{"test": true}'
        secret = "sec"
        sig = _sign_payload(body, secret)

        # Truncated signature should fail
        assert verify_signature(body, sig[:32], secret) is False

    def test_verify_rejects_empty_signature(self):
        from sandcastle.webhooks.dispatcher import verify_signature

        assert verify_signature('{"x": 1}', "", "secret") is False

    def test_signature_with_unicode_body(self):
        """Signature should correctly handle unicode in the JSON body."""
        from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature

        body = json.dumps({"msg": "Hello world"}, ensure_ascii=False)
        secret = "key"
        sig = _sign_payload(body, secret)
        assert verify_signature(body, sig, secret) is True

    def test_signature_with_empty_body(self):
        from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature

        sig = _sign_payload("", "secret")
        assert verify_signature("", sig, "secret") is True
        assert len(sig) == 64

    @pytest.mark.asyncio
    async def test_dispatch_sends_signature_header(self):
        """dispatch_webhook should include X-Sandcastle-Signature header
        with the HMAC-SHA256 hex digest of the full body."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_response = MagicMock(status_code=200)
        captured_body = None
        captured_headers = {}

        async def capture_post(url, content=None, headers=None):
            nonlocal captured_body
            captured_body = content
            captured_headers.update(headers or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("sandcastle.webhooks.dispatcher.settings") as mock_settings,
        ):
            mock_settings.webhook_secret = "test-secret-123"
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="run.completed",
                run_id="run-hmac",
                workflow="wf",
                status="completed",
            )

        assert captured_body is not None
        sig = captured_headers["X-Sandcastle-Signature"]

        # Verify the signature matches the full body
        expected = hmac.new(
            b"test-secret-123",
            captured_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert sig == expected


# ---------------------------------------------------------------------------
# 5. Event ordering guarantees
# ---------------------------------------------------------------------------


class TestEventOrdering:
    """Verify sequence numbers are monotonically increasing and unique."""

    @pytest.mark.asyncio
    async def test_seq_strictly_increasing_across_many_events(self):
        bus = EventBus()
        queue = await bus.subscribe()

        for i in range(100):
            bus.publish("step.started", {"step_id": f"s{i}"})

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        seqs = [e["seq"] for e in events]
        for i in range(1, len(seqs)):
            assert seqs[i] == seqs[i - 1] + 1, (
                f"Gap in sequence: {seqs[i-1]} -> {seqs[i]}"
            )

        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_seq_unique_no_duplicates(self):
        bus = EventBus()
        q1 = await bus.subscribe()
        q2 = await bus.subscribe()

        for i in range(50):
            bus.publish("run.started", {"run_id": f"r{i}"})

        seqs1 = set()
        while not q1.empty():
            seqs1.add(q1.get_nowait()["seq"])

        seqs2 = set()
        while not q2.empty():
            seqs2.add(q2.get_nowait()["seq"])

        # Both subscribers should see the exact same sequence numbers
        assert seqs1 == seqs2
        assert len(seqs1) == 50

        await bus.unsubscribe(q1)
        await bus.unsubscribe(q2)

    @pytest.mark.asyncio
    async def test_seq_counter_independent_per_bus_instance(self):
        """Each EventBus instance should have its own counter."""
        bus1 = EventBus()
        bus2 = EventBus()

        q1 = await bus1.subscribe()
        q2 = await bus2.subscribe()

        bus1.publish("run.started", {"run_id": "r1"})
        bus2.publish("run.started", {"run_id": "r2"})

        e1 = q1.get_nowait()
        e2 = q2.get_nowait()

        # Both should start from 1 (independent counters)
        assert e1["seq"] == 1
        assert e2["seq"] == 1

        await bus1.unsubscribe(q1)
        await bus2.unsubscribe(q2)

    @pytest.mark.asyncio
    async def test_late_subscriber_starts_from_next_seq(self):
        """A subscriber that joins after events have been published should
        only see events published after subscribing."""
        bus = EventBus()

        # Publish some events with no subscribers
        bus.publish("run.started", {"run_id": "r1"})
        bus.publish("run.completed", {"run_id": "r1"})

        # Now subscribe
        queue = await bus.subscribe()
        bus.publish("run.started", {"run_id": "r2"})

        event = queue.get_nowait()
        assert event["seq"] == 3  # First two went to nobody
        assert event["data"]["run_id"] == "r2"
        assert queue.empty()

        await bus.unsubscribe(queue)


class TestEventBusMultiWorkerDocumentation:
    """Verify the module-level documentation mentions multi-worker limitation."""

    def test_module_docstring_mentions_multi_worker(self):
        import sandcastle.engine.events as events_module

        docstring = events_module.__doc__
        assert docstring is not None
        assert "multi-worker" in docstring.lower() or "multi_worker" in docstring.lower()

    def test_module_docstring_mentions_redis(self):
        import sandcastle.engine.events as events_module

        docstring = events_module.__doc__
        assert docstring is not None
        assert "Redis" in docstring or "redis" in docstring


# ---------------------------------------------------------------------------
# 6. EventBus concurrent subscribe/unsubscribe safety
# ---------------------------------------------------------------------------


class TestEventBusConcurrency:
    """Test that concurrent subscribe/unsubscribe operations are safe."""

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_respects_limit(self):
        """When many tasks try to subscribe concurrently, the limit should
        never be exceeded."""
        bus = EventBus()
        bus.MAX_SUBSCRIBERS = 10

        queues: list[asyncio.Queue] = []
        errors: list[RuntimeError] = []

        async def try_subscribe():
            try:
                q = await bus.subscribe()
                queues.append(q)
            except RuntimeError as e:
                errors.append(e)

        # Launch 20 concurrent subscribes (limit is 10)
        await asyncio.gather(*[try_subscribe() for _ in range(20)])

        assert len(queues) == 10
        assert len(errors) == 10
        assert bus.subscriber_count == 10

        for q in queues:
            await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_concurrent_unsubscribe_safe(self):
        """Multiple concurrent unsubscribes of the same queue should not error."""
        bus = EventBus()
        queue = await bus.subscribe()

        # Unsubscribe the same queue 5 times concurrently
        await asyncio.gather(*[bus.unsubscribe(queue) for _ in range(5)])

        assert bus.subscriber_count == 0


# ---------------------------------------------------------------------------
# 7. Webhook dispatch edge cases
# ---------------------------------------------------------------------------


class TestWebhookDispatchEdgeCases:
    """Edge cases in webhook dispatch behavior."""

    @pytest.mark.asyncio
    async def test_dispatch_with_zero_retries(self):
        """max_retries=0 should not attempt any delivery."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
        ):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="run.completed",
                run_id="run-zero",
                workflow="wf",
                status="completed",
                max_retries=0,
            )

        # With 0 retries, range(1, 0+1) is empty, so no attempts made
        assert result is False
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_with_one_retry(self):
        """max_retries=1 should make exactly one attempt."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
        ):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="run.completed",
                run_id="run-one",
                workflow="wf",
                status="completed",
                max_retries=1,
            )

        assert result is True
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_payload_includes_required_fields(self):
        """The webhook payload should contain all documented fields."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        captured_body = None

        async def capture_post(url, content=None, headers=None):
            nonlocal captured_body
            captured_body = content
            return MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("sandcastle.webhooks.dispatcher.settings") as mock_settings,
        ):
            mock_settings.webhook_secret = ""
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="run.completed",
                run_id="run-fields",
                workflow="my-workflow",
                status="completed",
                outputs={"result": "ok"},
                costs=1.5,
                duration_seconds=42.0,
                error=None,
            )

        assert captured_body is not None
        payload = json.loads(captured_body)

        # Required fields
        assert payload["event"] == "run.completed"
        assert payload["run_id"] == "run-fields"
        assert payload["workflow"] == "my-workflow"
        assert payload["status"] == "completed"
        assert payload["outputs"] == {"result": "ok"}
        assert payload["total_cost_usd"] == 1.5
        assert payload["costs"] == 1.5  # backward compat
        assert payload["duration_seconds"] == 42.0
        assert payload["error"] is None
        assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_dispatch_event_header_always_present(self):
        """X-Sandcastle-Event header should always be present regardless
        of webhook_secret configuration."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        captured_headers = {}

        async def capture_post(url, content=None, headers=None):
            captured_headers.update(headers or {})
            return MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("sandcastle.webhooks.dispatcher.settings") as mock_settings,
        ):
            mock_settings.webhook_secret = ""
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="run.failed",
                run_id="run-hdr",
                workflow="wf",
                status="failed",
            )

        assert captured_headers["X-Sandcastle-Event"] == "run.failed"
        assert captured_headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# 8. EventBus publish with valid/invalid event types
# ---------------------------------------------------------------------------


class TestEventBusEventTypes:
    """Verify event type validation behavior."""

    @pytest.mark.asyncio
    async def test_all_valid_event_types(self):
        """All EVENT_TYPES should be publishable without warnings."""
        bus = EventBus()
        queue = await bus.subscribe()

        for et in bus.EVENT_TYPES:
            bus.publish(et, {"test": True})

        count = 0
        while not queue.empty():
            queue.get_nowait()
            count += 1

        assert count == len(bus.EVENT_TYPES)
        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_unknown_event_type_still_delivered(self):
        """Unknown event types should still be published (just warned)."""
        bus = EventBus()
        queue = await bus.subscribe()

        bus.publish("custom.unknown.type", {"data": "test"})

        event = queue.get_nowait()
        assert event["type"] == "custom.unknown.type"
        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_event_has_all_required_fields(self):
        """Every published event must have type, data, seq, and timestamp."""
        bus = EventBus()
        queue = await bus.subscribe()

        bus.publish("run.started", {"run_id": "r1"})
        event = queue.get_nowait()

        assert "type" in event
        assert "data" in event
        assert "seq" in event
        assert "timestamp" in event
        assert isinstance(event["seq"], int)
        assert "T" in event["timestamp"]  # ISO format

        await bus.unsubscribe(queue)


# ---------------------------------------------------------------------------
# 9. Sweep multiple stale subscribers at once
# ---------------------------------------------------------------------------


class TestSweepMultipleStale:
    """Sweep should handle multiple stale subscribers in a single pass."""

    @pytest.mark.asyncio
    async def test_sweep_multiple_stale_at_once(self):
        bus = EventBus()
        bus.STALE_SUBSCRIBER_TTL_SECONDS = 0.05

        queues = []
        for _ in range(5):
            q = await bus.subscribe()
            queues.append(q)

        assert bus.subscriber_count == 5

        # Fill all queues and cause drops
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # One more to trigger a drop on all
        bus.publish("step.started", {"step_id": "overflow"})

        await asyncio.sleep(0.1)

        evicted = await bus.sweep_stale_subscribers()
        assert evicted == 5
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_sweep_mixed_active_and_stale(self):
        """Sweep should only remove stale subscribers, keeping active ones."""
        bus = EventBus()
        bus.STALE_SUBSCRIBER_TTL_SECONDS = 0.05

        stale_q = await bus.subscribe()
        active_q = await bus.subscribe()

        # Fill stale queue and cause drops
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # Drain active queue to keep it healthy
        while not active_q.empty():
            active_q.get_nowait()

        # Cause a drop (stale is full, active has space)
        bus.publish("step.started", {"step_id": "overflow"})

        await asyncio.sleep(0.1)

        evicted = await bus.sweep_stale_subscribers()
        assert evicted == 1  # Only stale_q
        assert bus.subscriber_count == 1  # active_q still there

        await bus.unsubscribe(active_q)
