"""Sandshore runtime - pluggable sandbox execution for Sandcastle.

Supports multiple sandbox backends (E2B, Docker, Local, Cloudflare)
through the ``SandboxBackend`` protocol defined in ``backends.py``.

When no backend is available, falls back to the legacy HTTP proxy
mode for backward compatibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from httpx_sse import aconnect_sse

from sandcastle.engine.backends import SandboxBackend, SSEEvent, create_backend
from sandcastle.engine.providers import get_failover

logger = logging.getLogger(__name__)


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
    """Unified runtime with pluggable sandbox backends.

    Resolves the sandbox backend from config (``sandbox_backend`` setting)
    and delegates execution.  Falls back to the legacy HTTP proxy when
    no backend is healthy and a ``proxy_url`` is configured.
    """

    def __init__(
        self,
        anthropic_api_key: str,
        e2b_api_key: str,
        proxy_url: str | None = None,
        timeout: float = 300.0,
        template: str = "",
        max_concurrent: int = 5,
        sandbox_backend: str = "e2b",
        docker_image: str = "sandcastle-runner:latest",
        docker_url: str | None = None,
        cloudflare_worker_url: str = "",
    ) -> None:
        self.anthropic_api_key = anthropic_api_key
        self.e2b_api_key = e2b_api_key
        self.proxy_url = proxy_url.rstrip("/") if proxy_url else None
        self.timeout = timeout
        self.template = template
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._http: httpx.AsyncClient | None = None
        self._sandbox_backend_type = sandbox_backend

        # Cached health check result (value, timestamp)
        self._health_cache: tuple[bool, float] = (False, 0.0)
        self._health_cache_ttl = 60.0  # seconds

        # Create the pluggable backend
        self._backend: SandboxBackend = create_backend(
            sandbox_backend,
            e2b_api_key=e2b_api_key,
            template=template,
            docker_image=docker_image,
            docker_url=docker_url or None,
            cloudflare_worker_url=cloudflare_worker_url,
            timeout=timeout,
        )

    @property
    def backend_name(self) -> str:
        """Return the name of the active sandbox backend."""
        return self._backend.name

    async def close(self) -> None:
        """Close underlying resources."""
        await self._backend.close()
        if self._http:
            await self._http.aclose()
            self._http = None

    async def health(self) -> bool:
        """Check runtime health via the active backend."""
        backend_ok = await self._backend.health()
        if backend_ok:
            return True
        # Fallback: check proxy if available
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

    async def _cached_health(self) -> bool:
        """Return cached health check, refreshing if stale."""
        cached_result, cached_at = self._health_cache
        if time.monotonic() - cached_at < self._health_cache_ttl:
            return cached_result
        result = await self._backend.health()
        self._health_cache = (result, time.monotonic())
        return result

    async def query_stream(
        self,
        request: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Execute a query and yield SSE events as they stream."""
        backend_healthy = await self._cached_health()

        if backend_healthy:
            async for event in self._stream_backend(
                request, cancel_event=cancel_event
            ):
                yield event
        elif self.proxy_url:
            async for event in self._stream_proxy(request):
                yield event
        else:
            raise SandshoreError(
                f"Sandbox backend '{self._sandbox_backend_type}' is not "
                f"available and no proxy URL is configured."
            )

    # ------------------------------------------------------------------
    # Backend delegation
    # ------------------------------------------------------------------

    def _build_env(self, request: dict) -> tuple[dict[str, str], str, bool]:
        """Build environment variables and resolve runner info for request.

        Returns (envs, runner_file, use_claude_runner).
        """
        from sandcastle.engine.providers import (
            get_api_key,
            resolve_model,
        )

        model_str = request.get("model", "sonnet")
        try:
            model_info = resolve_model(model_str)
        except KeyError:
            logger.warning("Unknown model '%s', falling back to sonnet", model_str)
            model_str = "sonnet"
            model_info = resolve_model(model_str)

        use_claude_runner = model_info.provider == "claude"
        runner_file = model_info.runner

        envs: dict[str, str] = {
            "SANDCASTLE_REQUEST": json.dumps(request),
        }

        if use_claude_runner:
            envs["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        else:
            model_api_key = get_api_key(model_info)
            envs["MODEL_API_KEY"] = model_api_key
            envs["MODEL_ID"] = model_info.api_model_id
            envs["MODEL_INPUT_PRICE"] = str(model_info.input_price_per_m)
            envs["MODEL_OUTPUT_PRICE"] = str(model_info.output_price_per_m)
            if model_info.api_base_url:
                envs["MODEL_BASE_URL"] = model_info.api_base_url

        return envs, runner_file, use_claude_runner

    @staticmethod
    def _is_retriable_provider_error(error_msg: str) -> bool:
        """Return True if *error_msg* indicates a retriable provider error (429/5xx)."""
        msg = error_msg.lower()
        # Rate limit patterns
        if "429" in msg or "rate limit" in msg or "too many requests" in msg:
            return True
        # 5xx patterns
        if re.search(r"\b50[0-4]\b", msg):
            return True
        if "server error" in msg or "overloaded" in msg or "capacity" in msg:
            return True
        return False

    async def _stream_backend_once(
        self,
        request: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Execute via the pluggable backend with semaphore + cancellation.

        Detects retriable SSE error events and raises ``SandshoreError``
        instead of yielding them, so the failover wrapper can catch and retry.
        """
        envs, runner_file, use_claude_runner = self._build_env(request)

        try:
            async with self._semaphore:
                cancelled = False
                async for event in self._backend.start(
                    runner_file=runner_file,
                    envs=envs,
                    use_claude_runner=use_claude_runner,
                    timeout=self.timeout,
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        logger.info("Cancellation requested, stopping backend")
                        cancelled = True
                        break

                    # Detect retriable provider errors in SSE error events
                    if event.event == "error" or event.data.get("type") == "error":
                        error_msg = event.data.get("error", "")
                        if self._is_retriable_provider_error(error_msg):
                            raise SandshoreError(error_msg)

                    yield event

                if cancelled:
                    return

        except SandshoreError:
            raise
        except Exception as e:
            import traceback
            logger.error(
                "Backend '%s' raised %s: %s\n%s",
                self._backend.name,
                type(e).__name__,
                e,
                traceback.format_exc(),
            )
            raise SandshoreError(
                f"Sandbox backend '{self._backend.name}' execution failed: {e}"
            ) from e

    async def _stream_backend(
        self,
        request: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Execute with automatic model failover on retriable errors."""
        from sandcastle.engine.providers import resolve_model

        model_str = request.get("model", "sonnet")
        failover = get_failover()

        # Try primary model
        try:
            async for event in self._stream_backend_once(
                request, cancel_event=cancel_event
            ):
                yield event
            return
        except SandshoreError as exc:
            if not self._is_retriable_provider_error(str(exc)):
                raise
            # Mark the primary model's key on cooldown
            try:
                info = resolve_model(model_str)
                failover.mark_cooldown(info.api_key_env)
                logger.warning(
                    "Model '%s' hit retriable error: %s - trying alternatives",
                    model_str, exc,
                )
            except KeyError:
                raise exc

        # Try alternatives
        alternatives = failover.get_alternatives(model_str)
        if not alternatives:
            raise SandshoreError(
                f"Model '{model_str}' is rate-limited and no alternatives are available"
            )

        last_error: SandshoreError | None = None
        for alt_model in alternatives:
            alt_request = {**request, "model": alt_model}
            try:
                logger.info("Failing over from '%s' to '%s'", model_str, alt_model)
                async for event in self._stream_backend_once(
                    alt_request, cancel_event=cancel_event
                ):
                    yield event
                return
            except SandshoreError as exc:
                last_error = exc
                if self._is_retriable_provider_error(str(exc)):
                    try:
                        alt_info = resolve_model(alt_model)
                        failover.mark_cooldown(alt_info.api_key_env)
                    except KeyError:
                        pass
                    logger.warning(
                        "Alternative '%s' also failed: %s", alt_model, exc,
                    )
                    continue
                raise

        raise SandshoreError(
            f"All failover alternatives exhausted for '{model_str}': {last_error}"
        )

    # ------------------------------------------------------------------
    # HTTP proxy fallback
    # ------------------------------------------------------------------

    async def _stream_proxy(self, request: dict) -> AsyncIterator[SSEEvent]:
        """Stream events via HTTP proxy (legacy fallback mode)."""
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


# ------------------------------------------------------------------
# Singleton pool
# ------------------------------------------------------------------

_client_pool: dict[tuple[str, ...], SandshoreRuntime] = {}


def get_sandshore_runtime(
    anthropic_api_key: str,
    e2b_api_key: str,
    proxy_url: str | None = None,
    timeout: float = 300.0,
    template: str = "",
    max_concurrent: int = 5,
    sandbox_backend: str = "e2b",
    docker_image: str = "sandcastle-runner:latest",
    docker_url: str | None = None,
    cloudflare_worker_url: str = "",
) -> SandshoreRuntime:
    """Return a shared SandshoreRuntime instance."""
    key = (anthropic_api_key, e2b_api_key, template or "", sandbox_backend)
    client = _client_pool.get(key)
    if client is None:
        client = SandshoreRuntime(
            anthropic_api_key=anthropic_api_key,
            e2b_api_key=e2b_api_key,
            proxy_url=proxy_url,
            timeout=timeout,
            template=template,
            max_concurrent=max_concurrent,
            sandbox_backend=sandbox_backend,
            docker_image=docker_image,
            docker_url=docker_url,
            cloudflare_worker_url=cloudflare_worker_url,
        )
        _client_pool[key] = client
    return client


