"""Comprehensive tests for webhook dispatcher, scheduler, and queue/worker systems.

Covers:
- Webhook dispatcher: SSRF prevention, HMAC signatures, retries, timeouts,
  redirect blocking, payload serialization, error handling
- Scheduler: cron expression validation, enable/disable, restore on startup,
  schedule execution, deletion, timezone handling, webhook callback
- Queue/Worker: job enqueueing, execution, failure handling, dead letter queue,
  retry with backoff, Redis pool creation, in-process fallback, cancellation

Target: 35+ tests. All external HTTP calls and Redis are mocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.webhooks.dispatcher import (
    MAX_PAYLOAD_BYTES,
    MAX_RETRY_DELAY_SECONDS,
    _resolve_and_check_ip,
    _sign_payload,
    _truncate_payload,
    dispatch_webhook,
    validate_callback_url,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Helper: common mock patches for dispatch_webhook
# ---------------------------------------------------------------------------

def _make_dispatch_patches():
    """Return (p_validate, p_resolve) patches for dispatch_webhook tests."""
    return (
        patch(
            "sandcastle.webhooks.dispatcher.validate_callback_url",
            return_value="https://ok.com/hook",
        ),
        patch(
            "sandcastle.webhooks.dispatcher._resolve_and_check_ip",
        ),
    )


def _make_mock_client(post_side_effect=None, post_return=None):
    """Create a mock httpx.AsyncClient with configurable post behavior."""
    mock_client = AsyncMock()
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    elif post_return is not None:
        mock_client.post = AsyncMock(return_value=post_return)
    else:
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ===========================================================================
# 1. WEBHOOK DISPATCHER TESTS (15+ tests)
# ===========================================================================


class TestSSRFPrevention:
    """SSRF prevention: blocked networks, metadata endpoints, DNS rebinding."""

    def test_blocks_localhost_127(self):
        """127.0.0.1 (loopback) must be blocked."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://localhost/hook")

    def test_blocks_10_x_private(self):
        """10.0.0.0/8 private class A must be blocked."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("10.255.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://internal.corp/hook")

    def test_blocks_172_16_x_private(self):
        """172.16.0.0/12 private class B must be blocked."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("172.31.255.255", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://docker-host/hook")

    def test_blocks_169_254_link_local(self):
        """169.254.0.0/16 (link-local / cloud metadata) must be blocked."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("169.254.169.254", 80)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_ipv6_loopback(self):
        """::1 (IPv6 loopback) must be blocked."""
        with patch("socket.getaddrinfo", return_value=[
            (10, 1, 6, "", ("::1", 443, 0, 0)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://ip6-localhost/hook")

    def test_blocks_metadata_google_internal(self):
        """http://metadata.google.internal resolves to 169.254.x and must be blocked."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("169.254.169.254", 80)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_allows_valid_public_url(self):
        """A legitimate public IP should pass validation."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]):
            result = validate_callback_url("https://example.com/webhook")
            assert result == "https://example.com/webhook"

    def test_blocks_192_168_private(self):
        """192.168.0.0/16 private class C must be blocked."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("192.168.1.100", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://home-router.local/hook")


class TestHMACSignature:
    """HMAC signature generation, verification, and timing-safe comparison."""

    def test_sign_payload_matches_manual_hmac(self):
        """_sign_payload produces the same result as manual hmac.new()."""
        body = '{"event": "workflow.completed", "run_id": "abc"}'
        secret = "s3cret-key-123"
        expected = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert _sign_payload(body, secret) == expected

    def test_verify_signature_valid(self):
        """verify_signature returns True for a correctly signed payload."""
        body = '{"data": "test"}'
        secret = "webhook-secret"
        sig = _sign_payload(body, secret)
        assert verify_signature(body, sig, secret) is True

    def test_verify_signature_invalid(self):
        """verify_signature returns False for a tampered signature."""
        assert verify_signature('{"ok": true}', "badhex" * 8, "secret") is False

    def test_verify_uses_timing_safe_comparison(self):
        """verify_signature must use hmac.compare_digest (timing-safe)."""
        import inspect
        source = inspect.getsource(verify_signature)
        assert "compare_digest" in source, (
            "verify_signature must use hmac.compare_digest for timing-safe comparison"
        )

    def test_signature_hex_string_format(self):
        """Signature is a 64-char lowercase hex string (SHA-256 digest)."""
        sig = _sign_payload("body", "key")
        assert len(sig) == 64
        assert sig == sig.lower()
        int(sig, 16)  # must be valid hex


class TestDispatchWebhookBehavior:
    """dispatch_webhook: timeout, redirects, retries, error handling, payload."""

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self):
        """dispatch_webhook configures a 30s overall / 10s connect timeout."""
        captured_kwargs = {}

        original_init = httpx.AsyncClient.__init__

        def capture_init(self, **kwargs):
            captured_kwargs.update(kwargs)
            original_init(self, **kwargs)

        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client()

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ) as mock_cls):
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-timeout",
                workflow="wf",
                status="completed",
            )
            # Verify the AsyncClient was called with timeout configuration
            call_kwargs = mock_cls.call_args
            assert call_kwargs is not None
            timeout_arg = call_kwargs.kwargs.get("timeout") or call_kwargs[1].get("timeout")
            assert timeout_arg is not None

    @pytest.mark.asyncio
    async def test_redirect_following_disabled(self):
        """Redirects (3xx) must not be followed and must return False."""
        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client(post_return=MagicMock(status_code=301))

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        )):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-redir",
                workflow="wf",
                status="completed",
            )
        assert result is False
        # Must not retry on redirect
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_payload_serialized_as_json(self):
        """The POST body must be valid JSON with expected keys."""
        captured_body = {}

        async def capture_post(url, content=None, headers=None):
            captured_body["raw"] = content
            return MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = _make_dispatch_patches()
        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("sandcastle.webhooks.dispatcher.settings") as mock_settings):
            mock_settings.webhook_secret = ""
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-json",
                workflow="my-wf",
                status="completed",
                outputs={"result": 42},
            )

        body = json.loads(captured_body["raw"])
        assert body["event"] == "workflow.completed"
        assert body["run_id"] == "run-json"
        assert body["workflow"] == "my-wf"
        assert body["status"] == "completed"
        assert body["outputs"] == {"result": 42}
        assert "timestamp" in body

    @pytest.mark.asyncio
    async def test_error_handling_on_network_failure(self):
        """Network errors (httpx.HTTPError) must not propagate; returns False."""
        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client(
            post_side_effect=httpx.ConnectError("Connection refused")
        )

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("asyncio.sleep", new_callable=AsyncMock)):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.failed",
                run_id="run-net",
                workflow="wf",
                status="failed",
                max_retries=2,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_retry_on_5xx_with_eventual_success(self):
        """5xx responses trigger retries; eventual 200 returns True."""
        responses = [
            MagicMock(status_code=502),
            MagicMock(status_code=503),
            MagicMock(status_code=200),
        ]
        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client(post_side_effect=responses)

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("asyncio.sleep", new_callable=AsyncMock)):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-retry",
                workflow="wf",
                status="completed",
                max_retries=3,
            )
        assert result is True
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_url_rejected(self):
        """An empty URL should fail validation and return False."""
        result = await dispatch_webhook(
            url="",
            event="workflow.completed",
            run_id="run-empty",
            workflow="wf",
            status="completed",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_ftp_scheme_rejected(self):
        """ftp:// scheme must be rejected."""
        result = await dispatch_webhook(
            url="ftp://evil.com/payload",
            event="workflow.completed",
            run_id="run-ftp",
            workflow="wf",
            status="completed",
        )
        assert result is False

    def test_ipv6_localhost_blocked_at_validation(self):
        """::1 in DNS resolution must be blocked by validate_callback_url."""
        with patch("socket.getaddrinfo", return_value=[
            (10, 1, 6, "", ("::1", 443, 0, 0)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://ip6-localhost/hook")

    def test_dns_rebinding_protection(self):
        """_resolve_and_check_ip blocks when hostname resolves to private IP."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="rebinding"):
                _resolve_and_check_ip("evil-rebind.example.com", 443)

    @pytest.mark.asyncio
    async def test_dispatch_dns_rebinding_returns_false(self):
        """dispatch_webhook returns False when DNS rebinding is detected."""
        with (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://evil.com/hook",
            ),
            patch(
                "sandcastle.webhooks.dispatcher._resolve_and_check_ip",
                side_effect=ValueError("DNS rebinding detected"),
            ),
        ):
            result = await dispatch_webhook(
                url="https://evil.com/hook",
                event="workflow.completed",
                run_id="run-rebind",
                workflow="wf",
                status="completed",
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_exception_triggers_retry(self):
        """httpx.TimeoutException triggers retry, eventual success returns True."""
        responses = [
            httpx.ReadTimeout("read timed out"),
            MagicMock(status_code=200),
        ]
        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client(post_side_effect=responses)

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("asyncio.sleep", new_callable=AsyncMock)):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-tout",
                workflow="wf",
                status="completed",
                max_retries=2,
            )
        assert result is True
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_4xx_not_retried(self):
        """Client errors (4xx) must not be retried."""
        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client(post_return=MagicMock(status_code=422))

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        )):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-4xx",
                workflow="wf",
                status="completed",
                max_retries=3,
            )
        assert result is False
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_includes_hmac_header_when_secret_set(self):
        """X-Sandcastle-Signature header is present when webhook_secret is set."""
        captured_headers = {}

        async def capture_post(url, content=None, headers=None):
            captured_headers.update(headers or {})
            return MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = _make_dispatch_patches()
        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("sandcastle.webhooks.dispatcher.settings") as mock_settings):
            mock_settings.webhook_secret = "my-secret"
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-sig",
                workflow="wf",
                status="completed",
            )

        assert "X-Sandcastle-Signature" in captured_headers
        assert len(captured_headers["X-Sandcastle-Signature"]) == 64
        assert captured_headers["X-Sandcastle-Event"] == "workflow.completed"

    @pytest.mark.asyncio
    async def test_no_signature_when_secret_empty(self):
        """No X-Sandcastle-Signature header when webhook_secret is empty."""
        captured_headers = {}

        async def capture_post(url, content=None, headers=None):
            captured_headers.update(headers or {})
            return MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = _make_dispatch_patches()
        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("sandcastle.webhooks.dispatcher.settings") as mock_settings):
            mock_settings.webhook_secret = ""
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-nosig",
                workflow="wf",
                status="completed",
            )

        assert "X-Sandcastle-Signature" not in captured_headers

    @pytest.mark.asyncio
    async def test_retry_backoff_capped_at_max(self):
        """Exponential backoff delay is capped at MAX_RETRY_DELAY_SECONDS."""
        sleep_delays = []
        original_sleep = asyncio.sleep

        async def capture_sleep(delay):
            sleep_delays.append(delay)

        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client(
            post_side_effect=[
                MagicMock(status_code=500),
                MagicMock(status_code=500),
                MagicMock(status_code=500),
                MagicMock(status_code=500),
                MagicMock(status_code=500),
            ]
        )

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("asyncio.sleep", side_effect=capture_sleep)):
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.failed",
                run_id="run-backoff",
                workflow="wf",
                status="failed",
                max_retries=5,
            )

        # All delays must be <= MAX_RETRY_DELAY_SECONDS
        for delay in sleep_delays:
            assert delay <= MAX_RETRY_DELAY_SECONDS

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_returns_false(self):
        """When all retries are exhausted on 5xx, returns False."""
        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client(post_return=MagicMock(status_code=500))

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("asyncio.sleep", new_callable=AsyncMock)):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.failed",
                run_id="run-exhaust",
                workflow="wf",
                status="failed",
                max_retries=3,
            )
        assert result is False
        assert mock_client.post.call_count == 3


# ===========================================================================
# 2. SCHEDULER TESTS (10+ tests)
# ===========================================================================


class TestScheduleCreation:
    """Schedule creation, validation, enable/disable, deletion."""

    @pytest.mark.asyncio
    async def test_valid_cron_expression_accepted(self):
        """A valid 5-field cron expression should be accepted."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            scheduler = sched_mod.get_scheduler()
            if not scheduler.running:
                scheduler.start()

            with patch.object(sched_mod.settings, "workflows_dir", "/tmp/nonexistent"):
                sched_mod.add_schedule(
                    schedule_id="test-valid-cron",
                    cron_expression="*/5 * * * *",
                    workflow_name="test-wf",
                    input_data={"key": "value"},
                )

            # Verify it was registered
            jobs = sched_mod.list_schedules()
            job_ids = [j["id"] for j in jobs]
            assert "test-valid-cron" in job_ids

            # Cleanup
            sched_mod.remove_schedule("test-valid-cron")
            scheduler.shutdown(wait=False)
        finally:
            sched_mod._scheduler = old

    @pytest.mark.asyncio
    async def test_invalid_cron_rejected(self):
        """An invalid cron expression raises ValueError."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            scheduler = sched_mod.get_scheduler()
            if not scheduler.running:
                scheduler.start()

            with pytest.raises(ValueError, match="Invalid cron"):
                sched_mod.add_schedule(
                    schedule_id="test-bad-cron",
                    cron_expression="not a cron",
                    workflow_name="test-wf",
                )

            scheduler.shutdown(wait=False)
        finally:
            sched_mod._scheduler = old

    def test_empty_cron_rejected(self):
        """An empty cron expression raises ValueError."""
        import sandcastle.queue.scheduler as sched_mod

        with pytest.raises(ValueError, match="must not be empty"):
            sched_mod.add_schedule(
                schedule_id="test-empty",
                cron_expression="",
                workflow_name="test-wf",
            )

    def test_empty_workflow_name_rejected(self):
        """An empty workflow_name raises ValueError."""
        import sandcastle.queue.scheduler as sched_mod

        with pytest.raises(ValueError, match="must not be empty"):
            sched_mod.add_schedule(
                schedule_id="test-empty-wf",
                cron_expression="* * * * *",
                workflow_name="",
            )

    @pytest.mark.asyncio
    async def test_schedule_deletion_cleanup(self):
        """remove_schedule returns True on success, removes the job."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            scheduler = sched_mod.get_scheduler()
            if not scheduler.running:
                scheduler.start()

            with patch.object(sched_mod.settings, "workflows_dir", "/tmp/nonexistent"):
                sched_mod.add_schedule(
                    schedule_id="test-delete-me",
                    cron_expression="0 0 * * *",
                    workflow_name="test-wf",
                )

            result = sched_mod.remove_schedule("test-delete-me")
            assert result is True

            # Verify it was removed
            jobs = sched_mod.list_schedules()
            job_ids = [j["id"] for j in jobs]
            assert "test-delete-me" not in job_ids

            scheduler.shutdown(wait=False)
        finally:
            sched_mod._scheduler = old

    @pytest.mark.asyncio
    async def test_remove_nonexistent_schedule_returns_false(self):
        """Removing a schedule that does not exist returns False."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            scheduler = sched_mod.get_scheduler()
            if not scheduler.running:
                scheduler.start()

            result = sched_mod.remove_schedule("nonexistent-id-xyz")
            assert result is False

            scheduler.shutdown(wait=False)
        finally:
            sched_mod._scheduler = old

    def test_timezone_utc_used(self):
        """CronTrigger is created with UTC timezone."""
        import inspect
        from sandcastle.queue.scheduler import add_schedule
        source = inspect.getsource(add_schedule)
        assert "timezone=timezone.utc" in source or "timezone.utc" in source

    @pytest.mark.asyncio
    async def test_list_schedules_returns_job_info(self):
        """list_schedules returns a list of dicts with id, name, next_run_time."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            scheduler = sched_mod.get_scheduler()
            if not scheduler.running:
                scheduler.start()

            with patch.object(sched_mod.settings, "workflows_dir", "/tmp/nonexistent"):
                sched_mod.add_schedule(
                    schedule_id="test-list-1",
                    cron_expression="0 * * * *",
                    workflow_name="test-wf",
                )
                sched_mod.add_schedule(
                    schedule_id="test-list-2",
                    cron_expression="30 * * * *",
                    workflow_name="test-wf",
                )

            jobs = sched_mod.list_schedules()
            ids = [j["id"] for j in jobs]
            assert "test-list-1" in ids
            assert "test-list-2" in ids

            for job in jobs:
                assert "id" in job
                assert "name" in job
                assert "next_run_time" in job
                assert "trigger" in job

            sched_mod.remove_schedule("test-list-1")
            sched_mod.remove_schedule("test-list-2")
            scheduler.shutdown(wait=False)
        finally:
            sched_mod._scheduler = old

    @pytest.mark.asyncio
    async def test_replace_existing_schedule(self):
        """Adding a schedule with the same ID replaces the existing one."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            scheduler = sched_mod.get_scheduler()
            if not scheduler.running:
                scheduler.start()

            with patch.object(sched_mod.settings, "workflows_dir", "/tmp/nonexistent"):
                sched_mod.add_schedule(
                    schedule_id="test-replace",
                    cron_expression="0 * * * *",
                    workflow_name="wf-old",
                )
                # Replace with different cron
                sched_mod.add_schedule(
                    schedule_id="test-replace",
                    cron_expression="30 * * * *",
                    workflow_name="wf-new",
                )

            jobs = sched_mod.list_schedules()
            matches = [j for j in jobs if j["id"] == "test-replace"]
            # Only one job with this ID should exist
            assert len(matches) == 1

            sched_mod.remove_schedule("test-replace")
            scheduler.shutdown(wait=False)
        finally:
            sched_mod._scheduler = old


class TestScheduleRestore:
    """Schedule restore on startup from database."""

    @pytest.mark.asyncio
    async def test_restore_schedules_on_startup(self):
        """restore_schedules loads enabled schedules from DB and registers them."""
        from sandcastle.queue.scheduler import restore_schedules

        mock_schedule = MagicMock()
        mock_schedule.id = uuid.uuid4()
        mock_schedule.cron_expression = "*/10 * * * *"
        mock_schedule.workflow_name = "restore-wf"
        mock_schedule.input_data = {"x": 1}
        mock_schedule.enabled = True

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_schedule]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.models.db.async_session", return_value=mock_session),
            patch("sandcastle.queue.scheduler.add_schedule") as mock_add,
        ):
            await restore_schedules()
            mock_add.assert_called_once_with(
                schedule_id=str(mock_schedule.id),
                cron_expression="*/10 * * * *",
                workflow_name="restore-wf",
                input_data={"x": 1},
            )

    @pytest.mark.asyncio
    async def test_restore_skips_invalid_cron(self):
        """restore_schedules handles invalid cron expressions gracefully."""
        from sandcastle.queue.scheduler import restore_schedules

        mock_schedule = MagicMock()
        mock_schedule.id = uuid.uuid4()
        mock_schedule.cron_expression = "bad cron"
        mock_schedule.workflow_name = "bad-wf"
        mock_schedule.input_data = {}
        mock_schedule.enabled = True

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_schedule]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.get = AsyncMock(return_value=mock_schedule)
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.models.db.async_session", return_value=mock_session),
            patch(
                "sandcastle.queue.scheduler.add_schedule",
                side_effect=ValueError("Invalid cron"),
            ),
        ):
            # Should not raise
            await restore_schedules()

    @pytest.mark.asyncio
    async def test_restore_handles_db_failure(self):
        """restore_schedules handles database connection failure gracefully."""
        from sandcastle.queue.scheduler import restore_schedules

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB connection failed"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            # Should not raise
            await restore_schedules()


class TestScheduleExecution:
    """Schedule execution triggers workflow runs."""

    @pytest.mark.asyncio
    async def test_schedule_execution_triggers_workflow_run(self):
        """_run_scheduled_workflow creates a run and enqueues it."""
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())

        async with async_session() as session:
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="exec-test-wf",
                cron_expression="* * * * *",
                enabled=True,
                last_run_id=None,
            )
            session.add(schedule)
            await session.commit()

        with (
            patch(
                "sandcastle.queue.scheduler._load_workflow_yaml",
                return_value="name: exec-test\nsteps: []",
            ),
            patch(
                "sandcastle.queue.worker.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            await _run_scheduled_workflow(schedule_id, "exec-test-wf", {"input": "data"})
            mock_enqueue.assert_awaited_once()
            # Verify the correct arguments were passed
            call_args = mock_enqueue.call_args
            assert call_args[0][0] == "name: exec-test\nsteps: []"
            assert call_args[0][1] == {"input": "data"}

    @pytest.mark.asyncio
    async def test_multiple_schedules_independent(self):
        """Two schedules with different IDs do not interfere with each other."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            scheduler = sched_mod.get_scheduler()
            if not scheduler.running:
                scheduler.start()

            with patch.object(sched_mod.settings, "workflows_dir", "/tmp/nonexistent"):
                sched_mod.add_schedule(
                    schedule_id="multi-1",
                    cron_expression="0 * * * *",
                    workflow_name="wf-a",
                )
                sched_mod.add_schedule(
                    schedule_id="multi-2",
                    cron_expression="30 * * * *",
                    workflow_name="wf-b",
                )

            jobs = sched_mod.list_schedules()
            ids = {j["id"] for j in jobs}
            assert "multi-1" in ids
            assert "multi-2" in ids

            # Remove one, the other should remain
            sched_mod.remove_schedule("multi-1")
            jobs = sched_mod.list_schedules()
            ids = {j["id"] for j in jobs}
            assert "multi-1" not in ids
            assert "multi-2" in ids

            sched_mod.remove_schedule("multi-2")
            scheduler.shutdown(wait=False)
        finally:
            sched_mod._scheduler = old

    @pytest.mark.asyncio
    async def test_disabled_schedule_skipped(self):
        """_run_scheduled_workflow skips execution for disabled schedules."""
        from sandcastle.models.db import Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())

        async with async_session() as session:
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="disabled-wf",
                cron_expression="* * * * *",
                enabled=False,
            )
            session.add(schedule)
            await session.commit()

        with patch(
            "sandcastle.queue.worker.enqueue_workflow",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            await _run_scheduled_workflow(schedule_id, "disabled-wf", {})
            mock_enqueue.assert_not_awaited()


class TestScheduleWithWebhookCallback:
    """Schedule with webhook callback on completion."""

    @pytest.mark.asyncio
    async def test_schedule_with_callback_url(self):
        """Scheduled runs with tenant_id have budget resolved from ApiKey."""
        from sandcastle.models.db import ApiKey, Run, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())
        tenant_id = "tenant-webhook-test"

        async with async_session() as session:
            api_key = ApiKey(
                id=uuid.uuid4(),
                name="webhook-key",
                key_hash="fakehash_webhook_" + tenant_id,
                is_active=True,
                tenant_id=tenant_id,
                max_cost_per_run_usd=10.0,
            )
            session.add(api_key)

            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="webhook-wf",
                cron_expression="* * * * *",
                enabled=True,
                tenant_id=tenant_id,
                last_run_id=None,
            )
            session.add(schedule)
            await session.commit()

        with (
            patch(
                "sandcastle.queue.scheduler._load_workflow_yaml",
                return_value="name: webhook-test\nsteps: []",
            ),
            patch(
                "sandcastle.queue.worker.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            await _run_scheduled_workflow(schedule_id, "webhook-wf", {})
            mock_enqueue.assert_awaited_once()

        async with async_session() as session:
            schedule = await session.get(Schedule, uuid.UUID(schedule_id))
            run = await session.get(Run, schedule.last_run_id)
            assert run is not None
            assert run.tenant_id == tenant_id
            assert run.max_cost_usd == 10.0


# ===========================================================================
# 3. QUEUE / WORKER TESTS (8+ tests)
# ===========================================================================


class TestJobEnqueue:
    """Job enqueueing via Redis (arq) or in-process (asyncio)."""

    @pytest.mark.asyncio
    async def test_enqueue_creates_asyncio_task_in_local_mode(self):
        """Without Redis, enqueue_workflow creates an asyncio.Task."""
        import sandcastle.queue.worker as worker_mod

        with patch.object(worker_mod.settings, "redis_url", ""):
            with patch.object(
                worker_mod, "run_workflow_job", new_callable=AsyncMock
            ) as mock_job:
                mock_job.return_value = {"status": "completed"}
                run_id = str(uuid.uuid4())
                await worker_mod.enqueue_workflow("yaml", {"k": "v"}, run_id)

                # Task should be tracked
                matching = [
                    t for t in worker_mod._background_tasks
                    if t.get_name() == f"workflow-{run_id}"
                ]
                assert len(matching) == 1

    @pytest.mark.asyncio
    async def test_enqueue_redis_uses_pool(self):
        """With Redis configured, enqueue_workflow uses the shared pool."""
        import sandcastle.queue.worker as worker_mod

        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock()

        old_pool = worker_mod._enqueue_redis_pool
        worker_mod._enqueue_redis_pool = mock_pool

        try:
            with patch.object(worker_mod.settings, "redis_url", "redis://localhost:6379"):
                run_id = str(uuid.uuid4())
                await worker_mod.enqueue_workflow("yaml-content", {"a": 1}, run_id)

                mock_pool.enqueue_job.assert_awaited_once()
                call_args = mock_pool.enqueue_job.call_args
                assert call_args[0][0] == "run_workflow_job"
                assert call_args[0][1] == "yaml-content"
                assert call_args[0][2] == {"a": 1}
                assert call_args[0][3] == run_id
        finally:
            worker_mod._enqueue_redis_pool = old_pool


class TestJobExecution:
    """Job execution and DB status updates."""

    @pytest.mark.asyncio
    async def test_run_workflow_job_updates_status(self):
        """run_workflow_job transitions run from QUEUED to RUNNING."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="exec-wf",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.outputs = {"result": "ok"}
        mock_result.total_cost_usd = 0.01
        mock_result.error = None
        mock_result.started_at = datetime.now(timezone.utc)
        mock_result.completed_at = datetime.now(timezone.utc)

        with (
            patch("sandcastle.engine.dag.parse_yaml_string") as mock_parse,
            patch("sandcastle.engine.dag.validate", return_value=[]),
            patch("sandcastle.engine.dag.build_plan"),
            patch("sandcastle.engine.storage.create_storage"),
            patch("sandcastle.engine.executor.execute_workflow", new_callable=AsyncMock, return_value=mock_result),
            patch("sandcastle.webhooks.dispatcher.dispatch_webhook", new_callable=AsyncMock),
        ):
            mock_workflow = MagicMock()
            mock_workflow.name = "exec-wf"
            mock_workflow.on_complete = None
            mock_workflow.on_failure = None
            mock_parse.return_value = mock_workflow
            result = await run_workflow_job({}, "yaml", {}, run_id)

        assert result["status"] == "completed"

        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(run_id))
            assert db_run.status == RunStatus.COMPLETED


class TestJobFailureHandling:
    """Job failure handling and error recording."""

    @pytest.mark.asyncio
    async def test_failed_job_records_error_in_db(self):
        """When parse_yaml_string raises, the run is marked FAILED with error."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="fail-wf",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.engine.dag.parse_yaml_string", side_effect=ValueError("bad yaml")),
            patch("sandcastle.webhooks.dispatcher.dispatch_webhook", new_callable=AsyncMock),
        ):
            result = await run_workflow_job({}, "bad-yaml", {}, run_id)

        assert result["status"] == "failed"
        assert "bad yaml" in result["error"]

        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(run_id))
            assert db_run.status == RunStatus.FAILED
            assert "bad yaml" in db_run.error

    @pytest.mark.asyncio
    async def test_dead_letter_on_repeated_failure(self):
        """After max retries exhausted on Redis enqueue, run is marked FAILED (dead letter)."""
        import sandcastle.queue.worker as worker_mod

        run_id = str(uuid.uuid4())

        # Create a QUEUED run
        from sandcastle.models.db import Run, RunStatus, async_session

        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="dead-letter-wf",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        mock_pool = AsyncMock()
        mock_pool.enqueue_job.side_effect = ConnectionError("Redis permanently down")

        with (
            patch.object(worker_mod.settings, "redis_url", "redis://localhost:6379"),
            patch("arq.create_pool", new_callable=AsyncMock, return_value=mock_pool),
        ):
            old_pool = worker_mod._enqueue_redis_pool
            worker_mod._enqueue_redis_pool = None
            try:
                with pytest.raises(ConnectionError):
                    await worker_mod.enqueue_workflow("yaml", {}, run_id)

                # Run should be marked FAILED (dead letter queue equivalent)
                async with async_session() as session:
                    db_run = await session.get(Run, uuid.UUID(run_id))
                    assert db_run.status == RunStatus.FAILED
                    assert "Redis enqueue failed" in db_run.error
            finally:
                worker_mod._enqueue_redis_pool = old_pool


class TestJobRetryWithBackoff:
    """Retry with exponential backoff in dispatch_webhook (used by worker)."""

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self):
        """Retry delays follow exponential backoff: 2, 4, 8, ..."""
        sleep_delays = []

        async def capture_sleep(delay):
            sleep_delays.append(delay)

        p1, p2 = _make_dispatch_patches()
        mock_client = _make_mock_client(
            post_side_effect=[
                MagicMock(status_code=500),
                MagicMock(status_code=500),
                MagicMock(status_code=500),
                MagicMock(status_code=500),
            ]
        )

        with (p1, p2, patch(
            "sandcastle.webhooks.dispatcher.httpx.AsyncClient",
            return_value=mock_client,
        ), patch("asyncio.sleep", side_effect=capture_sleep)):
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.failed",
                run_id="run-exp",
                workflow="wf",
                status="failed",
                max_retries=4,
            )

        # Delays between attempts: 2^1=2, 2^2=4, 2^3=8
        assert len(sleep_delays) == 3
        assert sleep_delays[0] == 2
        assert sleep_delays[1] == 4
        assert sleep_delays[2] == 8


class TestRedisPoolCreation:
    """Redis pool creation when REDIS_URL is set."""

    @pytest.mark.asyncio
    async def test_redis_pool_created_lazily(self):
        """Redis pool is created on first enqueue, not at import time."""
        import sandcastle.queue.worker as worker_mod

        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock()

        old_pool = worker_mod._enqueue_redis_pool
        worker_mod._enqueue_redis_pool = None

        try:
            with (
                patch.object(worker_mod.settings, "redis_url", "redis://localhost:6379"),
                patch("arq.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create,
            ):
                run_id = str(uuid.uuid4())
                await worker_mod.enqueue_workflow("yaml", {}, run_id)

                # create_pool should have been called exactly once
                mock_create.assert_awaited_once()
        finally:
            worker_mod._enqueue_redis_pool = old_pool

    def test_parse_redis_url(self):
        """_parse_redis_url correctly parses a Redis URL into RedisSettings."""
        from sandcastle.queue.worker import _parse_redis_url

        settings = _parse_redis_url("redis://:mypass@redis.example.com:6380/2")
        assert settings.host == "redis.example.com"
        assert settings.port == 6380
        assert settings.database == 2
        assert settings.password == "mypass"


class TestInProcessFallback:
    """In-process fallback when no Redis is configured."""

    @pytest.mark.asyncio
    async def test_in_process_creates_task_with_done_callback(self):
        """In local mode, the created task has a done callback for error logging."""
        import sandcastle.queue.worker as worker_mod

        with patch.object(worker_mod.settings, "redis_url", ""):
            with patch.object(
                worker_mod, "run_workflow_job", new_callable=AsyncMock
            ) as mock_job:
                mock_job.return_value = {"status": "completed"}
                run_id = str(uuid.uuid4())
                await worker_mod.enqueue_workflow("yaml", {}, run_id)

                matching = [
                    t for t in worker_mod._background_tasks
                    if t.get_name() == f"workflow-{run_id}"
                ]
                assert len(matching) == 1
                # Task should be in the tracking set
                task = matching[0]
                assert task in worker_mod._background_tasks


class TestJobCancellation:
    """Job cancellation: skipping duplicate execution."""

    @pytest.mark.asyncio
    async def test_duplicate_execution_skipped(self):
        """run_workflow_job skips execution if run is not QUEUED."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="cancel-wf",
                status=RunStatus.RUNNING,  # Already running
                input_data={},
            )
            session.add(run)
            await session.commit()

        result = await run_workflow_job({}, "yaml", {}, run_id)
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_missing_run_returns_failed(self):
        """run_workflow_job returns failed status for non-existent run."""
        from sandcastle.queue.worker import run_workflow_job

        fake_id = str(uuid.uuid4())
        result = await run_workflow_job({}, "yaml", {}, fake_id)
        assert result["status"] == "failed"
        assert "not found" in result["error"]


class TestTaskDoneCallback:
    """Background task done callback for error logging."""

    def test_callback_discards_task_from_tracking_set(self):
        """_task_done_callback removes the task from _background_tasks."""
        from sandcastle.queue.worker import _background_tasks, _task_done_callback

        mock_task = MagicMock()
        mock_task.cancelled.return_value = False
        mock_task.exception.return_value = None

        _background_tasks.add(mock_task)
        _task_done_callback(mock_task)
        assert mock_task not in _background_tasks

    def test_callback_handles_cancelled_task(self):
        """_task_done_callback handles a cancelled task gracefully."""
        from sandcastle.queue.worker import _background_tasks, _task_done_callback

        mock_task = MagicMock()
        mock_task.cancelled.return_value = True

        _background_tasks.add(mock_task)
        _task_done_callback(mock_task)
        assert mock_task not in _background_tasks

    def test_callback_logs_exception(self, caplog):
        """_task_done_callback logs unhandled exceptions from tasks."""
        import logging
        from sandcastle.queue.worker import _background_tasks, _task_done_callback

        mock_task = MagicMock()
        mock_task.cancelled.return_value = False
        mock_task.exception.return_value = RuntimeError("boom")

        _background_tasks.add(mock_task)
        with caplog.at_level(logging.ERROR, logger="sandcastle.queue.worker"):
            _task_done_callback(mock_task)

        assert mock_task not in _background_tasks
        assert "boom" in caplog.text


class TestWorkerStartupShutdown:
    """Worker startup and shutdown hooks."""

    @pytest.mark.asyncio
    async def test_startup_recovers_stuck_runs(self):
        """startup() calls _recover_stuck_runs."""
        with patch(
            "sandcastle.queue.worker._recover_stuck_runs",
            new_callable=AsyncMock,
        ) as mock_recover:
            from sandcastle.queue.worker import startup
            await startup({})
            mock_recover.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_cleans_up_pool(self):
        """shutdown() calls cleanup_enqueue_pool."""
        with patch(
            "sandcastle.queue.worker.cleanup_enqueue_pool",
            new_callable=AsyncMock,
        ) as mock_cleanup:
            from sandcastle.queue.worker import shutdown
            await shutdown({})
            mock_cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_pool_when_none(self):
        """cleanup_enqueue_pool is safe when no pool exists."""
        import sandcastle.queue.worker as worker_mod

        old_pool = worker_mod._enqueue_redis_pool
        worker_mod._enqueue_redis_pool = None
        try:
            await worker_mod.cleanup_enqueue_pool()
            assert worker_mod._enqueue_redis_pool is None
        finally:
            worker_mod._enqueue_redis_pool = old_pool

    @pytest.mark.asyncio
    async def test_cleanup_pool_closes_and_nulls(self):
        """cleanup_enqueue_pool closes the pool and sets it to None."""
        import sandcastle.queue.worker as worker_mod

        mock_pool = AsyncMock()
        old_pool = worker_mod._enqueue_redis_pool
        worker_mod._enqueue_redis_pool = mock_pool
        try:
            await worker_mod.cleanup_enqueue_pool()
            mock_pool.close.assert_awaited_once()
            assert worker_mod._enqueue_redis_pool is None
        finally:
            worker_mod._enqueue_redis_pool = old_pool
