"""HTTP transports that preserve SSRF validation through connection setup."""

from __future__ import annotations

from typing import Any


def build_pinned_transport(host_to_ip: dict[str, str]) -> Any:
    """Build an httpx transport that dials validated IPs without re-resolving DNS.

    ``host_to_ip`` maps a hostname to the single allowed IP address already
    validated by the caller. The transport rewrites the connection host to that
    address while retaining the original Host header and TLS SNI hostname.
    """
    import httpx

    class _PinnedHTTPTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request: Any) -> Any:
            original_host = request.url.host
            pinned_ip = host_to_ip.get(original_host)
            if pinned_ip and pinned_ip != original_host:
                request.url = request.url.copy_with(host=pinned_ip)
                extensions = dict(request.extensions or {})
                extensions.setdefault("sni_hostname", original_host)
                request.extensions = extensions
            return await super().handle_async_request(request)

    return _PinnedHTTPTransport()
