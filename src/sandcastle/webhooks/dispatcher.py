"""Webhook callback dispatcher with HMAC signing and retries."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from sandcastle.config import settings

logger = logging.getLogger(__name__)


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
    payload = {
        "event": event,
        "run_id": run_id,
        "workflow": workflow,
        "status": status,
        "outputs": outputs,
        "costs": costs,
        "duration_seconds": duration_seconds,
        "error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    body = json.dumps(payload, default=str)
    signature = _sign_payload(body, settings.webhook_secret)

    headers = {
        "Content-Type": "application/json",
        "X-Sandcastle-Signature": signature,
        "X-Sandcastle-Event": event,
    }

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.post(url, content=body, headers=headers)

            if response.status_code < 400:
                logger.info(
                    f"Webhook delivered: {event} for run {run_id} "
                    f"(status={response.status_code})"
                )
                return True

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
