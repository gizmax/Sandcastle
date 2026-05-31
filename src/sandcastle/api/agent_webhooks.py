"""Webhooks subscriber and handler for Anthropic Managed Agent lifecycle events.

This module exposes a FastAPI router that receives webhook callbacks from
Anthropic's Managed Agents API (beta header `managed-agents-2026-04-01`) and
dispatches them to registered async handlers. It also provides an
`AnthropicWebhookSubscription` client for managing subscriptions remotely.

Event types handled:
  - session.status_idle
  - session.status_running
  - session.status_rescheduled
  - session.status_terminated
  - session.error
  - vault.credential_refreshed

The router ACKs within Anthropic's webhook timeout by firing handlers as
asyncio tasks rather than awaiting them inline.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status

from sandcastle.config import settings

logger = logging.getLogger(__name__)


SUPPORTED_EVENTS: tuple[str, ...] = (
    "session.status_idle",
    "session.status_running",
    "session.status_rescheduled",
    "session.status_run_started",  # self-hosted-sandbox May 19 2026 addition
    "session.status_terminated",
    "session.error",
    "vault.credential_refreshed",
)

# Anthropic Managed Agents beta header (May 6 2026 webhooks beta).
ANTHROPIC_BETA_HEADER = "managed-agents-2026-04-01"

# Endpoint path for webhook subscription management. The public path was not
# finalised at module-authoring time; we default to /v1/webhooks and allow
# callers to override via the `endpoint` constructor argument.
DEFAULT_SUBSCRIPTIONS_ENDPOINT = "/v1/webhooks"


HandlerFn = Callable[[dict[str, Any]], Awaitable[None]]

# Registry of handlers keyed by event type. External modules append via
# `register_handler` (decorator or direct call).
AGENT_WEBHOOK_HANDLERS: dict[str, list[HandlerFn]] = {
    event: [] for event in SUPPORTED_EVENTS
}


class WebhookVerifyError(Exception):
    """Raised when a webhook HMAC signature cannot be verified."""


def register_handler(
    event_type: str, handler: HandlerFn | None = None
) -> HandlerFn | Callable[[HandlerFn], HandlerFn]:
    """Register an async handler for a webhook event type.

    Usable both as decorator and as direct call:

        @register_handler("session.status_idle")
        async def my_handler(event): ...

        register_handler("session.error", my_handler)
    """

    if event_type not in AGENT_WEBHOOK_HANDLERS:
        AGENT_WEBHOOK_HANDLERS[event_type] = []

    def _add(fn: HandlerFn) -> HandlerFn:
        AGENT_WEBHOOK_HANDLERS[event_type].append(fn)
        return fn

    if handler is not None:
        return _add(handler)
    return _add


def verify_signature(secret: str, raw_body: bytes, signature_header: str) -> bool:
    """Verify an HMAC-SHA256 signature header against the raw request body.

    Accepts the digest either as a bare hex string or prefixed with `sha256=`.
    """

    if not signature_header:
        return False

    provided = signature_header.strip()
    if provided.startswith("sha256="):
        provided = provided[len("sha256=") :]

    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, provided)


router = APIRouter(prefix="/agent-webhooks", tags=["agent-webhooks"])


async def _dispatch(event_type: str, event: dict[str, Any]) -> None:
    """Run all registered handlers for `event_type` concurrently."""

    handlers = AGENT_WEBHOOK_HANDLERS.get(event_type, [])
    if not handlers:
        logger.debug("No handlers registered for event %s", event_type)
        return

    async def _safe_run(fn: HandlerFn) -> None:
        try:
            await fn(event)
        except Exception:  # noqa: BLE001 - we never want a handler to crash dispatch
            logger.exception("Webhook handler %s failed for %s", fn, event_type)

    await asyncio.gather(*(_safe_run(fn) for fn in handlers))


@router.post("/anthropic")
async def receive_anthropic_webhook(
    request: Request,
    x_anthropic_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive a webhook from Anthropic Managed Agents.

    Verifies HMAC-SHA256 signature when `ANTHROPIC_WEBHOOK_SECRET` is set, or
    when running in non-local mode (in which case absence of the secret is a
    misconfiguration and yields 401). Dispatches in the background and returns
    immediately so Anthropic's ACK timeout is not exceeded.
    """

    raw_body = await request.body()
    secret = os.getenv("ANTHROPIC_WEBHOOK_SECRET")

    if secret:
        if not verify_signature(secret, raw_body, x_anthropic_signature or ""):
            logger.warning("Rejected Anthropic webhook with bad signature")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid signature",
            )
    else:
        if settings.is_local_mode:
            logger.warning(
                "ANTHROPIC_WEBHOOK_SECRET not set; skipping signature verify "
                "(local mode only)"
            )
        else:
            logger.error(
                "ANTHROPIC_WEBHOOK_SECRET not set in non-local mode; rejecting"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="webhook secret not configured",
            )

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid json: {exc}",
        ) from exc

    event_type = payload.get("type") or payload.get("event_type")
    if not event_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing event type",
        )

    if event_type not in SUPPORTED_EVENTS:
        logger.info("Ignoring unsupported Anthropic webhook event %s", event_type)
        return {"status": "ignored", "event_type": event_type}

    # Fire-and-forget dispatch so we ACK Anthropic within their timeout.
    asyncio.create_task(_dispatch(event_type, payload))

    return {"status": "accepted", "event_type": event_type}


class AnthropicWebhookSubscription:
    """Client for managing Anthropic Managed Agents webhook subscriptions.

    The public endpoint for webhook subscription management was not finalised
    at module-authoring time. We default to `/v1/webhooks`; pass `endpoint=`
    to override once Anthropic publishes the final path.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com",
        endpoint: str = DEFAULT_SUBSCRIPTIONS_ENDPOINT,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self._client = client
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": ANTHROPIC_BETA_HEADER,
            "content-type": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout)

    async def create_subscription(
        self,
        callback_url: str,
        events: list[str],
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Create a new webhook subscription. Returns the subscription record."""

        body: dict[str, Any] = {"url": callback_url, "events": events}
        if secret:
            body["secret"] = secret

        client = await self._get_client()
        owns_client = self._client is None
        try:
            resp = await client.post(
                self.endpoint, json=body, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns_client:
                await client.aclose()

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        """List existing webhook subscriptions for this API key."""

        client = await self._get_client()
        owns_client = self._client is None
        try:
            resp = await client.get(self.endpoint, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return list(data.get("data", []) or data.get("subscriptions", []))
            return list(data)
        finally:
            if owns_client:
                await client.aclose()

    async def delete_subscription(self, sub_id: str) -> None:
        """Delete the named subscription."""

        client = await self._get_client()
        owns_client = self._client is None
        try:
            resp = await client.delete(
                f"{self.endpoint}/{sub_id}", headers=self._headers()
            )
            resp.raise_for_status()
        finally:
            if owns_client:
                await client.aclose()


__all__ = [
    "ANTHROPIC_BETA_HEADER",
    "AGENT_WEBHOOK_HANDLERS",
    "AnthropicWebhookSubscription",
    "SUPPORTED_EVENTS",
    "WebhookVerifyError",
    "register_handler",
    "router",
    "verify_signature",
]
