"""Webhook callback dispatcher with HMAC signing and retries."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from sandcastle.config import settings

logger = logging.getLogger(__name__)

# Networks blocked for SSRF prevention
_BLOCKED_NETWORKS = [
    # IPv4 private / special-purpose ranges
    ipaddress.ip_network("0.0.0.0/8"),         # "This" network (unspecified source)
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("10.0.0.0/8"),         # Private class A
    ipaddress.ip_network("172.16.0.0/12"),      # Private class B
    ipaddress.ip_network("192.168.0.0/16"),     # Private class C
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local / APIPA
    ipaddress.ip_network("100.64.0.0/10"),      # CGNAT (RFC 6598)
    # IPv6 private / special-purpose ranges
    ipaddress.ip_network("::1/128"),            # Loopback
    ipaddress.ip_network("fc00::/7"),           # Unique local (ULA, includes fd00::/8)
    ipaddress.ip_network("fe80::/10"),          # Link-local
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped IPv6 addresses
]


def validate_callback_url(url: str) -> str:
    """Validate a callback URL to prevent SSRF attacks."""
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError(f"callback_url must use http(s), got '{parsed.scheme}'")
    if not parsed.hostname:
        raise ValueError("callback_url has no hostname")

    default_port = 443 if parsed.scheme == "https" else 80
    try:
        resolved = socket.getaddrinfo(parsed.hostname, parsed.port or default_port)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve hostname '{parsed.hostname}': {e}")

    for _, _, _, _, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                raise ValueError(
                    f"callback_url resolves to blocked network ({ip})"
                )
    return url


async def dispatch_webhook(
    url: str,
    event: str,
    run_id: str,
    workflow: str,
    status: str,
    outputs: dict[str, Any] | None = None,
    costs: float = 0.0,
    duration_seconds: float = 0.0,
    error: str | None = None,
    max_retries: int = 3,
) -> bool:
    """Send a webhook callback with HMAC signature and retry logic.

    Returns True if the webhook was delivered successfully.
    """
    # Validate URL to prevent SSRF
    try:
        validate_callback_url(url)
    except ValueError as e:
        logger.error(f"Webhook URL validation failed: {e}")
        return False
    payload = {
        "event": event,
        "run_id": run_id,
        "workflow": workflow,
        "status": status,
        "outputs": outputs,
        "total_cost_usd": costs,
        "costs": costs,  # Kept for backward compat
        "duration_seconds": duration_seconds,
        "error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    body = json.dumps(payload, default=str)

    # Guard against excessively large payloads (max 1MB)
    max_payload_bytes = 1_048_576
    if len(body.encode("utf-8")) > max_payload_bytes:
        logger.warning(
            f"Webhook payload for run {run_id} exceeds {max_payload_bytes} bytes, "
            "truncating outputs"
        )
        if outputs:
            full_preview = json.dumps(outputs, default=str)
            if len(full_preview) > 10000:
                outputs_preview = full_preview[:9990] + "...(truncated)"
            else:
                outputs_preview = full_preview
        else:
            outputs_preview = None
        payload["outputs"] = {
            "outputs_truncated": True,
            "outputs_preview": outputs_preview,
            "_reason": "payload_too_large",
        }
        body = json.dumps(payload, default=str)

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Sandcastle-Event": event,
    }
    if settings.webhook_secret:
        signature = _sign_payload(body, settings.webhook_secret)
        headers["X-Sandcastle-Signature"] = signature
    else:
        logger.warning("Webhook dispatched without HMAC signature (no webhook_secret configured)")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=False,
        max_redirects=0,
    ) as client:
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.post(url, content=body, headers=headers)

                if response.status_code < 300:
                    logger.info(
                        f"Webhook delivered: {event} for run {run_id} "
                        f"(status={response.status_code})"
                    )
                    return True

                if 300 <= response.status_code < 400:
                    logger.warning(
                        f"Webhook got redirect {response.status_code} for {url}, "
                        "not following (SSRF prevention)"
                    )
                    return False

                if 400 <= response.status_code < 500:
                    logger.warning(
                        f"Webhook got client error {response.status_code} for {url}, "
                        "not retrying"
                    )
                    return False

                logger.warning(
                    f"Webhook attempt {attempt} got status {response.status_code} "
                    f"for {url}"
                )

            except httpx.HTTPError as e:
                logger.warning(f"Webhook attempt {attempt} failed: {e}")

            if attempt < max_retries:
                delay = min(2**attempt, 30)
                await asyncio.sleep(delay)

    logger.error(
        f"Webhook delivery failed after {max_retries} attempts: "
        f"{event} for run {run_id} to {url}"
    )
    return False


def _sign_payload(body: str, secret: str) -> str:
    """Create HMAC-SHA256 signature for a webhook payload."""
    return hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(body: str, signature: str, secret: str) -> bool:
    """Verify an incoming webhook signature."""
    expected = _sign_payload(body, secret)
    return hmac.compare_digest(expected, signature)
