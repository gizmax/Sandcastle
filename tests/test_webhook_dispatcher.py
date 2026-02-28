"""Tests for webhook dispatcher - HMAC signing, SSRF prevention, retry logic."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.webhooks.dispatcher import (
    _sign_payload,
    validate_callback_url,
    verify_signature,
)


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


class TestDispatchWebhook:
    @pytest.mark.asyncio
    async def test_successful_delivery(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com/hook"),
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

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com"),
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

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com"),
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

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com"),
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
