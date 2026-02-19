"""Sandshore runtime - direct E2B sandbox execution for Sandcastle.

Replaces the Sandstorm HTTP proxy with a direct E2B SDK integration.
Creates E2B sandboxes, uploads the bundled runner.mjs, and streams
stdout events via an asyncio.Queue bridge.

When E2B is not available (no API key or SDK), falls back to the
legacy HTTP proxy mode for backward compatibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from httpx_sse import aconnect_sse

logger = logging.getLogger(__name__)

# Bundled runner script (Claude Agent SDK inside E2B sandbox)
_RUNNER_MJS_PATH = Path(__file__).parent / "runner.mjs"


@dataclass
class SSEEvent:
    """A single SSE event from the execution stream."""

    event: str  # "system", "assistant", "user", "result", "error"
    data: dict


@dataclass
class SandshoreResult:
    """Final result from executing a step."""

    text: str = ""
    structured_output: dict | None = None
    total_cost_usd: float = 0.0
    num_turns: int = 0


class SandshoreError(Exception):
    """Error from the Sandshore runtime."""


class SandshoreRuntime:
    """Unified runtime - direct E2B SDK or HTTP proxy fallback.

    When ``e2b_api_key`` is set and the ``e2b`` package is available,
    sandboxes are created directly via the E2B Python SDK.  Otherwise,
    falls back to the legacy HTTP proxy (Sandstorm-compatible endpoint).
    """

    def __init__(
        self,
        anthropic_api_key: str,
        e2b_api_key: str,
        proxy_url: str | None = None,
        timeout: float = 300.0,
        template: str = "",
        max_concurrent: int = 5,
    ) -> None:
        self.anthropic_api_key = anthropic_api_key
        self.e2b_api_key = e2b_api_key
        self.proxy_url = proxy_url.rstrip("/") if proxy_url else None
        self.timeout = timeout
        self.template = template
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._http: httpx.AsyncClient | None = None
        self._use_direct = self._can_use_direct()

    def _can_use_direct(self) -> bool:
        """Check if direct E2B SDK mode is available."""
        if not self.e2b_api_key:
            return False
        try:
            import e2b  # noqa: F401
            return True
        except ImportError:
            return False

    async def close(self) -> None:
        """Close underlying HTTP client (proxy mode only)."""
        if self._http:
            await self._http.aclose()
            self._http = None

    async def health(self) -> bool:
        """Check runtime health."""
        if self._use_direct:
            return bool(self.e2b_api_key and self.anthropic_api_key)
        if self.proxy_url:
            try:
                client = await self._get_http()
                resp = await client.get(f"{self.proxy_url}/health")
                return resp.status_code == 200
            except httpx.HTTPError:
                return False
        return False

    async def query(
        self,
        request: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> SandshoreResult:
        """Execute a query and return the final aggregated result."""
        result = SandshoreResult()
        assistant_texts: list[str] = []

        async for event in self.query_stream(request, cancel_event=cancel_event):
            evt_type = event.data.get("type", event.event)

            if evt_type == "result":
                result.text = (
                    event.data.get("result", "")
                    or event.data.get("text", "")
                )
                result.structured_output = event.data.get("structured_output")
                result.total_cost_usd = event.data.get("total_cost_usd", 0.0)
                result.num_turns = event.data.get("num_turns", 0)
                if not result.text:
                    logger.debug(
                        "Result event has no text. Keys: %s, turns=%d, cost=%.4f",
                        list(event.data.keys()),
                        result.num_turns,
                        result.total_cost_usd,
                    )
            elif evt_type == "error":
                error_msg = event.data.get("error", "Unknown runtime error")
                raise SandshoreError(error_msg)
            elif evt_type in ("assistant", "message"):
                text = _extract_text(event.data)
                if text:
                    assistant_texts.append(text)

        # Fallback: use last assistant message if result text is empty
        if not result.text and assistant_texts:
            result.text = assistant_texts[-1]
            logger.info(
                "Using last assistant message as result text (%d chars, "
                "%d total messages)",
                len(result.text),
                len(assistant_texts),
            )

        return result

    async def query_stream(
        self,
        request: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Execute a query and yield SSE events as they stream."""
        if self._use_direct:
            async for event in self._stream_direct(
                request, cancel_event=cancel_event
            ):
                yield event
        elif self.proxy_url:
            async for event in self._stream_proxy(request):
                yield event
        else:
            raise SandshoreError(
                "No runtime configured. Set E2B_API_KEY for direct mode "
                "or provide a proxy URL."
            )

    # ------------------------------------------------------------------
    # Direct E2B SDK mode
    # ------------------------------------------------------------------

    async def _stream_direct(
        self,
        request: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Create an E2B sandbox and stream execution events."""
        from e2b import AsyncSandbox

        queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=1000)
        sandbox = None

        def on_stdout(data: Any) -> None:
            line = data.line if hasattr(data, "line") else str(data)
            try:
                parsed = json.loads(line)
                event = SSEEvent(
                    event=parsed.get("type", "message"),
                    data=parsed,
                )
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full, dropping event")
            except (json.JSONDecodeError, ValueError):
                logger.debug("Non-JSON stdout: %s", line[:200])

        def on_stderr(data: Any) -> None:
            line = data.line if hasattr(data, "line") else str(data)
            logger.debug("Sandbox stderr: %s", line[:500])

        try:
            async with self._semaphore:
                sandbox_kwargs: dict[str, Any] = {
                    "api_key": self.e2b_api_key,
                    "timeout": int(self.timeout),
                    "envs": {
                        "ANTHROPIC_API_KEY": self.anthropic_api_key,
                        "SANDCASTLE_REQUEST": json.dumps(request),
                    },
                }
                if self.template:
                    sandbox_kwargs["template"] = self.template

                sandbox = await AsyncSandbox.create(**sandbox_kwargs)

                if not self.template:
                    # Upload runner script
                    runner_code = _get_runner_mjs()
                    await sandbox.files.write(
                        "/home/user/runner.mjs", runner_code
                    )

                    # Install Claude Agent SDK if not in template
                    await sandbox.commands.run(
                        "npm install @anthropic-ai/claude-agent-sdk"
                        " 2>/dev/null || true",
                        timeout=60,
                    )

                # Run the agent
                handle = await sandbox.commands.run(
                    "node /home/user/runner.mjs",
                    background=True,
                    on_stdout=on_stdout,
                    on_stderr=on_stderr,
                    cwd="/home/user",
                    timeout=int(self.timeout),
                )

                # Yield events from the queue as they arrive
                cancelled = False
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        logger.info("Cancellation requested, stopping sandbox")
                        cancelled = True
                        break

                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=2.0
                        )
                        if event is None:
                            break
                        yield event
                    except asyncio.TimeoutError:
                        # Check if process finished
                        if handle.exit_code is not None:
                            break
                        continue

                if cancelled:
                    # Kill sandbox immediately on cancellation
                    if sandbox:
                        try:
                            await sandbox.kill()
                        except Exception:
                            pass
                        sandbox = None
                    return

                # Drain remaining events
                while not queue.empty():
                    event = queue.get_nowait()
                    if event is not None:
                        yield event

                await handle.wait()

        except SandshoreError:
            raise
        except Exception as e:
            raise SandshoreError(
                f"E2B sandbox execution failed: {e}"
            ) from e
        finally:
            if sandbox:
                try:
                    await sandbox.kill()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # HTTP proxy fallback (Sandstorm-compatible)
    # ------------------------------------------------------------------

    async def _stream_proxy(self, request: dict) -> AsyncIterator[SSEEvent]:
        """Stream events via HTTP proxy (legacy Sandstorm mode)."""
        payload = {
            "prompt": request["prompt"],
            "anthropic_api_key": self.anthropic_api_key,
            "e2b_api_key": self.e2b_api_key,
        }

        for key in ("model", "max_turns", "timeout", "output_format"):
            if key in request:
                payload[key] = request[key]

        client = await self._get_http()
        async with aconnect_sse(
            client,
            "POST",
            f"{self.proxy_url}/query",
            json=payload,
        ) as event_source:
            async for sse in event_source.aiter_sse():
                try:
                    data = json.loads(sse.data) if sse.data else {}
                except json.JSONDecodeError:
                    data = {"raw": sse.data}
                yield SSEEvent(event=sse.event or "message", data=data)

    async def _get_http(self) -> httpx.AsyncClient:
        """Lazy-init HTTP client for proxy mode."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout)
            )
        return self._http


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------


def _extract_text(data: dict) -> str:
    """Extract text content from various message formats."""
    for key in ("text", "content", "result", "data"):
        if key in data and isinstance(data[key], str) and data[key].strip():
            return data[key]

    # Nested message.content structure
    msg = data.get("message", {})
    if isinstance(msg, dict):
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    return t

    # Content blocks
    for block in data.get("content_blocks", []):
        if isinstance(block, dict) and block.get("text"):
            return block["text"]

    # AssistantMessage content array
    content = data.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")

    return ""


def _get_runner_mjs() -> str:
    """Load the bundled runner.mjs script."""
    if _RUNNER_MJS_PATH.exists():
        return _RUNNER_MJS_PATH.read_text()

    # Inline fallback if file not found in package
    return '''
import { query } from "@anthropic-ai/claude-agent-sdk";

const request = JSON.parse(process.env.SANDCASTLE_REQUEST);

const options = {
    allowedTools: ["Bash","Read","Write","Edit","Glob","Grep","WebSearch","WebFetch"],
    permissionMode: "bypassPermissions",
    model: request.model || "sonnet",
    maxTurns: request.max_turns || 10,
};
if (request.output_format) options.outputFormat = request.output_format;
if (request.max_budget_usd) options.maxBudgetUsd = request.max_budget_usd;

for await (const message of query({
    prompt: request.prompt,
    options,
})) {
    process.stdout.write(JSON.stringify(message) + "\\n");
}
'''


# ------------------------------------------------------------------
# Singleton pool
# ------------------------------------------------------------------

_client_pool: dict[tuple[str, str, str], SandshoreRuntime] = {}


def get_sandshore_runtime(
    anthropic_api_key: str,
    e2b_api_key: str,
    proxy_url: str | None = None,
    timeout: float = 300.0,
    template: str = "",
    max_concurrent: int = 5,
) -> SandshoreRuntime:
    """Return a shared SandshoreRuntime instance."""
    key = (anthropic_api_key, e2b_api_key, template or "")
    client = _client_pool.get(key)
    if client is None:
        client = SandshoreRuntime(
            anthropic_api_key=anthropic_api_key,
            e2b_api_key=e2b_api_key,
            proxy_url=proxy_url,
            timeout=timeout,
            template=template,
            max_concurrent=max_concurrent,
        )
        _client_pool[key] = client
    return client


# ------------------------------------------------------------------
# Backward compatibility aliases
# ------------------------------------------------------------------

# These aliases let existing code (tests, imports) keep working
# without changes. They will be removed in a future version.
SandstormClient = SandshoreRuntime
SandstormResult = SandshoreResult
SandstormError = SandshoreError
get_sandstorm_client = get_sandshore_runtime
