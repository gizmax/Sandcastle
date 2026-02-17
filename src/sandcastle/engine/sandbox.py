"""Sandstorm HTTP client - calls the /query endpoint via SSE."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from httpx_sse import aconnect_sse

logger = logging.getLogger(__name__)


@dataclass
class SSEEvent:
    """A single SSE event from the Sandstorm stream."""

    event: str  # "system", "assistant", "user", "result", "error"
    data: dict


@dataclass
class SandstormResult:
    """Final result from a Sandstorm /query call."""

    text: str = ""
    structured_output: dict | None = None
    total_cost_usd: float = 0.0
    num_turns: int = 0


class SandstormClient:
    """HTTP client for the Sandstorm API."""

    def __init__(
        self,
        base_url: str,
        anthropic_api_key: str,
        e2b_api_key: str,
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.anthropic_api_key = anthropic_api_key
        self.e2b_api_key = e2b_api_key
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def health(self) -> bool:
        """Check if Sandstorm is healthy."""
        try:
            resp = await self._client.get(f"{self.base_url}/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def query(self, request: dict) -> SandstormResult:
        """Send a query to Sandstorm and consume the full SSE stream.

        Returns the final aggregated result.
        """
        result = SandstormResult()

        async for event in self.query_stream(request):
            evt_type = event.data.get("type", event.event)

            if evt_type == "result":
                result.text = event.data.get("result", "") or event.data.get("text", "")
                result.structured_output = event.data.get("structured_output")
                result.total_cost_usd = event.data.get("total_cost_usd", 0.0)
                result.num_turns = event.data.get("num_turns", 0)
            elif evt_type == "error":
                error_msg = event.data.get("error", "Unknown Sandstorm error")
                raise SandstormError(error_msg)

        return result

    async def query_stream(self, request: dict) -> AsyncIterator[SSEEvent]:
        """Send a query to Sandstorm and yield SSE events as they arrive."""
        payload = {
            "prompt": request["prompt"],
            "anthropic_api_key": self.anthropic_api_key,
            "e2b_api_key": self.e2b_api_key,
        }

        # Optional fields
        if "model" in request:
            payload["model"] = request["model"]
        if "max_turns" in request:
            payload["max_turns"] = request["max_turns"]
        if "timeout" in request:
            payload["timeout"] = request["timeout"]
        if "output_format" in request:
            payload["output_format"] = request["output_format"]

        async with aconnect_sse(
            self._client,
            "POST",
            f"{self.base_url}/query",
            json=payload,
        ) as event_source:
            async for sse in event_source.aiter_sse():
                try:
                    data = json.loads(sse.data) if sse.data else {}
                except json.JSONDecodeError:
                    data = {"raw": sse.data}

                yield SSEEvent(event=sse.event or "message", data=data)


class SandstormError(Exception):
    """Error returned by the Sandstorm API."""


# Singleton client pool keyed by (base_url, anthropic_key, e2b_key)
_client_pool: dict[tuple[str, str, str], SandstormClient] = {}


def get_sandstorm_client(
    base_url: str,
    anthropic_api_key: str,
    e2b_api_key: str,
    timeout: float = 300.0,
) -> SandstormClient:
    """Return a shared SandstormClient, reusing TCP connections."""
    key = (base_url.rstrip("/"), anthropic_api_key, e2b_api_key)
    client = _client_pool.get(key)
    if client is None:
        client = SandstormClient(
            base_url=base_url,
            anthropic_api_key=anthropic_api_key,
            e2b_api_key=e2b_api_key,
            timeout=timeout,
        )
        _client_pool[key] = client
    return client
