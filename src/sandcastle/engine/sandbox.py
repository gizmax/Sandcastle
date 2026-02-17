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
        # Accumulate assistant text as fallback if result event has no text
        assistant_texts: list[str] = []

        async for event in self.query_stream(request):
            evt_type = event.data.get("type", event.event)

            if evt_type == "result":
                result.text = event.data.get("result", "") or event.data.get("text", "")
                result.structured_output = event.data.get("structured_output")
                result.total_cost_usd = event.data.get("total_cost_usd", 0.0)
                result.num_turns = event.data.get("num_turns", 0)
                if not result.text:
                    logger.debug(
                        "Result event has no text. Keys: %s, turns=%d, cost=%.4f",
                        list(event.data.keys()),
                        result.num_turns, result.total_cost_usd,
                    )
            elif evt_type == "error":
                error_msg = event.data.get("error", "Unknown Sandstorm error")
                raise SandstormError(error_msg)
            elif evt_type in ("assistant", "message"):
                text = (
                    event.data.get("text", "")
                    or event.data.get("content", "")
                    or event.data.get("result", "")
                    or event.data.get("data", "")
                )
                # Try nested message.content structure (Sandstorm format)
                if not text:
                    msg = event.data.get("message", {})
                    if isinstance(msg, dict):
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                t = block.get("text", "")
                                if t:
                                    text = t
                                    break
                # Try content_blocks
                if not text:
                    for block in event.data.get("content_blocks", []):
                        if isinstance(block, dict) and block.get("text"):
                            text = block["text"]
                            break
                if text:
                    assistant_texts.append(text)

        # Fallback: use accumulated assistant text if result text is empty
        if not result.text and assistant_texts:
            result.text = assistant_texts[-1]
            logger.info(
                "Using last assistant message as result text (%d chars, "
                "%d total messages)",
                len(result.text), len(assistant_texts),
            )

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
