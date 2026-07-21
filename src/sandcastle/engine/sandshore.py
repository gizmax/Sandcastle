"""Sandshore runtime - pluggable sandbox execution for Sandcastle.

Supports multiple sandbox backends (E2B, Docker, Local, Cloudflare)
through the ``SandboxBackend`` protocol defined in ``backends.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from sandcastle.engine.backends import SandboxBackend, SSEEvent, create_backend
from sandcastle.engine.providers import get_failover

logger = logging.getLogger(__name__)


_REGION_ALLOWANCES = {"eu": {"eu", "local"}, "local": {"local"}}


def is_region_allowed(data_residency: str, region: str) -> bool:
    """Return whether ``region`` satisfies a configured residency constraint."""
    allowed_regions = _REGION_ALLOWANCES.get(data_residency)
    return allowed_regions is None or region in allowed_regions


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass
class SandshoreResult:
    """Final result from executing a step."""

    text: str = ""
    structured_output: dict | None = None
    total_cost_usd: float = 0.0
    num_turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class RuntimeMetrics:
    """Lightweight metrics for runtime observability."""

    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    failover_attempts: int = 0
    circuit_breaker_rejections: int = 0
    total_cost_usd: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def record_query_success(self, cost_usd: float = 0.0) -> None:
        """Record a successful query execution."""
        async with self._lock:
            self.total_queries += 1
            self.successful_queries += 1
            self.total_cost_usd += cost_usd

    async def record_query_failure(self) -> None:
        """Record a failed query execution."""
        async with self._lock:
            self.total_queries += 1
            self.failed_queries += 1

    async def record_failover(self) -> None:
        """Record a failover attempt to an alternative model."""
        async with self._lock:
            self.failover_attempts += 1

    async def record_circuit_breaker_rejection(self) -> None:
        """Record a request rejected by the circuit breaker."""
        async with self._lock:
            self.circuit_breaker_rejections += 1

    def snapshot(self) -> dict:
        """Return a point-in-time snapshot of metrics (not locked)."""
        return {
            "total_queries": self.total_queries,
            "successful_queries": self.successful_queries,
            "failed_queries": self.failed_queries,
            "failover_attempts": self.failover_attempts,
            "circuit_breaker_rejections": self.circuit_breaker_rejections,
            "total_cost_usd": round(self.total_cost_usd, 6),
        }


# ------------------------------------------------------------------
# Circuit breaker
# ------------------------------------------------------------------


class CircuitBreaker:
    """Circuit breaker for backend failure detection.

    Three states:
    - CLOSED: normal operation, requests pass through.
    - OPEN: backend is failing, reject immediately.
    - HALF_OPEN: cooldown expired, allow exactly one test request.

    The half-open state is properly guarded: only a single probe request
    is allowed through. If it succeeds the breaker resets to CLOSED; if
    it fails the breaker trips back to OPEN immediately.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_permitted = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Return the current effective state, accounting for recovery timeout.

        Note: this is a snapshot for observability. Use ``allow_request()``
        for the authoritative gate decision (it holds the lock).
        """
        if self._state == self.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                return self.HALF_OPEN
        return self._state

    async def record_success(self) -> None:
        """Record a successful execution - reset to CLOSED."""
        async with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED
            self._half_open_permitted = False

    async def record_failure(self) -> None:
        """Record a failed execution - trip to OPEN after threshold.

        In HALF_OPEN state a single failure trips back to OPEN immediately
        with the failure counter preserved (so recovery_timeout restarts).
        """
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            self._half_open_permitted = False
            if self._state == self.HALF_OPEN:
                # Probe failed - trip back to OPEN immediately
                logger.warning(
                    "Circuit breaker half-open probe failed, "
                    "tripping back to OPEN (failures=%d)",
                    self._failure_count,
                )
                self._state = self.OPEN
            elif self._failure_count >= self.failure_threshold:
                if self._state != self.OPEN:
                    logger.warning(
                        "Circuit breaker tripped OPEN after %d consecutive failures",
                        self._failure_count,
                    )
                self._state = self.OPEN

    async def allow_request(self) -> bool:
        """Return True if a request should be allowed through.

        Holds the lock so that only one request passes through in
        HALF_OPEN state (the probe request).
        """
        async with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    # Transition to HALF_OPEN and allow exactly one probe
                    if not self._half_open_permitted:
                        self._state = self.HALF_OPEN
                        self._half_open_permitted = True
                        return True
                    # Another request while probe is in-flight - reject
                    return False
                return False
            # HALF_OPEN but probe already dispatched
            if self._half_open_permitted:
                return False
            self._half_open_permitted = True
            return True


# ------------------------------------------------------------------
# Errors
# ------------------------------------------------------------------


class SandshoreError(Exception):
    """Error from the Sandshore runtime."""


# ------------------------------------------------------------------
# Runtime
# ------------------------------------------------------------------


class SandshoreRuntime:
    """Unified runtime with pluggable sandbox backends.

    Resolves the sandbox backend from config (``sandbox_backend`` setting)
    and delegates execution to the appropriate backend.
    """

    def __init__(
        self,
        anthropic_api_key: str,
        e2b_api_key: str,
        timeout: float = 300.0,
        template: str = "",
        max_concurrent: int = 5,
        sandbox_backend: str = "e2b",
        docker_image: str = "sandcastle-runner:latest",
        docker_url: str | None = None,
        cloudflare_worker_url: str = "",
        # Kept for backward compatibility - ignored
        proxy_url: str | None = None,
    ) -> None:
        self.anthropic_api_key = anthropic_api_key
        self.e2b_api_key = e2b_api_key
        self.timeout = timeout
        self.template = template
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._sandbox_backend_type = sandbox_backend
        self._docker_image = docker_image
        self._docker_url = docker_url
        self._cloudflare_worker_url = cloudflare_worker_url
        self._in_flight_queries = 0

        # Cached health check result (value, timestamp) with lock
        self._health_cache: tuple[bool, float] = (False, 0.0)
        self._health_cache_ttl = 60.0  # seconds
        self._health_lock = asyncio.Lock()

        # Circuit breaker for backend failures
        self._circuit_breaker = CircuitBreaker()

        # Runtime metrics
        self._metrics = RuntimeMetrics()

        # Create the pluggable backend
        from sandcastle.config import settings as _cfg

        self._backend: SandboxBackend = create_backend(
            sandbox_backend,
            e2b_api_key=e2b_api_key,
            template=template,
            docker_image=docker_image,
            docker_url=docker_url or None,
            docker_seccomp_profile=_cfg.docker_seccomp_profile,
            docker_pids_limit=_cfg.docker_pids_limit,
            docker_cpu_period=_cfg.docker_cpu_period,
            docker_cpu_quota=_cfg.docker_cpu_quota,
            cloudflare_worker_url=cloudflare_worker_url,
            timeout=timeout,
        )

    @property
    def backend_name(self) -> str:
        """Return the name of the active sandbox backend."""
        return self._backend.name

    @property
    def metrics(self) -> RuntimeMetrics:
        """Return the runtime metrics collector."""
        return self._metrics

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Return the circuit breaker instance."""
        return self._circuit_breaker

    async def close(self) -> None:
        """Close underlying resources."""
        await self._backend.close()

    async def __aenter__(self) -> SandshoreRuntime:
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context manager - close resources."""
        await self.close()

    async def health(self) -> bool:
        """Check runtime health via the active backend."""
        return await self._backend.health()

    async def query(
        self,
        request: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> SandshoreResult:
        """Execute a query and return the final aggregated result."""
        result = SandshoreResult()
        assistant_texts: list[str] = []

        try:
            async for event in self.query_stream(
                request, cancel_event=cancel_event
            ):
                evt_type = event.data.get("type", event.event)

                if evt_type == "result":
                    result.text = (
                        event.data.get("result", "")
                        or event.data.get("text", "")
                    )
                    result.structured_output = event.data.get(
                        "structured_output"
                    )
                    result.total_cost_usd = event.data.get(
                        "total_cost_usd", 0.0
                    )
                    result.num_turns = event.data.get("num_turns", 0)
                    result.input_tokens = event.data.get(
                        "input_tokens", 0
                    )
                    result.output_tokens = event.data.get(
                        "output_tokens", 0
                    )
                    if not result.text:
                        logger.debug(
                            "Result event has no text. Keys: %s, "
                            "turns=%d, cost=%.4f",
                            list(event.data.keys()),
                            result.num_turns,
                            result.total_cost_usd,
                        )
                elif evt_type == "error":
                    error_msg = event.data.get(
                        "error", "Unknown runtime error"
                    )
                    raise SandshoreError(error_msg)
                elif evt_type in ("assistant", "message"):
                    text = _extract_text(event.data)
                    if text:
                        assistant_texts.append(text)

            # Fallback: use last assistant message if result text is empty
            if not result.text and assistant_texts:
                result.text = assistant_texts[-1]
                logger.info(
                    "Using last assistant message as result text "
                    "(%d chars, %d total messages)",
                    len(result.text),
                    len(assistant_texts),
                )

            await self._metrics.record_query_success(result.total_cost_usd)

        except Exception:
            await self._metrics.record_query_failure()
            raise

        return result

    async def _cached_health(self) -> bool:
        """Return cached health check, refreshing if stale.

        Uses a lock to prevent thundering herd on concurrent health checks.
        """
        async with self._health_lock:
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
        self._in_flight_queries = getattr(self, "_in_flight_queries", 0) + 1
        try:
            backend_healthy = await self._cached_health()

            if backend_healthy:
                async for event in self._stream_backend(
                    request, cancel_event=cancel_event
                ):
                    yield event
            else:
                raise SandshoreError(
                    f"Sandbox backend '{self._sandbox_backend_type}' is not "
                    f"available."
                )
        finally:
            self._in_flight_queries -= 1

    async def sandbox_exec(
        self,
        sandbox: Any,
        cmd: str,
        args: list[str],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Run ``cmd args`` inside the sandbox backend; return its output dict.

        Used by browser dom/computer_use/lightpanda modes to install/run setup
        scripts (e.g. ``npx playwright install`` then a ``node`` script). Returns
        ``{"stdout", "stderr", "exit_code"}``.

        ``sandbox`` is the runtime the caller already holds; it is accepted for
        call-site compatibility and execution always routes through this runtime's
        backend. Only backends that expose ``exec_command`` (currently ``local``)
        support this; others raise a clear error instead of failing obscurely. A
        persistent-sandbox exec path for E2B/Docker is a follow-up.
        """
        exec_command = getattr(self._backend, "exec_command", None)
        if exec_command is None:
            raise SandshoreError(
                f"sandbox_exec is not supported on the '{self._backend.name}' "
                "backend (it needs a persistent sandbox exec primitive). Use "
                "SANDCASTLE_BACKEND=local for browser dom/computer_use/lightpanda."
            )
        return await exec_command(cmd, args, timeout=timeout)

    # ------------------------------------------------------------------
    # Backend delegation
    # ------------------------------------------------------------------

    def _build_env(
        self, request: dict
    ) -> tuple[dict[str, str], str, bool, dict[str, str]]:
        """Build environment variables and resolve runner info for request.

        Returns (envs, runner_file, use_claude_runner, tool_files).
        """
        from sandcastle.engine.providers import (
            effective_model,
            get_api_key,
            resolve_base_url,
            resolve_model,
        )

        # Bare-default steps honour workflow_default_model / Spark autoroute here
        # too - sandboxed agent steps otherwise pick the Claude runner on a box
        # whose default is a local model, and fail without an Anthropic login.
        model_str = effective_model(request.get("model", "sonnet"))
        try:
            model_info = resolve_model(model_str)
        except KeyError:
            logger.warning(
                "Unknown model '%s', falling back to sonnet", model_str
            )
            model_str = "sonnet"
            model_info = resolve_model(model_str)

        use_claude_runner = model_info.provider == "claude"
        runner_file = model_info.runner

        envs: dict[str, str] = {
            "SANDCASTLE_REQUEST": json.dumps(request),
        }

        # Always pass pricing so both runners can calculate cost
        envs["MODEL_INPUT_PRICE"] = str(model_info.input_price_per_m)
        envs["MODEL_OUTPUT_PRICE"] = str(model_info.output_price_per_m)

        # Pin a low sampling temperature so steps are deterministic and don't
        # garble on endpoints that default to 1.0. Per-request override wins.
        from sandcastle.config import settings as _settings

        envs["STEP_TEMPERATURE"] = str(request.get("temperature", _settings.step_temperature))

        if use_claude_runner:
            envs["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        else:
            model_api_key = get_api_key(model_info)
            # Key-less local providers (nim/ollama/omlx): the OpenAI client
            # CONSTRUCTOR throws on an empty apiKey, crashing the runner before
            # it can emit a single event - which surfaced as steps "completing"
            # with empty output. Any non-empty placeholder satisfies it.
            envs["MODEL_API_KEY"] = model_api_key or "no-key-required"
            envs["MODEL_ID"] = model_info.api_model_id
            base_url = resolve_base_url(model_info)
            if base_url:
                envs["MODEL_BASE_URL"] = base_url

        # Inject tool credentials and bundle tool files
        tool_files: dict[str, str] = {}
        tools = request.get("tools", [])
        if tools:
            from sandcastle.engine.tools.credentials import get_tool_credentials
            from sandcastle.engine.tools.loader import bundle_tool_files

            tool_creds = get_tool_credentials(tools)
            envs.update(tool_creds)
            tool_files = bundle_tool_files(tools)

        return envs, runner_file, use_claude_runner, tool_files

    @staticmethod
    def _is_retriable_provider_error(error_msg: str) -> bool:
        """Return True if error_msg indicates a retriable provider error."""
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

    @staticmethod
    def _is_rate_limit_error(error_msg: str) -> bool:
        """Return True if error_msg specifically indicates a 429 rate limit."""
        msg = error_msg.lower()
        return "429" in msg or "rate limit" in msg or "too many requests" in msg

    async def _stream_backend_once(
        self,
        request: dict,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Execute via the pluggable backend with semaphore + cancellation.

        Checks the circuit breaker before executing. On success resets it,
        on failure records the failure. Detects retriable SSE error events
        and raises ``SandshoreError`` so the failover wrapper can retry.
        """
        # Check circuit breaker
        if not await self._circuit_breaker.allow_request():
            await self._metrics.record_circuit_breaker_rejection()
            raise SandshoreError(
                f"Circuit breaker is OPEN for backend "
                f"'{self._backend.name}' - rejecting request"
            )

        envs, runner_file, use_claude_runner, tool_files = self._build_env(request)

        try:
            async with self._semaphore:
                cancelled = False
                async for event in self._backend.start(
                    runner_file=runner_file,
                    envs=envs,
                    use_claude_runner=use_claude_runner,
                    timeout=self.timeout,
                    tool_files=tool_files or None,
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        logger.info(
                            "Cancellation requested, stopping backend"
                        )
                        cancelled = True
                        break

                    # Detect retriable provider errors in SSE error events
                    if (
                        event.event == "error"
                        or event.data.get("type") == "error"
                    ):
                        error_msg = event.data.get("error", "")
                        if self._is_retriable_provider_error(error_msg):
                            await self._circuit_breaker.record_failure()
                            raise SandshoreError(error_msg)

                    yield event

                if cancelled:
                    return

            # Successful execution - reset circuit breaker
            await self._circuit_breaker.record_success()

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
            await self._circuit_breaker.record_failure()
            raise SandshoreError(
                f"Sandbox backend '{self._backend.name}' "
                f"execution failed: {e}"
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

        # Enforce data residency constraint on the primary model
        from sandcastle.config import settings as _settings
        _residency = _settings.data_residency
        if _residency:
            try:
                _model_info = resolve_model(model_str)
                if not is_region_allowed(_residency, _model_info.region):
                    raise SandshoreError(
                        f"Data residency mode '{_residency}' is active. "
                        f"Model '{model_str}' runs in region '{_model_info.region}', "
                        f"which does not satisfy the residency requirement. "
                        f"Choose a model in the '{_residency}' region."
                    )
            except KeyError:
                pass  # Unknown model - let the backend handle it

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
            rate_limited = self._is_rate_limit_error(str(exc))
            try:
                info = resolve_model(model_str)
                failover.mark_cooldown(
                    info.api_key_env, is_rate_limit=rate_limited,
                )
                logger.warning(
                    "Model '%s' hit %s error: %s - trying alternatives",
                    model_str,
                    "rate-limit" if rate_limited else "server",
                    exc,
                )
            except KeyError:
                raise exc

        # Try alternatives, respecting data residency if configured
        from sandcastle.config import settings as _settings
        _residency = _settings.data_residency

        alternatives = failover.get_alternatives(model_str, data_residency=_residency)
        if not alternatives:
            if _residency:
                raise SandshoreError(
                    f"Model '{model_str}' is unavailable and no alternatives "
                    f"are available in the '{_residency}' region. "
                    f"Data residency mode prevents cross-region failover."
                )
            raise SandshoreError(
                f"Model '{model_str}' is rate-limited and no "
                f"alternatives are available"
            )

        last_error: SandshoreError | None = None
        for alt_model in alternatives:
            await self._metrics.record_failover()
            alt_request = {**request, "model": alt_model}
            try:
                logger.info(
                    "Failing over from '%s' to '%s'", model_str, alt_model
                )
                async for event in self._stream_backend_once(
                    alt_request, cancel_event=cancel_event
                ):
                    yield event
                return
            except SandshoreError as exc:
                last_error = exc
                if self._is_retriable_provider_error(str(exc)):
                    alt_rate_limited = self._is_rate_limit_error(str(exc))
                    try:
                        alt_info = resolve_model(alt_model)
                        failover.mark_cooldown(
                            alt_info.api_key_env,
                            is_rate_limit=alt_rate_limited,
                        )
                    except KeyError:
                        pass
                    logger.warning(
                        "Alternative '%s' also failed: %s",
                        alt_model,
                        exc,
                    )
                    continue
                raise

        raise SandshoreError(
            f"All failover alternatives exhausted for "
            f"'{model_str}': {last_error}"
        )


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
# Singleton pool with thread safety
# ------------------------------------------------------------------

_client_pool: dict[tuple[str, ...], SandshoreRuntime] = {}
_pool_lock = threading.Lock()
_MAX_POOL_SIZE = 20  # Prevent unbounded pool growth


def get_sandshore_runtime(
    anthropic_api_key: str,
    e2b_api_key: str,
    timeout: float = 300.0,
    template: str = "",
    max_concurrent: int = 5,
    sandbox_backend: str = "e2b",
    docker_image: str = "sandcastle-runner:latest",
    docker_url: str | None = None,
    cloudflare_worker_url: str = "",
    # Kept for backward compatibility - ignored
    proxy_url: str | None = None,
) -> SandshoreRuntime:
    """Return a shared SandshoreRuntime instance.

    The pool key includes all backend-specific parameters to ensure
    different configurations yield separate runtime instances.
    Pool is capped at ``_MAX_POOL_SIZE`` entries to prevent memory leaks.
    """
    key = (
        anthropic_api_key,
        e2b_api_key,
        template or "",
        sandbox_backend,
        docker_image,
        docker_url or "",
        cloudflare_worker_url or "",
        str(timeout),
        str(max_concurrent),
    )
    with _pool_lock:
        client = _client_pool.get(key)
        if client is None:
            # Evict oldest entry if pool is at capacity
            while len(_client_pool) >= _MAX_POOL_SIZE:
                oldest_key = next(
                    (
                        pool_key
                        for pool_key, runtime in _client_pool.items()
                        if not getattr(runtime, "_in_flight_queries", 0)
                    ),
                    None,
                )
                if oldest_key is None:
                    logger.warning(
                        "Runtime pool at capacity (%d), but all runtimes have active queries; "
                        "temporarily exceeding capacity",
                        _MAX_POOL_SIZE,
                    )
                    break
                evicted = _client_pool.pop(oldest_key)
                logger.warning(
                    "Runtime pool at capacity (%d), evicting inactive entry",
                    _MAX_POOL_SIZE,
                )
                # Best-effort close - can't await in sync context.
                # Use get_running_loop() instead of deprecated
                # get_event_loop() to avoid DeprecationWarning in
                # Python 3.12+.
                try:
                    loop = asyncio.get_running_loop()
                    _task = loop.create_task(evicted.close())

                    def _log_close_error(task: asyncio.Task) -> None:
                        if task.cancelled():
                            return
                        try:
                            task.result()
                        except Exception as exc:
                            logger.warning("Error closing evicted runtime: %s", exc)

                    _task.add_done_callback(_log_close_error)
                except RuntimeError:
                    # No running loop - skip async close; the evicted
                    # runtime's backend will be GC'd.
                    pass
            client = SandshoreRuntime(
                anthropic_api_key=anthropic_api_key,
                e2b_api_key=e2b_api_key,
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


async def cleanup_pool() -> None:
    """Gracefully close and remove all runtime instances from the pool."""
    with _pool_lock:
        runtimes = list(_client_pool.values())
        _client_pool.clear()
    for rt in runtimes:
        try:
            await rt.close()
        except Exception as e:
            logger.warning("Error closing runtime during pool cleanup: %s", e)


def pool_stats() -> dict:
    """Return observability info about the singleton pool."""
    with _pool_lock:
        entries = []
        for key, rt in _client_pool.items():
            entries.append({
                "backend": rt.backend_name,
                "template": rt.template,
                "circuit_breaker_state": rt.circuit_breaker.state,
                "metrics": rt.metrics.snapshot(),
            })
        return {
            "pool_size": len(_client_pool),
            "runtimes": entries,
        }
