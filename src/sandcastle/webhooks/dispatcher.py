"""Webhook callback dispatcher with HMAC signing and retries.

Retry policy
------------
Failed webhook deliveries (5xx responses or network errors) are retried
with exponential backoff: ``delay = min(2 ** attempt, MAX_RETRY_DELAY_SECONDS)``.
The default ceiling is **30 seconds** to prevent excessively long waits.
Client errors (4xx) and redirects (3xx) are never retried.

HMAC signature format
---------------------
When ``WEBHOOK_SECRET`` is configured, every outgoing request includes an
``X-Sandcastle-Signature`` header containing the hex-encoded HMAC-SHA256
digest of the **raw JSON body** (UTF-8 encoded).

Receivers should verify:

    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, request.headers["X-Sandcastle-Signature"])

The ``X-Sandcastle-Event`` header carries the event type string.
"""

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
from sandcastle.engine.http_transport import build_pinned_transport

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

# Maximum webhook payload size (1MB)
MAX_PAYLOAD_BYTES = 1_048_576

# Maximum output preview size in truncated payloads
MAX_OUTPUT_PREVIEW_BYTES = 10_000

# Maximum retry delay in seconds (exponential backoff ceiling)
MAX_RETRY_DELAY_SECONDS = 30


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


def _pick_allowed_ip(resolved: list, hostname: str) -> str:
    """Validate resolved addresses and pick the one to pin for delivery.

    Every resolved address must be public. The first validated address is
    returned so the caller can pin the subsequent TCP connection to the exact
    same address, preventing a DNS-rebinding TOCTOU.
    """
    first_allowed_ip: str | None = None
    for _, _, _, _, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                raise ValueError(
                    f"DNS rebinding detected: '{hostname}' now resolves to "
                    f"blocked network ({ip})"
                )
        if first_allowed_ip is None:
            first_allowed_ip = sockaddr[0]

    if first_allowed_ip is None:
        raise ValueError(f"Cannot resolve hostname '{hostname}' at delivery time")
    return first_allowed_ip


async def _resolve_and_check_ip(hostname: str, port: int) -> str:
    """Resolve (off the event loop), validate, and return the delivery IP."""
    try:
        resolved = await asyncio.get_running_loop().getaddrinfo(hostname, port)
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname '{hostname}' at delivery time")
    return _pick_allowed_ip(resolved, hostname)


def _truncate_payload(
    payload: dict[str, Any],
    outputs: dict[str, Any] | None,
    run_id: str,
) -> str:
    """Truncate a webhook payload to fit within MAX_PAYLOAD_BYTES.

    Returns the JSON-encoded body string.
    """
    body = json.dumps(payload, default=str)

    if len(body.encode("utf-8")) <= MAX_PAYLOAD_BYTES:
        return body

    logger.warning(
        f"Webhook payload for run {run_id} exceeds {MAX_PAYLOAD_BYTES} bytes, "
        "truncating outputs"
    )
    if outputs:
        full_preview = json.dumps(outputs, default=str)
        if len(full_preview) > MAX_OUTPUT_PREVIEW_BYTES:
            outputs_preview = (
                full_preview[: MAX_OUTPUT_PREVIEW_BYTES - 15] + "...(truncated)"
            )
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

    # Final safety check: if still over limit (e.g. huge error field),
    # build a minimal valid JSON payload so receivers can still parse it.
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > MAX_PAYLOAD_BYTES:
        logger.warning(
            "Webhook payload for run %s still exceeds limit after output "
            "truncation (%d bytes), replacing with minimal payload",
            run_id,
            len(body_bytes),
        )
        minimal = {
            "event": payload.get("event", "unknown"),
            "run_id": run_id,
            "status": payload.get("status", "unknown"),
            "timestamp": payload.get("timestamp", ""),
            "outputs": {
                "outputs_truncated": True,
                "_reason": "payload_too_large",
            },
            "error": "(truncated - payload exceeded size limit)",
        }
        body = json.dumps(minimal, default=str)

    return body


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
    # Validate URL syntax without synchronously resolving on this async path.
    parsed = urlparse(url)
    try:
        if parsed.scheme not in ("https", "http"):
            raise ValueError(f"callback_url must use http(s), got '{parsed.scheme}'")
        if not parsed.hostname:
            raise ValueError("callback_url has no hostname")
    except ValueError as e:
        logger.error(f"Webhook URL validation failed: {e}")
        return False

    # Resolve and re-validate DNS at delivery time, then pin the connection to
    # that exact address so httpx cannot resolve a TTL-0 rebound address later.
    # The initial validate_callback_url() checks at creation/submission time,
    # but the hostname could resolve to a different IP at delivery time.
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        pinned_ip = await _resolve_and_check_ip(
            parsed.hostname or "", parsed.port or default_port
        )
    except ValueError as e:
        logger.error(f"Webhook DNS rebinding check failed: {e}")
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

    body = _truncate_payload(payload, outputs, run_id)

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "Sandcastle-Webhook/1.0",
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
        transport=build_pinned_transport({parsed.hostname: pinned_ip}) if parsed.hostname else None,
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

            except httpx.TimeoutException as e:
                logger.warning(
                    f"Webhook attempt {attempt} timed out for {url}: {e}"
                )
            except httpx.HTTPError as e:
                logger.warning(f"Webhook attempt {attempt} failed: {e}")
            except Exception as e:
                # Catch unexpected errors (e.g. SSL, encoding) so webhook
                # failures never propagate and block workflow execution.
                logger.error(
                    f"Webhook attempt {attempt} unexpected error: {e}"
                )

            if attempt < max_retries:
                delay = min(2**attempt, MAX_RETRY_DELAY_SECONDS)
                await asyncio.sleep(delay)

    logger.error(
        f"Webhook delivery failed after {max_retries} attempts: "
        f"{event} for run {run_id} to {url}"
    )
    return False


def _sign_payload(body: str, secret: str) -> str:
    """Create HMAC-SHA256 signature for a webhook payload.

    The signature is the hex-encoded HMAC-SHA256 digest of the raw JSON
    ``body`` string (UTF-8 encoded), keyed with ``secret`` (also UTF-8).
    The full body is signed - no normalization or canonicalization is applied.
    """
    return hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(body: str, signature: str, secret: str) -> bool:
    """Verify an incoming webhook signature (constant-time comparison).

    ``body`` must be the raw JSON string exactly as received (no re-encoding).
    ``signature`` is the value of the ``X-Sandcastle-Signature`` header.
    ``secret`` is the shared ``WEBHOOK_SECRET`` value.

    Returns True when the signature matches.
    """
    expected = _sign_payload(body, secret)
    return hmac.compare_digest(expected, signature)
