"""Tests for Sandshore optimization features.

Covers circuit breaker, runtime metrics, health cache with lock,
pool key fix, pool lifecycle, context manager, backend input validation,
backend health caching, backend client pooling, and configurable limits.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.backends import (
    CloudflareBackend,
    DockerBackend,
    E2BBackend,
    SSEEvent,
    _validate_runner_file,
    create_backend,
)
from sandcastle.engine.sandshore import (
    CircuitBreaker,
    RuntimeMetrics,
    SandshoreRuntime,
    _client_pool,
    _pool_lock,
    cleanup_pool,
    get_sandshore_runtime,
    pool_stats,
)


# ---------------------------------------------------------------------------
# TestCircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for the CircuitBreaker class."""

    def test_initial_state_is_closed(self) -> None:
        """New circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb._failure_count == 0

    async def test_stays_closed_below_threshold(self) -> None:
        """4 failures (below default threshold of 5) keeps state CLOSED."""
        cb = CircuitBreaker()
        for _ in range(4):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb._failure_count == 4

    async def test_opens_after_threshold(self) -> None:
        """5 failures (meeting default threshold) trips state to OPEN."""
        cb = CircuitBreaker()
        for _ in range(5):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb._failure_count == 5

    async def test_rejects_when_open(self) -> None:
        """allow_request returns False when circuit is OPEN."""
        cb = CircuitBreaker()
        for _ in range(5):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert await cb.allow_request() is False

    async def test_half_open_after_recovery_timeout(self) -> None:
        """State transitions to HALF_OPEN after recovery timeout elapses."""
        cb = CircuitBreaker(recovery_timeout=0.05)
        for _ in range(5):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        # Wait for recovery timeout to pass
        await asyncio.sleep(0.06)
        assert cb.state == CircuitBreaker.HALF_OPEN
        # HALF_OPEN allows one probe request through
        assert await cb.allow_request() is True

    async def test_resets_on_success(self) -> None:
        """record_success resets to CLOSED and clears failure count."""
        cb = CircuitBreaker()
        for _ in range(5):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        await cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb._failure_count == 0
        assert await cb.allow_request() is True

    async def test_custom_threshold_and_timeout(self) -> None:
        """Custom failure_threshold and recovery_timeout are respected."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.05)

        # 2 failures should not trip
        for _ in range(2):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED

        # 3rd failure trips it
        await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        # After custom timeout, should be HALF_OPEN
        await asyncio.sleep(0.06)
        assert cb.state == CircuitBreaker.HALF_OPEN


# ---------------------------------------------------------------------------
# TestRuntimeMetrics
# ---------------------------------------------------------------------------


class TestRuntimeMetrics:
    """Tests for the RuntimeMetrics class."""

    def test_initial_values_zero(self) -> None:
        """Fresh metrics instance has all counters at zero."""
        m = RuntimeMetrics()
        assert m.total_queries == 0
        assert m.successful_queries == 0
        assert m.failed_queries == 0
        assert m.failover_attempts == 0
        assert m.circuit_breaker_rejections == 0
        assert m.total_cost_usd == 0.0

    async def test_record_query_success(self) -> None:
        """record_query_success increments total + successful and adds cost."""
        m = RuntimeMetrics()
        await m.record_query_success(cost_usd=0.005)
        assert m.total_queries == 1
        assert m.successful_queries == 1
        assert m.failed_queries == 0
        assert m.total_cost_usd == pytest.approx(0.005)

        await m.record_query_success(cost_usd=0.003)
        assert m.total_queries == 2
        assert m.successful_queries == 2
        assert m.total_cost_usd == pytest.approx(0.008)

    async def test_record_query_failure(self) -> None:
        """record_query_failure increments total + failed but not cost."""
        m = RuntimeMetrics()
        await m.record_query_failure()
        assert m.total_queries == 1
        assert m.failed_queries == 1
        assert m.successful_queries == 0
        assert m.total_cost_usd == 0.0

    async def test_record_failover(self) -> None:
        """record_failover increments failover_attempts counter."""
        m = RuntimeMetrics()
        await m.record_failover()
        await m.record_failover()
        assert m.failover_attempts == 2

    async def test_record_circuit_breaker_rejection(self) -> None:
        """record_circuit_breaker_rejection increments rejection counter."""
        m = RuntimeMetrics()
        await m.record_circuit_breaker_rejection()
        assert m.circuit_breaker_rejections == 1

    async def test_snapshot_returns_dict(self) -> None:
        """snapshot() returns a dict with all expected keys."""
        m = RuntimeMetrics()
        await m.record_query_success(cost_usd=0.01)
        await m.record_query_failure()
        await m.record_failover()
        await m.record_circuit_breaker_rejection()

        snap = m.snapshot()
        assert isinstance(snap, dict)
        assert snap["total_queries"] == 2
        assert snap["successful_queries"] == 1
        assert snap["failed_queries"] == 1
        assert snap["failover_attempts"] == 1
        assert snap["circuit_breaker_rejections"] == 1
        assert snap["total_cost_usd"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# TestHealthCacheLock
# ---------------------------------------------------------------------------


class TestHealthCacheLock:
    """Tests for the SandshoreRuntime._cached_health() with lock."""

    async def test_cached_health_returns_cached_value(self) -> None:
        """Second call within TTL returns cached value without calling backend."""
        mock_backend = AsyncMock()
        mock_backend.health = AsyncMock(return_value=True)
        mock_backend.name = "mock"
        mock_backend.close = AsyncMock()

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = mock_backend
        runtime._health_cache = (False, 0.0)
        runtime._health_cache_ttl = 60.0
        runtime._health_lock = asyncio.Lock()

        # First call: cache miss -> calls backend
        result1 = await runtime._cached_health()
        assert result1 is True
        assert mock_backend.health.call_count == 1

        # Second call: cache hit -> uses cached value
        result2 = await runtime._cached_health()
        assert result2 is True
        assert mock_backend.health.call_count == 1  # still 1

    async def test_cached_health_refreshes_after_ttl(self) -> None:
        """After TTL expires, backend.health() is called again."""
        mock_backend = AsyncMock()
        mock_backend.health = AsyncMock(return_value=True)
        mock_backend.name = "mock"
        mock_backend.close = AsyncMock()

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = mock_backend
        runtime._health_cache = (False, 0.0)
        runtime._health_cache_ttl = 0.05  # very short TTL
        runtime._health_lock = asyncio.Lock()

        # First call -> backend check
        await runtime._cached_health()
        assert mock_backend.health.call_count == 1

        # Wait for TTL expiry
        await asyncio.sleep(0.06)

        # Second call -> cache expired, backend check again
        await runtime._cached_health()
        assert mock_backend.health.call_count == 2


# ---------------------------------------------------------------------------
# TestPoolFix
# ---------------------------------------------------------------------------


class TestPoolFix:
    """Tests for singleton pool key, thread safety, cleanup, and stats."""

    def setup_method(self) -> None:
        """Clear the pool before each test to avoid cross-test pollution."""
        with _pool_lock:
            _client_pool.clear()

    def teardown_method(self) -> None:
        """Clear the pool after each test."""
        with _pool_lock:
            _client_pool.clear()

    def test_pool_key_includes_backend_params(self) -> None:
        """Different docker_image values produce separate pool entries."""
        rt1 = get_sandshore_runtime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            sandbox_backend="docker",
            docker_image="image-a:latest",
        )
        rt2 = get_sandshore_runtime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            sandbox_backend="docker",
            docker_image="image-b:latest",
        )
        assert rt1 is not rt2
        with _pool_lock:
            assert len(_client_pool) == 2

        # Same params return the same instance
        rt3 = get_sandshore_runtime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            sandbox_backend="docker",
            docker_image="image-a:latest",
        )
        assert rt3 is rt1

    def test_pool_thread_safety(self) -> None:
        """Concurrent get_sandshore_runtime calls produce exactly one instance per key."""

        def create_runtime(_i: int) -> SandshoreRuntime:
            return get_sandshore_runtime(
                anthropic_api_key="ak",
                e2b_api_key="ek",
                sandbox_backend="local",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(create_runtime, range(20)))

        # All should be the same instance
        assert all(r is results[0] for r in results)
        with _pool_lock:
            assert len(_client_pool) == 1

    async def test_cleanup_pool(self) -> None:
        """cleanup_pool closes all runtimes and empties the pool."""
        rt1 = get_sandshore_runtime(
            anthropic_api_key="ak1",
            e2b_api_key="ek1",
            sandbox_backend="local",
        )
        rt2 = get_sandshore_runtime(
            anthropic_api_key="ak2",
            e2b_api_key="ek2",
            sandbox_backend="local",
        )

        with _pool_lock:
            assert len(_client_pool) == 2

        # Patch close to track calls
        rt1._backend.close = AsyncMock()
        rt2._backend.close = AsyncMock()

        await cleanup_pool()

        with _pool_lock:
            assert len(_client_pool) == 0

        rt1._backend.close.assert_awaited_once()
        rt2._backend.close.assert_awaited_once()

    def test_pool_stats(self) -> None:
        """pool_stats returns expected structure with pool_size and runtimes."""
        get_sandshore_runtime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            sandbox_backend="local",
        )

        stats = pool_stats()
        assert isinstance(stats, dict)
        assert "pool_size" in stats
        assert "runtimes" in stats
        assert stats["pool_size"] == 1
        assert len(stats["runtimes"]) == 1

        entry = stats["runtimes"][0]
        assert "backend" in entry
        assert "circuit_breaker_state" in entry
        assert "metrics" in entry
        assert entry["backend"] == "local"
        assert entry["circuit_breaker_state"] == "closed"


# ---------------------------------------------------------------------------
# TestContextManager
# ---------------------------------------------------------------------------


class TestContextManager:
    """Tests for the SandshoreRuntime async context manager."""

    async def test_async_with_calls_close(self) -> None:
        """Exiting async with block calls close() on the runtime."""
        mock_backend = AsyncMock()
        mock_backend.close = AsyncMock()
        mock_backend.name = "mock"

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = mock_backend

        async with runtime:
            pass

        mock_backend.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestBackendInputValidation
# ---------------------------------------------------------------------------


class TestBackendInputValidation:
    """Tests for _validate_runner_file path traversal prevention."""

    def test_rejects_path_traversal(self) -> None:
        """Runner file with '..' is rejected."""
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("../../../etc/passwd")

    def test_rejects_absolute_path(self) -> None:
        """Runner file starting with '/' is rejected."""
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("/etc/passwd")

    def test_accepts_valid_runner_file(self) -> None:
        """Normal runner filenames pass validation."""
        _validate_runner_file("runner.mjs")
        _validate_runner_file("runner-openai.mjs")

    def test_rejects_dot_dot_in_middle(self) -> None:
        """Path traversal embedded in path is rejected."""
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("subdir/../runner.mjs")


# ---------------------------------------------------------------------------
# TestBackendHealthCache
# ---------------------------------------------------------------------------


class TestBackendHealthCache:
    """Tests for health caching in Docker and Cloudflare backends."""

    async def test_docker_health_caching(self) -> None:
        """Docker backend caches health result; second call skips check."""
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.version = AsyncMock(
            return_value={"Version": "24.0"}
        )
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()

            # First call: actual check
            result1 = await backend.health()
            assert result1 is True
            assert mock_docker_instance.version.call_count == 1

            # Second call: cached
            result2 = await backend.health()
            assert result2 is True
            assert mock_docker_instance.version.call_count == 1

    async def test_cloudflare_health_caching(self) -> None:
        """Cloudflare backend caches health result; second call skips check."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        backend = CloudflareBackend(
            worker_url="https://sandbox.example.workers.dev"
        )
        backend._client = mock_client

        # First call: actual check
        result1 = await backend.health()
        assert result1 is True
        assert mock_client.get.call_count == 1

        # Second call: cached
        result2 = await backend.health()
        assert result2 is True
        assert mock_client.get.call_count == 1

    async def test_health_cache_expires(self) -> None:
        """After TTL, the backend re-checks health."""
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.version = AsyncMock(
            return_value={"Version": "24.0"}
        )
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()
            backend._health_cache_ttl = 0.05  # very short TTL

            await backend.health()
            assert mock_docker_instance.version.call_count == 1

            # Wait for TTL to expire
            await asyncio.sleep(0.06)

            await backend.health()
            assert mock_docker_instance.version.call_count == 2


# ---------------------------------------------------------------------------
# TestBackendClientPooling
# ---------------------------------------------------------------------------


class TestBackendClientPooling:
    """Tests for client reuse in Docker and Cloudflare backends."""

    async def test_docker_client_reused(self) -> None:
        """Docker health + start reuse the same aiodocker client."""
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.version = AsyncMock(
            return_value={"Version": "24.0"}
        )
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()

            # First call creates the client
            await backend.health()
            client1 = backend._client

            # _get_client returns the same instance
            client2 = backend._get_client()
            assert client1 is client2

            # Docker constructor called only once
            assert mock_docker_cls.call_count == 1

    async def test_cloudflare_client_reused(self) -> None:
        """Cloudflare health + _get_client reuse the same httpx client."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_client.get = AsyncMock(return_value=mock_resp)

        backend = CloudflareBackend(
            worker_url="https://sandbox.example.workers.dev"
        )

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            # First call creates the client
            client1 = backend._get_client()

            # Second call returns cached
            client2 = backend._get_client()
            assert client1 is client2
            assert mock_cls.call_count == 1

    async def test_docker_close_cleans_client(self) -> None:
        """Docker close() closes and clears the pooled client."""
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.version = AsyncMock(
            return_value={"Version": "24.0"}
        )
        mock_docker_instance.close = AsyncMock()
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()

            # Create the client
            backend._get_client()
            assert backend._client is not None

            # Close should clear it
            await backend.close()
            assert backend._client is None
            mock_docker_instance.close.assert_awaited_once()

    async def test_cloudflare_close_cleans_client(self) -> None:
        """Cloudflare close() closes and clears the pooled httpx client."""
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()

        backend = CloudflareBackend(
            worker_url="https://sandbox.example.workers.dev"
        )
        backend._client = mock_client

        await backend.close()
        assert backend._client is None
        mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestConfigurableLimits
# ---------------------------------------------------------------------------


class TestConfigurableLimits:
    """Tests for configurable backend parameters."""

    def test_e2b_custom_queue_size(self) -> None:
        """E2B backend accepts custom event_queue_size."""
        backend = E2BBackend(
            e2b_api_key="key",
            event_queue_size=500,
        )
        assert backend._event_queue_size == 500

    def test_e2b_default_queue_size(self) -> None:
        """E2B backend defaults to 1000 event queue size."""
        backend = E2BBackend(e2b_api_key="key")
        assert backend._event_queue_size == 1000

    def test_docker_custom_memory_limit(self) -> None:
        """Docker backend accepts custom memory_limit."""
        custom_limit = 256 * 1024 * 1024  # 256 MB
        backend = DockerBackend(memory_limit=custom_limit)
        assert backend._memory_limit == custom_limit

    def test_docker_default_memory_limit(self) -> None:
        """Docker backend defaults to 512 MB memory limit."""
        backend = DockerBackend()
        assert backend._memory_limit == 512 * 1024 * 1024

    def test_e2b_custom_npm_timeouts(self) -> None:
        """E2B backend accepts custom npm install timeouts."""
        backend = E2BBackend(
            e2b_api_key="key",
            npm_install_timeout=120,
            npm_poll_deadline=180.0,
        )
        assert backend._npm_install_timeout == 120
        assert backend._npm_poll_deadline == 180.0

    def test_e2b_custom_execution_grace(self) -> None:
        """E2B backend accepts custom execution grace period."""
        backend = E2BBackend(
            e2b_api_key="key",
            execution_grace_period=60.0,
        )
        assert backend._execution_grace_period == 60.0

    def test_create_backend_passes_e2b_limits(self) -> None:
        """create_backend forwards E2B-specific configurable limits."""
        backend = create_backend(
            "e2b",
            e2b_api_key="key",
            e2b_event_queue_size=2000,
            e2b_npm_install_timeout=45,
            e2b_npm_poll_deadline=120.0,
            e2b_execution_grace_period=45.0,
        )
        assert isinstance(backend, E2BBackend)
        assert backend._event_queue_size == 2000
        assert backend._npm_install_timeout == 45
        assert backend._npm_poll_deadline == 120.0
        assert backend._execution_grace_period == 45.0

    def test_create_backend_passes_docker_memory_limit(self) -> None:
        """create_backend forwards docker_memory_limit to DockerBackend."""
        custom_limit = 1024 * 1024 * 1024  # 1 GB
        backend = create_backend(
            "docker",
            docker_memory_limit=custom_limit,
        )
        assert isinstance(backend, DockerBackend)
        assert backend._memory_limit == custom_limit
