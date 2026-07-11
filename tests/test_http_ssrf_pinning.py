"""Tests for HTTP-step SSRF IP-pinning (DNS-rebind TOCTOU closure).

The executor resolves and validates a hostname once, then pins the validated IP
at connect time via a custom httpx transport so httpx cannot re-resolve to a
rebound private IP. These tests prove the transport dials the exact validated IP
while preserving the Host header and TLS SNI.
"""

from __future__ import annotations

import httpx
import pytest

from sandcastle.engine.dag import StepDefinition
from sandcastle.engine.dag import HttpConfig
from sandcastle.engine.executor import (
    RunContext,
    _build_pinned_transport,
    _execute_http_step,
)

_PUBLIC_IP = "93.184.216.34"  # example.com, public
_PRIVATE_IP = "169.254.169.254"  # cloud metadata, blocked


class TestPinnedTransport:
    """Unit tests for the pinning transport itself."""

    @pytest.mark.asyncio
    async def test_dials_pinned_ip_and_preserves_host_and_sni(self, monkeypatch) -> None:
        captured: dict = {}

        async def _fake_parent(self, request):  # noqa: ANN001
            captured["host"] = request.url.host
            captured["header_host"] = request.headers.get("host")
            captured["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, json={"ok": True}, request=request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _fake_parent)

        transport = _build_pinned_transport({"api.example.com": _PUBLIC_IP})
        request = httpx.Request("GET", "https://api.example.com/data")
        await transport.handle_async_request(request)

        # The connection host is rewritten to the validated IP...
        assert captured["host"] == _PUBLIC_IP
        # ...while the Host header and TLS SNI still name the real hostname.
        assert captured["header_host"] == "api.example.com"
        assert captured["sni"] == "api.example.com"


class TestHttpStepPinning:
    """End-to-end: _execute_http_step dials the validated IP, not re-resolved DNS."""

    @pytest.mark.asyncio
    async def test_request_dialed_to_validated_public_ip(self, monkeypatch) -> None:
        # Pre-flight resolves the hostname to a public IP.
        def _fake_getaddrinfo(host, port, *a, **k):  # noqa: ANN001
            return [(2, 1, 6, "", (_PUBLIC_IP, 443))]

        monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo)

        captured: dict = {}

        async def _fake_parent(self, request):  # noqa: ANN001
            captured["host"] = request.url.host
            captured["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, json={"ok": True}, request=request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _fake_parent)

        step = StepDefinition(
            id="h",
            type="http",
            http_config=HttpConfig(url="https://api.example.com/data", method="GET"),
        )
        ctx = RunContext(run_id="r", input={}, step_outputs={}, admin_trusted=True)
        result = await _execute_http_step(step, ctx)

        assert result.status == "completed"
        assert captured["host"] == _PUBLIC_IP
        assert captured["sni"] == "api.example.com"

    @pytest.mark.asyncio
    async def test_rebind_to_private_ip_cannot_be_reached(self, monkeypatch) -> None:
        # First resolution (pre-flight) yields a public IP and is validated+pinned.
        # A subsequent rebind to a private IP is irrelevant: the transport dials
        # the already-validated public IP and never re-resolves the hostname.
        calls = {"n": 0}

        def _rebinding_getaddrinfo(host, port, *a, **k):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] == 1:
                return [(2, 1, 6, "", (_PUBLIC_IP, 443))]
            return [(2, 1, 6, "", (_PRIVATE_IP, 443))]

        monkeypatch.setattr("socket.getaddrinfo", _rebinding_getaddrinfo)

        captured: dict = {}

        async def _fake_parent(self, request):  # noqa: ANN001
            captured["host"] = request.url.host
            return httpx.Response(200, json={"ok": True}, request=request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _fake_parent)

        step = StepDefinition(
            id="h",
            type="http",
            http_config=HttpConfig(url="https://api.example.com/data", method="GET"),
        )
        ctx = RunContext(run_id="r", input={}, step_outputs={}, admin_trusted=True)
        result = await _execute_http_step(step, ctx)

        assert result.status == "completed"
        # The dial went to the validated public IP, never the rebound private IP.
        assert captured["host"] == _PUBLIC_IP
        assert captured["host"] != _PRIVATE_IP

    @pytest.mark.asyncio
    async def test_preflight_blocks_private_resolution(self, monkeypatch) -> None:
        # If the hostname resolves to a blocked network up front, the step fails
        # before any request is dialed.
        def _fake_getaddrinfo(host, port, *a, **k):  # noqa: ANN001
            return [(2, 1, 6, "", (_PRIVATE_IP, 443))]

        monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo)

        step = StepDefinition(
            id="h",
            type="http",
            http_config=HttpConfig(url="https://metadata.internal/data", method="GET"),
        )
        ctx = RunContext(run_id="r", input={}, step_outputs={}, admin_trusted=True)
        result = await _execute_http_step(step, ctx)

        assert result.status == "failed"
        assert "blocked network" in result.error
