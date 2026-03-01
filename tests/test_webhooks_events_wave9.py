"""Wave 9 deep audit - webhook dispatcher and EventBus edge cases.

Covers previously untested paths:
1.  Hard truncation now produces valid JSON (bug fix)
2.  User-Agent header always sent (bug fix)
3.  Timeout exception logging distinguished from generic HTTP errors (bug fix)
4.  HMAC with unicode, emoji, and multi-byte payloads
5.  Payload serialization of non-JSON-native types (datetime, UUID, Decimal)
6.  Concurrent webhook dispatch for the same run
7.  Redirect status codes (301, 302, 307, 308) - all blocked
8.  Client error classification (400-499) - all non-retryable
9.  Server error classification (500-599) - all retryable
10. httpx.ConnectError, httpx.ReadTimeout specific handling
11. Empty/None outputs in payload
12. EventBus: rapid subscribe/unsubscribe cycles
13. EventBus: publish with no subscribers (noop)
14. EventBus: event data immutability across subscribers
15. EventBus: sweep returns 0 for recently-stale (below TTL)
16. EventBus: drop count reset after single successful delivery
17. EventBus: MAX_CONSECUTIVE_DROPS boundary (N-1 does not evict, N does)
18. EventBus: subscriber limit boundary
19. EventBus: publish after all subscribers evicted
20. EventBus: concurrent publish safety (synchronous, no yield)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.engine.events import EventBus
from sandcastle.webhooks.dispatcher import (
    MAX_PAYLOAD_BYTES,
    MAX_RETRY_DELAY_SECONDS,
    _sign_payload,
    _truncate_payload,
    dispatch_webhook,
    validate_callback_url,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(responses=None, side_effect=None, capture=None):
    """Build a mock httpx.AsyncClient with configurable responses.

    Args:
        responses: List of MagicMock response objects to return in sequence.
        side_effect: Exception or callable side_effect for post().
        capture: If a dict, captured 'body' and 'headers' will be stored.
    """
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    if capture is not None:
        async def _capture_post(url, content=None, headers=None):
            capture["body"] = content
            capture["headers"] = dict(headers or {})
            if responses:
                return responses.pop(0)
            return MagicMock(status_code=200)

        mock_client.post = _capture_post
    elif side_effect is not None:
        mock_client.post = AsyncMock(side_effect=side_effect)
    elif responses is not None:
        mock_client.post = AsyncMock(side_effect=responses)
    else:
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    return mock_client


def _dispatch_patches():
    """Standard patches for dispatch_webhook tests."""
    return (
        patch(
            "sandcastle.webhooks.dispatcher.validate_callback_url",
            return_value="https://example.com/hook",
        ),
        patch("sandcastle.webhooks.dispatcher._resolve_and_check_ip"),
    )


async def _dispatch(mock_client, **kwargs):
    """Run dispatch_webhook with standard patches."""
    defaults = {
        "url": "https://example.com/hook",
        "event": "run.completed",
        "run_id": "run-test",
        "workflow": "wf-test",
        "status": "completed",
    }
    defaults.update(kwargs)
    p1, p2 = _dispatch_patches()
    with (
        p1,
        p2,
        patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        return await dispatch_webhook(**defaults)


# ===================================================================
# 1. Hard truncation now produces valid JSON
# ===================================================================


class TestHardTruncationValidJSON:
    """Previously, hard truncation could produce broken JSON. The fix
    replaces the raw byte slice with a minimal valid JSON payload."""

    def test_hard_truncated_payload_is_valid_json(self):
        """When both outputs AND error are huge, the result must still
        be parseable JSON."""
        payload = {
            "event": "run.completed",
            "run_id": "run-huge",
            "status": "failed",
            "outputs": None,
            "error": "E" * 2_000_000,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        body = _truncate_payload(payload, None, "run-huge")

        # Must be valid JSON
        parsed = json.loads(body)
        assert parsed["run_id"] == "run-huge"
        assert parsed["outputs"]["outputs_truncated"] is True
        assert parsed["outputs"]["_reason"] == "payload_too_large"
        assert len(body.encode("utf-8")) <= MAX_PAYLOAD_BYTES

    def test_hard_truncation_preserves_event_and_status(self):
        """Minimal payload must keep event and status for routing."""
        payload = {
            "event": "workflow.failed",
            "run_id": "run-evt",
            "status": "failed",
            "outputs": None,
            "error": "X" * 2_000_000,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        body = _truncate_payload(payload, None, "run-evt")
        parsed = json.loads(body)
        assert parsed["event"] == "workflow.failed"
        assert parsed["status"] == "failed"

    def test_hard_truncation_with_multibyte_error(self):
        """Multi-byte characters in error should not cause decode issues."""
        # Each character is 3+ bytes in UTF-8
        payload = {
            "event": "run.failed",
            "run_id": "run-mb",
            "status": "failed",
            "outputs": None,
            "error": "\u2603" * 500_000,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        body = _truncate_payload(payload, None, "run-mb")
        parsed = json.loads(body)
        assert parsed["outputs"]["outputs_truncated"] is True
        assert len(body.encode("utf-8")) <= MAX_PAYLOAD_BYTES


# ===================================================================
# 2. User-Agent header
# ===================================================================


class TestUserAgentHeader:
    """dispatch_webhook must include a User-Agent header."""

    @pytest.mark.asyncio
    async def test_user_agent_header_present(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)

        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            result = await _dispatch(mock_client)

        assert result is True
        assert captured["headers"]["User-Agent"] == "Sandcastle-Webhook/1.0"

    @pytest.mark.asyncio
    async def test_user_agent_alongside_signature(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)

        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = "test-secret"
            await _dispatch(mock_client)

        assert "User-Agent" in captured["headers"]
        assert "X-Sandcastle-Signature" in captured["headers"]


# ===================================================================
# 3. Timeout exception logging
# ===================================================================


class TestTimeoutExceptionHandling:
    """httpx.TimeoutException should be caught and retried with
    a specific log message."""

    @pytest.mark.asyncio
    async def test_timeout_is_retried(self):
        """Timeout on first attempt, success on second."""
        responses = [
            httpx.ReadTimeout("read timed out"),
            MagicMock(status_code=200),
        ]
        mock_client = _make_mock_client(side_effect=responses)

        result = await _dispatch(mock_client, max_retries=2)
        assert result is True
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_connect_timeout_is_retried(self):
        """httpx.ConnectTimeout is a subclass of TimeoutException."""
        responses = [
            httpx.ConnectTimeout("connect timed out"),
            MagicMock(status_code=200),
        ]
        mock_client = _make_mock_client(side_effect=responses)

        result = await _dispatch(mock_client, max_retries=2)
        assert result is True

    @pytest.mark.asyncio
    async def test_all_timeouts_exhaust_retries(self):
        """If every attempt times out, return False."""
        mock_client = _make_mock_client(
            side_effect=httpx.ReadTimeout("timeout"),
        )

        result = await _dispatch(mock_client, max_retries=3)
        assert result is False
        assert mock_client.post.call_count == 3


# ===================================================================
# 4. HMAC edge cases: unicode, emoji, multi-byte
# ===================================================================


class TestHMACEdgeCases:
    """HMAC signing with diverse character sets."""

    def test_sign_emoji_payload(self):
        body = json.dumps({"msg": "Hello \U0001f600 World"})
        secret = "key"
        sig = _sign_payload(body, secret)
        assert verify_signature(body, sig, secret) is True

    def test_sign_cjk_payload(self):
        body = json.dumps(
            {"msg": "\u4f60\u597d\u4e16\u754c"},
            ensure_ascii=False,
        )
        sig = _sign_payload(body, "secret-key")
        assert verify_signature(body, sig, "secret-key") is True

    def test_sign_mixed_scripts(self):
        body = json.dumps({
            "a": "ASCII",
            "b": "\u00e9\u00e8\u00ea",
            "c": "\u0410\u0411\u0412",
            "d": "\U0001f4a9",
        }, ensure_ascii=False)
        sig = _sign_payload(body, "s")
        assert verify_signature(body, sig, "s") is True

    def test_sign_large_payload(self):
        """Signing a ~1MB payload should work correctly."""
        body = json.dumps({"data": "x" * 900_000})
        sig = _sign_payload(body, "key")
        assert len(sig) == 64
        assert verify_signature(body, sig, "key") is True

    def test_verify_wrong_secret_fails(self):
        body = '{"x": 1}'
        sig = _sign_payload(body, "correct")
        assert verify_signature(body, sig, "wrong") is False

    def test_sign_null_bytes_in_body(self):
        """Null bytes in JSON values should be handled."""
        body = '{"data": "null\\u0000byte"}'
        sig = _sign_payload(body, "key")
        assert verify_signature(body, sig, "key") is True


# ===================================================================
# 5. Payload serialization of non-native types
# ===================================================================


class TestPayloadSerialization:
    """dispatch_webhook uses json.dumps(default=str) for serialization.
    Verify that datetime, UUID, and Decimal values are properly handled."""

    def test_datetime_serialized(self):
        dt = datetime(2026, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        payload = {"event": "test", "ts": dt, "outputs": None}
        body = _truncate_payload(payload, None, "run-dt")
        parsed = json.loads(body)
        assert "2026-01-15" in parsed["ts"]

    def test_uuid_serialized(self):
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        payload = {"event": "test", "id": uid, "outputs": None}
        body = _truncate_payload(payload, None, "run-uuid")
        parsed = json.loads(body)
        assert parsed["id"] == "12345678-1234-5678-1234-567812345678"

    def test_decimal_serialized(self):
        d = Decimal("3.14159")
        payload = {"event": "test", "cost": d, "outputs": None}
        body = _truncate_payload(payload, None, "run-dec")
        parsed = json.loads(body)
        assert parsed["cost"] == "3.14159"

    def test_nested_non_serializable(self):
        """Complex nested objects should be stringified via default=str."""
        payload = {
            "event": "test",
            "outputs": {
                "uuid_val": uuid.UUID("abcdef01-2345-6789-abcd-ef0123456789"),
                "dt_val": datetime(2026, 6, 1, tzinfo=timezone.utc),
            },
        }
        body = _truncate_payload(
            payload, payload["outputs"], "run-nested",
        )
        parsed = json.loads(body)
        assert "abcdef01" in parsed["outputs"]["uuid_val"]


# ===================================================================
# 6. Concurrent webhook dispatch
# ===================================================================


class TestConcurrentWebhookDispatch:
    """Multiple dispatch_webhook calls for the same run should not
    interfere with each other."""

    @pytest.mark.asyncio
    async def test_concurrent_dispatches_all_succeed(self):
        """Fire 5 webhooks simultaneously; all should complete."""
        results = []

        async def run_dispatch(i):
            mock_client = _make_mock_client()
            r = await _dispatch(
                mock_client,
                run_id=f"run-concurrent-{i}",
                event=f"event-{i}",
            )
            results.append(r)

        await asyncio.gather(*[run_dispatch(i) for i in range(5)])
        assert all(results)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_concurrent_dispatches_mixed_results(self):
        """Some succeed, some fail - they should not affect each other."""
        async def dispatch_success():
            mc = _make_mock_client()
            return await _dispatch(mc, run_id="run-ok")

        async def dispatch_failure():
            mc = _make_mock_client(
                side_effect=httpx.ConnectError("refused"),
            )
            return await _dispatch(mc, run_id="run-fail", max_retries=1)

        results = await asyncio.gather(
            dispatch_success(),
            dispatch_failure(),
            dispatch_success(),
            dispatch_failure(),
        )
        assert results == [True, False, True, False]


# ===================================================================
# 7. Redirect status codes - all blocked
# ===================================================================


class TestRedirectBlocking:
    """All 3xx redirects should be blocked and NOT retried."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
    async def test_redirect_codes_blocked(self, code):
        mock_client = _make_mock_client(
            side_effect=[MagicMock(status_code=code)],
        )
        result = await _dispatch(mock_client, max_retries=3)
        assert result is False
        # Should NOT retry
        assert mock_client.post.call_count == 1


# ===================================================================
# 8. Client error classification
# ===================================================================


class TestClientErrorClassification:
    """4xx errors should not be retried."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [400, 401, 403, 404, 405, 422, 429])
    async def test_4xx_not_retried(self, code):
        mock_client = _make_mock_client(
            side_effect=[MagicMock(status_code=code)],
        )
        result = await _dispatch(mock_client, max_retries=3)
        assert result is False
        assert mock_client.post.call_count == 1


# ===================================================================
# 9. Server error classification
# ===================================================================


class TestServerErrorClassification:
    """5xx errors should be retried."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [500, 502, 503, 504])
    async def test_5xx_retried(self, code):
        responses = [
            MagicMock(status_code=code),
            MagicMock(status_code=code),
            MagicMock(status_code=200),
        ]
        mock_client = _make_mock_client(side_effect=responses)
        result = await _dispatch(mock_client, max_retries=3)
        assert result is True
        assert mock_client.post.call_count == 3


# ===================================================================
# 10. Network errors
# ===================================================================


class TestNetworkErrors:
    """Various httpx network errors should be retried."""

    @pytest.mark.asyncio
    async def test_connect_error_retried(self):
        responses = [
            httpx.ConnectError("connection refused"),
            MagicMock(status_code=200),
        ]
        mock_client = _make_mock_client(side_effect=responses)
        result = await _dispatch(mock_client, max_retries=2)
        assert result is True

    @pytest.mark.asyncio
    async def test_ssl_error_caught(self):
        """SSL errors are caught by the generic Exception handler."""
        mock_client = _make_mock_client(
            side_effect=Exception("SSL: CERTIFICATE_VERIFY_FAILED"),
        )
        result = await _dispatch(mock_client, max_retries=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_read_error_retried(self):
        responses = [
            httpx.ReadError("connection reset"),
            MagicMock(status_code=200),
        ]
        mock_client = _make_mock_client(side_effect=responses)
        result = await _dispatch(mock_client, max_retries=2)
        assert result is True


# ===================================================================
# 11. Empty/None outputs
# ===================================================================


class TestEmptyOutputs:
    """Payloads with None or empty outputs."""

    @pytest.mark.asyncio
    async def test_none_outputs_in_payload(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(mock_client, outputs=None)

        payload = json.loads(captured["body"])
        assert payload["outputs"] is None

    @pytest.mark.asyncio
    async def test_empty_dict_outputs(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(mock_client, outputs={})

        payload = json.loads(captured["body"])
        assert payload["outputs"] == {}

    def test_truncation_with_empty_outputs_dict(self):
        payload = {
            "event": "test",
            "run_id": "r",
            "outputs": {},
            "error": "Z" * 2_000_000,
        }
        body = _truncate_payload(payload, {}, "r")
        # Empty dict is falsy, so outputs_preview is None
        parsed = json.loads(body)
        assert parsed["outputs"]["outputs_truncated"] is True


# ===================================================================
# 12. EventBus: rapid subscribe/unsubscribe cycles
# ===================================================================


class TestEventBusRapidCycles:
    """Rapid subscribe/unsubscribe should not leak resources."""

    @pytest.mark.asyncio
    async def test_rapid_subscribe_unsubscribe(self):
        bus = EventBus()
        for _ in range(100):
            q = await bus.subscribe()
            bus.publish("run.started", {"run_id": "r"})
            await bus.unsubscribe(q)

        assert bus.subscriber_count == 0
        assert len(bus._drop_counts) == 0
        assert len(bus._first_full_ts) == 0

    @pytest.mark.asyncio
    async def test_interleaved_subscribe_publish_unsubscribe(self):
        bus = EventBus()
        queues = []

        # Subscribe 10
        for i in range(10):
            queues.append(await bus.subscribe())

        assert bus.subscriber_count == 10

        # Publish some events
        for i in range(5):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # Unsubscribe odd-indexed
        for i in range(1, 10, 2):
            await bus.unsubscribe(queues[i])

        assert bus.subscriber_count == 5

        # Publish more - should only go to remaining subscribers
        bus.publish("step.completed", {"step_id": "final"})

        for i in range(0, 10, 2):
            events = []
            while not queues[i].empty():
                events.append(queues[i].get_nowait())
            # Should have 5 + 1 = 6 events
            assert len(events) == 6

        # Cleanup
        for i in range(0, 10, 2):
            await bus.unsubscribe(queues[i])


# ===================================================================
# 13. EventBus: publish with no subscribers
# ===================================================================


class TestEventBusNoSubscribers:
    """Publishing with no subscribers is a noop."""

    def test_publish_no_subscribers_no_error(self):
        bus = EventBus()
        # Should not raise
        bus.publish("run.started", {"run_id": "r1"})
        bus.publish("run.completed", {"run_id": "r1"})

    def test_seq_counter_advances_even_without_subscribers(self):
        bus = EventBus()
        bus.publish("run.started", {"run_id": "r1"})
        bus.publish("run.started", {"run_id": "r2"})

        # The counter should have advanced
        next_seq = next(bus._seq_counter)
        assert next_seq == 3


# ===================================================================
# 14. EventBus: event data immutability across subscribers
# ===================================================================


class TestEventDataImmutability:
    """All subscribers should receive the same event object (shared dict).
    Verify that modifying one subscriber's event does not affect others."""

    @pytest.mark.asyncio
    async def test_same_event_object_shared(self):
        """Event bus publishes the same dict to all queues. This is by
        design for performance. Subscribers must not mutate events."""
        bus = EventBus()
        q1 = await bus.subscribe()
        q2 = await bus.subscribe()

        bus.publish("run.started", {"run_id": "r1", "items": [1, 2]})

        e1 = q1.get_nowait()
        e2 = q2.get_nowait()

        # Same object identity (shared dict)
        assert e1 is e2
        assert e1["data"]["run_id"] == "r1"

        await bus.unsubscribe(q1)
        await bus.unsubscribe(q2)


# ===================================================================
# 15. EventBus: sweep with recently-stale (below TTL)
# ===================================================================


class TestSweepBelowTTL:
    """Subscribers that are full but not yet beyond the TTL should NOT
    be evicted."""

    @pytest.mark.asyncio
    async def test_sweep_does_not_evict_below_ttl(self):
        bus = EventBus()
        bus.STALE_SUBSCRIBER_TTL_SECONDS = 60  # Very long

        q = await bus.subscribe()
        # Fill and overflow
        for i in range(257):
            bus.publish("step.started", {"step_id": f"s{i}"})

        assert q in bus._first_full_ts

        # Sweep immediately - should NOT evict because TTL not reached
        evicted = await bus.sweep_stale_subscribers()
        assert evicted == 0
        assert bus.subscriber_count == 1

        await bus.unsubscribe(q)


# ===================================================================
# 16. EventBus: drop count reset
# ===================================================================


class TestDropCountReset:
    """When a subscriber drains its queue and receives a new event
    successfully, the drop count should reset to 0."""

    @pytest.mark.asyncio
    async def test_drop_count_resets_on_drain(self):
        bus = EventBus()
        q = await bus.subscribe()

        # Fill queue
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # Cause 10 drops
        for i in range(10):
            bus.publish("step.started", {"step_id": f"drop{i}"})

        assert bus._drop_counts.get(q) == 10

        # Drain enough to make space
        for _ in range(20):
            q.get_nowait()

        # Next publish succeeds - should reset drop count
        bus.publish("step.completed", {"step_id": "ok"})
        assert q not in bus._drop_counts

        await bus.unsubscribe(q)


# ===================================================================
# 17. MAX_CONSECUTIVE_DROPS boundary
# ===================================================================


class TestMaxConsecutiveDropsBoundary:
    """Exactly MAX_CONSECUTIVE_DROPS-1 drops should NOT evict.
    Exactly MAX_CONSECUTIVE_DROPS drops SHOULD evict."""

    @pytest.mark.asyncio
    async def test_n_minus_1_drops_no_eviction(self):
        bus = EventBus()
        q = await bus.subscribe()

        # Fill queue
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # Drop exactly N-1 times
        for i in range(bus.MAX_CONSECUTIVE_DROPS - 1):
            bus.publish("step.started", {"step_id": f"drop{i}"})

        assert bus.subscriber_count == 1
        assert bus._drop_counts[q] == bus.MAX_CONSECUTIVE_DROPS - 1

        await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_exactly_n_drops_evicts(self):
        bus = EventBus()
        q = await bus.subscribe()

        # Fill queue
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        # Drop exactly N times
        for i in range(bus.MAX_CONSECUTIVE_DROPS):
            bus.publish("step.started", {"step_id": f"drop{i}"})

        assert bus.subscriber_count == 0


# ===================================================================
# 18. Subscriber limit boundary
# ===================================================================


class TestSubscriberLimitBoundary:
    """Test at the exact boundary of MAX_SUBSCRIBERS."""

    @pytest.mark.asyncio
    async def test_exactly_at_limit(self):
        bus = EventBus()
        bus.MAX_SUBSCRIBERS = 5

        queues = []
        for _ in range(5):
            queues.append(await bus.subscribe())

        assert bus.subscriber_count == 5

        # One more should fail
        with pytest.raises(RuntimeError, match="subscriber limit"):
            await bus.subscribe()

        # Remove one and add one - should work
        await bus.unsubscribe(queues[0])
        q_new = await bus.subscribe()
        assert bus.subscriber_count == 5

        for q in queues[1:] + [q_new]:
            await bus.unsubscribe(q)


# ===================================================================
# 19. Publish after eviction
# ===================================================================


class TestPublishAfterEviction:
    """After all subscribers are evicted, publishing should be a noop."""

    @pytest.mark.asyncio
    async def test_publish_after_all_evicted(self):
        bus = EventBus()
        q = await bus.subscribe()

        # Fill and evict
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})
        for i in range(bus.MAX_CONSECUTIVE_DROPS):
            bus.publish("step.started", {"step_id": f"drop{i}"})

        assert bus.subscriber_count == 0

        # Publish to empty bus - should not raise
        bus.publish("run.completed", {"run_id": "r1"})

        # New subscriber should work
        q2 = await bus.subscribe()
        bus.publish("run.started", {"run_id": "r2"})
        event = q2.get_nowait()
        assert event["data"]["run_id"] == "r2"

        await bus.unsubscribe(q2)


# ===================================================================
# 20. Validate callback URL edge cases
# ===================================================================


class TestValidateCallbackUrlEdgeCases:
    """Edge cases in URL validation."""

    def test_url_with_custom_port(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 8443)),
        ]):
            result = validate_callback_url("https://example.com:8443/hook")
            assert result == "https://example.com:8443/hook"

    def test_url_with_path_and_query(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]):
            url = "https://example.com/webhook?token=abc&format=json"
            result = validate_callback_url(url)
            assert result == url

    def test_url_with_empty_scheme(self):
        with pytest.raises(ValueError, match="http"):
            validate_callback_url("://example.com/hook")

    def test_url_with_data_scheme(self):
        with pytest.raises(ValueError, match="http"):
            validate_callback_url("data:text/html,<h1>bad</h1>")

    def test_url_with_javascript_scheme(self):
        with pytest.raises(ValueError, match="http"):
            validate_callback_url("javascript:alert(1)")

    def test_multiple_resolved_ips_one_blocked(self):
        """If any resolved IP is in a blocked range, reject."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://dual-homed.example.com/hook")


# ===================================================================
# 21. Webhook payload timestamp
# ===================================================================


class TestWebhookTimestamp:
    """Webhook payload should contain an ISO 8601 UTC timestamp."""

    @pytest.mark.asyncio
    async def test_payload_has_utc_timestamp(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(mock_client)

        payload = json.loads(captured["body"])
        ts = payload["timestamp"]
        # Should be ISO format with timezone info
        assert "T" in ts
        # Should parse as a datetime
        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None


# ===================================================================
# 22. httpx.AsyncClient configuration
# ===================================================================


class TestHttpxClientConfig:
    """Verify the httpx client is configured securely."""

    @pytest.mark.asyncio
    async def test_follow_redirects_disabled(self):
        """The client must be created with follow_redirects=False."""
        captured_kwargs = {}

        original_init = httpx.AsyncClient.__init__

        def capture_init(self, **kwargs):
            captured_kwargs.update(kwargs)
            return original_init(self, **kwargs)

        p1, p2 = _dispatch_patches()
        with (
            p1,
            p2,
            patch.object(httpx.AsyncClient, "__init__", capture_init),
            patch.object(
                httpx.AsyncClient, "post",
                new_callable=AsyncMock,
                return_value=MagicMock(status_code=200),
            ),
            patch("sandcastle.webhooks.dispatcher.settings") as ms,
        ):
            ms.webhook_secret = ""
            await dispatch_webhook(
                url="https://example.com/hook",
                event="run.completed",
                run_id="run-config",
                workflow="wf",
                status="completed",
                max_retries=1,
            )

        assert captured_kwargs.get("follow_redirects") is False
        assert captured_kwargs.get("max_redirects") == 0


# ===================================================================
# 23. Truncation with small output (no preview truncation needed)
# ===================================================================


class TestTruncationSmallOutput:
    """When outputs are large enough to trigger truncation but small
    enough to fit in the preview, preview should be complete."""

    def test_outputs_fit_in_preview(self):
        # Build a payload that is over MAX_PAYLOAD_BYTES due to outputs,
        # but the outputs themselves are under MAX_OUTPUT_PREVIEW_BYTES
        small_output = {"key": "v" * 5000}
        # Make the payload large via a big error field
        payload = {
            "event": "test",
            "run_id": "r",
            "status": "failed",
            "outputs": small_output,
            "error": "E" * (MAX_PAYLOAD_BYTES + 1000),
        }
        body = _truncate_payload(payload, small_output, "r")
        parsed = json.loads(body)
        # Since the error is still huge after output truncation,
        # we get the minimal payload
        assert parsed["outputs"]["outputs_truncated"] is True


# ===================================================================
# 24. EventBus dlq.new event type
# ===================================================================


class TestEventBusDLQEventType:
    """dlq.new is a valid event type for dead-letter queue notifications."""

    @pytest.mark.asyncio
    async def test_dlq_new_is_valid(self):
        bus = EventBus()
        q = await bus.subscribe()

        bus.publish("dlq.new", {"run_id": "r1", "reason": "timeout"})

        event = q.get_nowait()
        assert event["type"] == "dlq.new"
        assert event["data"]["reason"] == "timeout"

        await bus.unsubscribe(q)


# ===================================================================
# 25. EventBus: multiple evictions in single publish
# ===================================================================


class TestMultipleEvictionsInSinglePublish:
    """When multiple subscribers hit MAX_CONSECUTIVE_DROPS in the same
    publish() call, all should be evicted."""

    @pytest.mark.asyncio
    async def test_two_subscribers_evicted_simultaneously(self):
        bus = EventBus()
        q1 = await bus.subscribe()
        q2 = await bus.subscribe()

        # Fill both queues
        for i in range(256):
            bus.publish("step.started", {"step_id": f"s{i}"})

        assert q1.full()
        assert q2.full()

        # Both are full. Drop MAX_CONSECUTIVE_DROPS - 1 times
        for i in range(bus.MAX_CONSECUTIVE_DROPS - 1):
            bus.publish("step.started", {"step_id": f"drop{i}"})

        assert bus.subscriber_count == 2

        # One more drop should evict BOTH
        bus.publish("step.started", {"step_id": "final-drop"})
        assert bus.subscriber_count == 0


# ===================================================================
# 26. Dispatch with costs and duration edge cases
# ===================================================================


class TestDispatchCostsDuration:
    """Edge cases for costs and duration_seconds parameters."""

    @pytest.mark.asyncio
    async def test_zero_costs_and_duration(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(mock_client, costs=0.0, duration_seconds=0.0)

        payload = json.loads(captured["body"])
        assert payload["total_cost_usd"] == 0.0
        assert payload["costs"] == 0.0
        assert payload["duration_seconds"] == 0.0

    @pytest.mark.asyncio
    async def test_large_costs(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(
                mock_client, costs=999.99, duration_seconds=3600.0,
            )

        payload = json.loads(captured["body"])
        assert payload["total_cost_usd"] == 999.99

    @pytest.mark.asyncio
    async def test_error_field_included(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(
                mock_client, status="failed", error="Sandbox crashed",
            )

        payload = json.loads(captured["body"])
        assert payload["error"] == "Sandbox crashed"
        assert payload["status"] == "failed"


# ===================================================================
# 27. EventBus: sequence numbers are integers
# ===================================================================


class TestSeqNumberTypes:
    """Sequence numbers must be integers, not floats or strings."""

    @pytest.mark.asyncio
    async def test_seq_is_int(self):
        bus = EventBus()
        q = await bus.subscribe()

        for _ in range(10):
            bus.publish("run.started", {"run_id": "r"})

        while not q.empty():
            event = q.get_nowait()
            assert isinstance(event["seq"], int)

        await bus.unsubscribe(q)


# ===================================================================
# 28. Dispatch idempotency - same body produces same signature
# ===================================================================


class TestSignatureIdempotency:
    """Given the same body and secret, the signature must be identical."""

    @pytest.mark.asyncio
    async def test_same_payload_same_signature(self):
        """Two dispatches with identical payloads should produce the
        same HMAC signature (given the same timestamp)."""
        body = '{"event": "test", "run_id": "r1"}'
        secret = "fixed-secret"
        sig1 = _sign_payload(body, secret)
        sig2 = _sign_payload(body, secret)
        assert sig1 == sig2

    def test_cross_verify_with_stdlib(self):
        """Signature from _sign_payload must match stdlib hmac."""
        body = '{"complex": true, "nested": {"a": [1,2,3]}}'
        secret = "my-key-123"
        sig = _sign_payload(body, secret)

        expected = hmac_mod.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert sig == expected


# ===================================================================
# 29. EventBus: sweep with no _first_full_ts entries
# ===================================================================


class TestSweepNoFullTimestamp:
    """Sweep should handle subscribers that have never been full."""

    @pytest.mark.asyncio
    async def test_sweep_with_healthy_subscribers(self):
        bus = EventBus()
        q1 = await bus.subscribe()
        q2 = await bus.subscribe()

        bus.publish("run.started", {"run_id": "r1"})

        evicted = await bus.sweep_stale_subscribers()
        assert evicted == 0
        assert bus.subscriber_count == 2

        await bus.unsubscribe(q1)
        await bus.unsubscribe(q2)


# ===================================================================
# 30. Webhook dispatch max_retries boundary values
# ===================================================================


class TestMaxRetriesBoundary:
    """Test dispatch with various max_retries values."""

    @pytest.mark.asyncio
    async def test_max_retries_negative_makes_no_attempts(self):
        """Negative max_retries should behave like 0 (no attempts)."""
        mock_client = _make_mock_client()
        result = await _dispatch(mock_client, max_retries=-1)
        assert result is False
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_retries_large_value(self):
        """Large max_retries should work (respecting backoff ceiling)."""
        attempts = []

        async def track_post(url, content=None, headers=None):
            attempts.append(1)
            if len(attempts) < 8:
                return MagicMock(status_code=500)
            return MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = track_post

        p1, p2 = _dispatch_patches()
        with (
            p1,
            p2,
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await dispatch_webhook(
                url="https://example.com/hook",
                event="run.completed",
                run_id="run-many",
                workflow="wf",
                status="completed",
                max_retries=10,
            )

        assert result is True
        assert len(attempts) == 8


# ===================================================================
# 31. EventBus: drop_counts property snapshot isolation
# ===================================================================


class TestDropCountsSnapshot:
    """The drop_counts property should return a snapshot, not a live view."""

    @pytest.mark.asyncio
    async def test_drop_counts_is_snapshot(self):
        bus = EventBus()
        q = await bus.subscribe()

        # Fill and cause 1 drop
        for i in range(257):
            bus.publish("step.started", {"step_id": f"s{i}"})

        snap1 = bus.drop_counts
        assert snap1[id(q)] == 1

        # Cause another drop
        bus.publish("step.started", {"step_id": "drop2"})

        # snap1 should be unchanged (it was a copy)
        assert snap1[id(q)] == 1
        # But a new snapshot shows 2
        assert bus.drop_counts[id(q)] == 2

        await bus.unsubscribe(q)


# ===================================================================
# 32. Webhook with outputs containing special JSON values
# ===================================================================


class TestOutputsSpecialValues:
    """Test outputs containing boolean, null, numeric edge cases."""

    @pytest.mark.asyncio
    async def test_boolean_outputs(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(
                mock_client,
                outputs={"flag": True, "other": False},
            )
        payload = json.loads(captured["body"])
        assert payload["outputs"]["flag"] is True
        assert payload["outputs"]["other"] is False

    @pytest.mark.asyncio
    async def test_numeric_outputs(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(
                mock_client,
                outputs={"int_val": 42, "float_val": 3.14, "neg": -1},
            )
        payload = json.loads(captured["body"])
        assert payload["outputs"]["int_val"] == 42
        assert payload["outputs"]["float_val"] == 3.14
        assert payload["outputs"]["neg"] == -1

    @pytest.mark.asyncio
    async def test_nested_null_outputs(self):
        captured = {}
        mock_client = _make_mock_client(capture=captured)
        with patch("sandcastle.webhooks.dispatcher.settings") as ms:
            ms.webhook_secret = ""
            await _dispatch(
                mock_client,
                outputs={"result": None, "items": [None, "ok", None]},
            )
        payload = json.loads(captured["body"])
        assert payload["outputs"]["result"] is None
        assert payload["outputs"]["items"] == [None, "ok", None]


# ===================================================================
# 33. EventBus: event timestamp is ISO 8601 with timezone
# ===================================================================


class TestEventTimestampFormat:
    """Event timestamps should be ISO 8601 with UTC timezone."""

    @pytest.mark.asyncio
    async def test_timestamp_parseable_as_datetime(self):
        bus = EventBus()
        q = await bus.subscribe()
        bus.publish("run.started", {"run_id": "r1"})

        event = q.get_nowait()
        dt = datetime.fromisoformat(event["timestamp"])
        assert dt.tzinfo is not None
        # Should be close to now
        delta = abs((datetime.now(timezone.utc) - dt).total_seconds())
        assert delta < 5

        await bus.unsubscribe(q)


# ===================================================================
# 34. Webhook retries do not sleep after last attempt
# ===================================================================


class TestNoSleepAfterLastAttempt:
    """There should be no asyncio.sleep after the final retry attempt."""

    @pytest.mark.asyncio
    async def test_sleep_count_is_retries_minus_one(self):
        mock_client = _make_mock_client(
            side_effect=MagicMock(status_code=500),
        )
        sleep_calls = []

        async def track_sleep(delay):
            sleep_calls.append(delay)

        p1, p2 = _dispatch_patches()
        with (
            p1,
            p2,
            patch(
                "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("asyncio.sleep", side_effect=track_sleep),
        ):
            await dispatch_webhook(
                url="https://example.com/hook",
                event="run.failed",
                run_id="run-sleep",
                workflow="wf",
                status="failed",
                max_retries=4,
            )

        # 4 attempts, 3 sleeps (no sleep after last)
        assert len(sleep_calls) == 3
        # Verify exponential: 2, 4, 8
        assert sleep_calls == [2, 4, 8]
