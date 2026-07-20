"""Tests for webhook dispatcher - HMAC signing, SSRF prevention, retry logic."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.webhooks.dispatcher import (
    _pick_allowed_ip,
    _resolve_and_check_ip,
    _sign_payload,
    _truncate_payload,
    validate_callback_url,
    verify_signature,
)

_PUBLIC_IP = "93.184.216.34"
_PRIVATE_IP = "169.254.169.254"


class TestSignPayload:
    def test_deterministic(self):
        sig1 = _sign_payload("body", "secret")
        sig2 = _sign_payload("body", "secret")
        assert sig1 == sig2

    def test_different_body_different_sig(self):
        sig1 = _sign_payload("body1", "secret")
        sig2 = _sign_payload("body2", "secret")
        assert sig1 != sig2

    def test_different_secret_different_sig(self):
        sig1 = _sign_payload("body", "secret1")
        sig2 = _sign_payload("body", "secret2")
        assert sig1 != sig2

    def test_returns_hex_string(self):
        sig = _sign_payload("test", "key")
        assert isinstance(sig, str)
        assert len(sig) == 64
        int(sig, 16)

    def test_matches_manual_hmac(self):
        body = '{"event": "test"}'
        secret = "my-webhook-secret"
        expected = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert _sign_payload(body, secret) == expected


class TestVerifySignature:
    def test_valid_signature(self):
        body = '{"data": 123}'
        secret = "secret"
        sig = _sign_payload(body, secret)
        assert verify_signature(body, sig, secret) is True

    def test_invalid_signature(self):
        assert verify_signature("body", "wrong", "secret") is False

    def test_tampered_body(self):
        body = '{"data": 123}'
        secret = "secret"
        sig = _sign_payload(body, secret)
        assert verify_signature(body + " ", sig, secret) is False

    def test_empty_body(self):
        sig = _sign_payload("", "secret")
        assert verify_signature("", sig, "secret") is True


class TestValidateCallbackUrl:
    def test_valid_https_url(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]):
            result = validate_callback_url("https://example.com/webhook")
            assert result == "https://example.com/webhook"

    def test_valid_http_url(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 80)),
        ]):
            result = validate_callback_url("http://example.com/hook")
            assert result == "http://example.com/hook"

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError, match="http"):
            validate_callback_url("ftp://example.com/file")

    def test_rejects_no_hostname(self):
        with pytest.raises(ValueError, match="hostname"):
            validate_callback_url("https:///path")

    def test_rejects_localhost(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://localhost/hook")

    def test_rejects_private_10_network(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://internal.corp/hook")

    def test_rejects_private_172_network(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("172.16.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://docker-host/hook")

    def test_rejects_private_192_168_network(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("192.168.1.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://router.local/hook")

    def test_rejects_ipv6_loopback(self):
        with patch("socket.getaddrinfo", return_value=[
            (10, 1, 6, "", ("::1", 443, 0, 0)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://ip6-localhost/hook")

    def test_rejects_link_local(self):
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("169.254.169.254", 80)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("http://metadata.internal/latest")

    def test_unresolvable_hostname(self):
        import socket
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS fail")):
            with pytest.raises(ValueError, match="resolve"):
                validate_callback_url("https://nonexistent.invalid/hook")

    # --- New tests for extended SSRF blocklist (added in audit) ---

    def test_rejects_ipv6_link_local(self):
        """fe80::/10 was missing from the original blocklist."""
        with patch("socket.getaddrinfo", return_value=[
            (10, 1, 6, "", ("fe80::1", 443, 0, 1)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://ipv6-link-local.example/hook")

    def test_rejects_ipv4_mapped_ipv6_loopback(self):
        """::ffff:127.0.0.1 (IPv4-mapped IPv6 loopback) was missing."""
        with patch("socket.getaddrinfo", return_value=[
            (10, 1, 6, "", ("::ffff:127.0.0.1", 443, 0, 0)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://mapped-loopback.example/hook")

    def test_rejects_cgnat(self):
        """100.64.0.0/10 CGNAT range was missing."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("100.64.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://cgnat.example/hook")

    def test_rejects_unspecified_address(self):
        """0.0.0.0/8 unspecified range was missing."""
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("0.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://unspecified.example/hook")

    def test_allows_public_ipv6(self):
        """Legitimate public IPv6 addresses should be allowed."""
        with patch("socket.getaddrinfo", return_value=[
            (10, 1, 6, "", ("2001:db8::1", 443, 0, 0)),
        ]):
            result = validate_callback_url("https://ipv6.example.com/hook")
            assert result == "https://ipv6.example.com/hook"


class TestResolveAndCheckIp:
    """Tests for the DNS rebinding prevention check."""

    def test_public_ip_passes(self):
        """A public IP at delivery time should pass."""
        # Should not raise
        _pick_allowed_ip([(2, 1, 6, "", ("93.184.216.34", 443))], "example.com")

    def test_private_ip_at_delivery_time_blocked(self):
        """DNS rebinding: hostname now resolves to private IP."""
        with pytest.raises(ValueError, match="rebinding"):
            _pick_allowed_ip([(2, 1, 6, "", ("10.0.0.1", 443))], "evil-rebind.com")

    def test_localhost_at_delivery_time_blocked(self):
        """DNS rebinding: hostname now resolves to localhost."""
        with pytest.raises(ValueError, match="rebinding"):
            _pick_allowed_ip([(2, 1, 6, "", ("127.0.0.1", 443))], "evil-rebind.com")

    def test_metadata_service_at_delivery_time_blocked(self):
        """DNS rebinding: hostname now resolves to cloud metadata endpoint."""
        with pytest.raises(ValueError, match="rebinding"):
            _pick_allowed_ip([(2, 1, 6, "", ("169.254.169.254", 80))], "evil-rebind.com")

    async def test_dns_failure_at_delivery_time_blocked(self):
        """Unresolvable hostname at delivery time should fail."""
        import socket
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            with pytest.raises(ValueError, match="resolve"):
                await _resolve_and_check_ip("gone.example.com", 443)

    def test_ipv6_ula_at_delivery_time_blocked(self):
        """IPv6 unique-local address at delivery time should be blocked."""
        with pytest.raises(ValueError, match="rebinding"):
            _pick_allowed_ip(
                [(10, 1, 6, "", ("fd12:3456:789a::1", 443, 0, 0))], "evil-ipv6.com"
            )


class TestWebhookDeliveryPinning:
    """Delivery must dial the exact public IP validated immediately beforehand."""

    @pytest.mark.asyncio
    async def test_delivery_rejects_private_ip_after_initial_validation(self, monkeypatch):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        def fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001
            # Delivery-time resolution lands on a private IP (TTL-0 rebind).
            return [(2, 1, 6, "", (_PRIVATE_IP, port))]

        client_cls = MagicMock()
        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr("sandcastle.webhooks.dispatcher.httpx.AsyncClient", client_cls)

        delivered = await dispatch_webhook(
            url="https://rebind.example/hook",
            event="workflow.completed",
            run_id="run-rebind",
            workflow="wf",
            status="completed",
        )

        assert delivered is False
        client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_delivery_dials_validated_public_ip_with_pinned_transport(self, monkeypatch):
        from sandcastle.engine.http_transport import build_pinned_transport
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        def fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001
            return [(2, 1, 6, "", (_PUBLIC_IP, port))]

        captured = {}
        transport_hosts = []

        def capture_pinned_transport(host_to_ip):  # noqa: ANN001
            transport_hosts.append(host_to_ip)
            return build_pinned_transport(host_to_ip)

        async def fake_parent_transport(self, request):  # noqa: ANN001
            captured["host"] = request.url.host
            captured["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, json={"ok": True}, request=request)

        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(
            httpx.AsyncHTTPTransport, "handle_async_request", fake_parent_transport
        )
        monkeypatch.setattr(
            "sandcastle.webhooks.dispatcher.build_pinned_transport", capture_pinned_transport
        )

        delivered = await dispatch_webhook(
            url="https://public.example/hook",
            event="workflow.completed",
            run_id="run-public",
            workflow="wf",
            status="completed",
        )

        assert delivered is True
        assert transport_hosts == [{"public.example": _PUBLIC_IP}]
        assert captured == {"host": _PUBLIC_IP, "sni": "public.example"}

    @pytest.mark.asyncio
    async def test_rebind_after_delivery_check_cannot_change_dial_target(self, monkeypatch):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        calls = 0

        def fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001
            nonlocal calls
            calls += 1
            if calls == 1:  # the single delivery-time resolution
                return [(2, 1, 6, "", (_PUBLIC_IP, port))]
            raise AssertionError("pinned transport must not resolve DNS again")

        captured = {}

        async def fake_parent_transport(self, request):  # noqa: ANN001
            captured["host"] = request.url.host
            return httpx.Response(200, json={"ok": True}, request=request)

        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(
            httpx.AsyncHTTPTransport, "handle_async_request", fake_parent_transport
        )

        delivered = await dispatch_webhook(
            url="https://ttl-zero.example/hook",
            event="workflow.completed",
            run_id="run-ttl-zero",
            workflow="wf",
            status="completed",
        )

        assert delivered is True
        assert calls == 1
        assert captured["host"] == _PUBLIC_IP


class TestTruncatePayload:
    """Tests for the payload truncation logic."""

    def test_small_payload_not_truncated(self):
        """Payloads under 1MB should pass through unchanged."""
        payload = {"event": "test", "outputs": {"result": "small"}}
        body = _truncate_payload(payload, payload.get("outputs"), "run-1")
        import json
        parsed = json.loads(body)
        assert parsed["outputs"] == {"result": "small"}
        assert "outputs_truncated" not in parsed.get("outputs", {})

    def test_large_payload_truncated(self):
        """Payloads over 1MB should have outputs truncated."""
        import json
        huge_output = {"data": "x" * 2_000_000}
        payload = {
            "event": "test",
            "run_id": "run-1",
            "outputs": huge_output,
        }
        body = _truncate_payload(payload, huge_output, "run-1")
        parsed = json.loads(body)
        assert parsed["outputs"]["outputs_truncated"] is True
        assert parsed["outputs"]["_reason"] == "payload_too_large"
        # Body must fit within 1MB
        assert len(body.encode("utf-8")) <= 1_048_576

    def test_truncated_outputs_have_preview(self):
        """Truncated payloads should contain an output preview."""
        import json
        huge_output = {"data": "y" * 2_000_000}
        payload = {
            "event": "test",
            "run_id": "run-2",
            "outputs": huge_output,
        }
        body = _truncate_payload(payload, huge_output, "run-2")
        parsed = json.loads(body)
        assert parsed["outputs"]["outputs_preview"] is not None
        assert "(truncated)" in parsed["outputs"]["outputs_preview"]

    def test_none_outputs_truncation(self):
        """Truncation with None outputs should set preview to None."""
        # Build a payload that is large due to a huge error field
        payload = {
            "event": "test",
            "run_id": "run-3",
            "outputs": None,
            "error": "e" * 2_000_000,
        }
        body = _truncate_payload(payload, None, "run-3")
        # Should not raise, and body must be within limit
        assert len(body.encode("utf-8")) <= 1_048_576

    def test_hard_truncation_safety_net(self):
        """If truncated payload is still over 1MB (e.g. huge error), hard-truncate."""
        payload = {
            "event": "test",
            "run_id": "run-4",
            "outputs": None,
            "error": "Z" * 2_000_000,
        }
        body = _truncate_payload(payload, None, "run-4")
        assert len(body.encode("utf-8")) <= 1_048_576


class TestDispatchWebhook:
    """Helper to build common mock patches for dispatch_webhook tests."""

    @staticmethod
    def _dispatch_patches():
        """Return context manager patches for dispatch_webhook calls."""
        return (
            patch(
                "sandcastle.webhooks.dispatcher.validate_callback_url",
                return_value="https://ok.com/hook",
            ),
            patch(
                "sandcastle.webhooks.dispatcher._resolve_and_check_ip",
            ),
        )

    @pytest.mark.asyncio
    async def test_successful_delivery(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = self._dispatch_patches()
        with (
            p1,
            p2,
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-123",
                workflow="test-wf",
                status="completed",
            )

        assert result is True
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_validation_returns_false(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        with patch(
            "sandcastle.webhooks.dispatcher.validate_callback_url",
            side_effect=ValueError("blocked"),
        ):
            result = await dispatch_webhook(
                url="https://localhost/evil",
                event="workflow.completed",
                run_id="run-1",
                workflow="wf",
                status="completed",
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_dns_rebinding_blocked(self):
        """dispatch_webhook should fail if DNS rebinding is detected."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

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
    async def test_retries_on_server_error(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        responses = [
            MagicMock(status_code=500),
            MagicMock(status_code=500),
            MagicMock(status_code=200),
        ]

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=responses)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = self._dispatch_patches()
        with (
            p1,
            p2,
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await dispatch_webhook(
                url="https://ok.com",
                event="workflow.completed",
                run_id="run-1",
                workflow="wf",
                status="completed",
                max_retries=3,
            )

        assert result is True
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=500))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = self._dispatch_patches()
        with (
            p1,
            p2,
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await dispatch_webhook(
                url="https://ok.com",
                event="workflow.failed",
                run_id="run-1",
                workflow="wf",
                status="failed",
                max_retries=2,
            )

        assert result is False
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_includes_hmac_signature_header(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_response = MagicMock(status_code=200)
        captured_headers = {}

        async def capture_post(url, content=None, headers=None):
            captured_headers.update(headers or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = self._dispatch_patches()
        with (
            p1,
            p2,
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
            patch("sandcastle.webhooks.dispatcher.settings") as mock_settings,
        ):
            mock_settings.webhook_secret = "test-secret"
            await dispatch_webhook(
                url="https://ok.com",
                event="workflow.completed",
                run_id="run-1",
                workflow="wf",
                status="completed",
            )

        assert "X-Sandcastle-Signature" in captured_headers
        assert "X-Sandcastle-Event" in captured_headers
        assert captured_headers["X-Sandcastle-Event"] == "workflow.completed"
        assert len(captured_headers["X-Sandcastle-Signature"]) == 64

    @pytest.mark.asyncio
    async def test_redirect_not_followed(self):
        """Redirects should not be followed (SSRF prevention)."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=302))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = self._dispatch_patches()
        with (
            p1,
            p2,
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-redir",
                workflow="wf",
                status="completed",
            )

        assert result is False
        # Should not retry on redirect
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_4xx_not_retried(self):
        """Client errors (4xx) should not be retried."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=404))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = self._dispatch_patches()
        with (
            p1,
            p2,
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-404",
                workflow="wf",
                status="completed",
                max_retries=3,
            )

        assert result is False
        # Should NOT retry on client error
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_webhook_does_not_block_workflow(self):
        """Webhook failure must not raise exceptions that block workflow execution."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("unexpected error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = self._dispatch_patches()
        with (
            p1,
            p2,
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            # Should return False, not raise
            result = await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-err",
                workflow="wf",
                status="completed",
                max_retries=1,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_no_hmac_when_secret_not_set(self):
        """Without a webhook_secret, no signature header should be sent."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_response = MagicMock(status_code=200)
        captured_headers = {}

        async def capture_post(url, content=None, headers=None):
            captured_headers.update(headers or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        p1, p2 = self._dispatch_patches()
        with (
            p1,
            p2,
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
            patch("sandcastle.webhooks.dispatcher.settings") as mock_settings,
        ):
            mock_settings.webhook_secret = ""
            await dispatch_webhook(
                url="https://ok.com/hook",
                event="workflow.completed",
                run_id="run-nosig",
                workflow="wf",
                status="completed",
            )

        assert "X-Sandcastle-Signature" not in captured_headers
        assert "X-Sandcastle-Event" in captured_headers
