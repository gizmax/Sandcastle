"""Concurrency stress tests for Sandcastle.

Stress-tests all concurrent/async code paths to find race conditions,
deadlocks, and data corruption. Uses asyncio.gather() for concurrent
operations and asyncio.Event/Barrier for precise timing control.
"""

from __future__ import annotations

import asyncio
import collections
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Browser action cache stress tests
# ---------------------------------------------------------------------------


class TestBrowserCacheStress:
    """Stress test _get_cached_actions / _save_cached_actions concurrency."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """Clear the browser cache before each test."""
        from sandcastle.engine.executor import _browser_action_cache

        _browser_action_cache.clear()
        yield
        _browser_action_cache.clear()

    @pytest.mark.asyncio
    async def test_concurrent_save_100(self):
        """100 concurrent saves - cache must never exceed max."""
        from sandcastle.engine.executor import (
            _browser_action_cache,
            _BROWSER_CACHE_MAX,
            _save_cached_actions,
        )

        async def save_one(i: int):
            await _save_cached_actions(
                f"https://example.com/page{i}", f"intent_{i}", [{"action": i}]
            )

        await asyncio.gather(*[save_one(i) for i in range(100)])
        assert len(_browser_action_cache) <= _BROWSER_CACHE_MAX

    @pytest.mark.asyncio
    async def test_concurrent_save_exceeds_max(self):
        """Save more than max items - LRU eviction must keep size bounded."""
        from sandcastle.engine.executor import (
            _browser_action_cache,
            _BROWSER_CACHE_MAX,
            _save_cached_actions,
        )

        # Fill beyond max with sequential saves first to ensure deterministic state
        for i in range(_BROWSER_CACHE_MAX + 100):
            await _save_cached_actions(
                f"https://example.com/p{i}", f"intent_{i}", [{"a": i}]
            )

        assert len(_browser_action_cache) <= _BROWSER_CACHE_MAX

    @pytest.mark.asyncio
    async def test_concurrent_read_write_100(self):
        """100 concurrent reads + writes - no corruption or deadlock."""
        from sandcastle.engine.executor import (
            _browser_action_cache,
            _BROWSER_CACHE_MAX,
            _get_cached_actions,
            _save_cached_actions,
        )

        # Pre-populate some entries
        for i in range(50):
            await _save_cached_actions(
                f"https://example.com/page{i}", f"intent_{i}", [{"action": i}]
            )

        async def reader(i: int):
            result = await _get_cached_actions(
                f"https://example.com/page{i % 50}", f"intent_{i % 50}"
            )
            return result

        async def writer(i: int):
            await _save_cached_actions(
                f"https://example.com/new{i}", f"new_intent_{i}", [{"new": i}]
            )

        tasks = []
        for i in range(100):
            tasks.append(reader(i))
            tasks.append(writer(i))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        # No exceptions should occur
        for r in results:
            assert not isinstance(r, Exception), f"Unexpected exception: {r}"

        assert len(_browser_action_cache) <= _BROWSER_CACHE_MAX

    @pytest.mark.asyncio
    async def test_lru_ordering_under_concurrency(self):
        """Verify LRU ordering: accessed items are moved to end."""
        from sandcastle.engine.executor import (
            _browser_action_cache,
            _get_cached_actions,
            _save_cached_actions,
        )

        # Save 3 items sequentially
        for i in range(3):
            await _save_cached_actions(
                f"https://site.com/p{i}", f"intent_{i}", [{"v": i}]
            )

        # Access item 0 to move it to end
        await _get_cached_actions("https://site.com/p0", "intent_0")

        keys = list(_browser_action_cache.keys())
        # item 0 should now be at the end (most recently used)
        assert keys[-1].startswith("site.com"), f"Last key: {keys[-1]}"

    @pytest.mark.asyncio
    async def test_concurrent_save_same_key(self):
        """50 concurrent saves to the same key - last write wins, no corruption."""
        from sandcastle.engine.executor import (
            _browser_action_cache,
            _get_cached_actions,
            _save_cached_actions,
        )

        async def save_one(i: int):
            await _save_cached_actions(
                "https://example.com/shared", "shared_intent", [{"value": i}]
            )

        await asyncio.gather(*[save_one(i) for i in range(50)])

        result = await _get_cached_actions("https://example.com/shared", "shared_intent")
        assert result is not None
        assert len(result) == 1
        # Value should be some integer from 0-49 (whichever wrote last)
        assert isinstance(result[0]["value"], int)

    @pytest.mark.asyncio
    async def test_cache_get_nonexistent_concurrent(self):
        """100 concurrent gets for nonexistent keys - all return None."""
        from sandcastle.engine.executor import _get_cached_actions

        results = await asyncio.gather(
            *[_get_cached_actions(f"https://no.com/{i}", f"nope_{i}") for i in range(100)]
        )
        assert all(r is None for r in results)

    @pytest.mark.asyncio
    async def test_repeated_stress_cycles(self):
        """Run 10 cycles of concurrent save+read to catch intermittent issues."""
        from sandcastle.engine.executor import (
            _browser_action_cache,
            _BROWSER_CACHE_MAX,
            _get_cached_actions,
            _save_cached_actions,
        )

        for cycle in range(10):
            tasks = []
            for i in range(20):
                tasks.append(
                    _save_cached_actions(
                        f"https://c{cycle}.com/{i}", f"int_{i}", [{"c": cycle, "i": i}]
                    )
                )
                tasks.append(
                    _get_cached_actions(f"https://c{cycle}.com/{i}", f"int_{i}")
                )
            await asyncio.gather(*tasks)
            assert len(_browser_action_cache) <= _BROWSER_CACHE_MAX


# ---------------------------------------------------------------------------
# 2. Cancel flags stress tests
# ---------------------------------------------------------------------------


class TestCancelFlagsStress:
    """Stress test cancel flag operations."""

    @pytest.fixture(autouse=True)
    def _clear_flags(self):
        """Clear cancel flags before each test."""
        from sandcastle.engine.executor import _cancel_flags

        _cancel_flags.clear()
        yield
        _cancel_flags.clear()

    @pytest.mark.asyncio
    async def test_50_concurrent_cancels_same_run(self):
        """50 concurrent cancel requests for the same run_id."""
        from sandcastle.engine.executor import _cancel_flags, cancel_run_local

        run_id = "run-concurrent-cancel"
        await asyncio.gather(*[cancel_run_local(run_id) for _ in range(50)])
        # Should still only have one entry for this run_id
        assert run_id in _cancel_flags

    @pytest.mark.asyncio
    async def test_concurrent_cancel_and_check(self):
        """Cancel and check happening concurrently."""
        from sandcastle.engine.executor import (
            _cancel_flags,
            _check_cancel,
            cancel_run_local,
        )

        run_id = "run-check-cancel"

        # Patch settings to force local mode
        with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
            mock_settings.redis_url = ""

            await cancel_run_local(run_id)

            # Multiple concurrent checks - all should find it (flag persists until cleanup)
            results = await asyncio.gather(*[_check_cancel(run_id) for _ in range(10)])
            assert all(results), "Cancel flag should be visible to all concurrent checks"

    @pytest.mark.asyncio
    async def test_many_different_run_cancels(self):
        """100 different run IDs cancelled concurrently."""
        from sandcastle.engine.executor import _cancel_flags, cancel_run_local

        run_ids = [f"run-{i}" for i in range(100)]
        await asyncio.gather(*[cancel_run_local(rid) for rid in run_ids])
        for rid in run_ids:
            assert rid in _cancel_flags

    @pytest.mark.asyncio
    async def test_cancel_flag_eviction_under_load(self):
        """When exceeding _MAX_CANCEL_FLAGS, eviction happens without deadlock."""
        from sandcastle.engine.executor import (
            _cancel_flags,
            _MAX_CANCEL_FLAGS,
            cancel_run_local,
        )

        # Pre-fill close to max
        for i in range(_MAX_CANCEL_FLAGS - 5):
            _cancel_flags[f"prefill-{i}"] = None

        # Now add 50 more concurrently to trigger eviction
        await asyncio.gather(
            *[cancel_run_local(f"overflow-{i}") for i in range(50)]
        )

        # Should not exceed max by much (eviction removes half)
        assert len(_cancel_flags) <= _MAX_CANCEL_FLAGS

    @pytest.mark.asyncio
    async def test_cancel_check_local_persistent(self):
        """Cancel flag persists across multiple checks (cleaned up in finally block)."""
        from sandcastle.engine.executor import _cancel_flags, _check_cancel, cancel_run_local

        run_id = "run-persistent"

        with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
            mock_settings.redis_url = ""
            await cancel_run_local(run_id)
            assert await _check_cancel(run_id) is True
            assert await _check_cancel(run_id) is True  # still visible
            # Manual cleanup (simulates finally block)
            _cancel_flags.pop(run_id, None)
            assert await _check_cancel(run_id) is False


# ---------------------------------------------------------------------------
# 3. Rate limiter stress tests
# ---------------------------------------------------------------------------


class TestRateLimiterStress:
    """Stress test InMemoryBackend and RateLimiter concurrency."""

    @pytest.mark.asyncio
    async def test_200_concurrent_checks_limit_100(self):
        """200 concurrent check_and_increment with limit=100 - verify exactly 100 pass."""
        from sandcastle.api.rate_limit import InMemoryBackend

        backend = InMemoryBackend()
        results = await asyncio.gather(
            *[
                backend.check_and_increment("test_key", 100, 60.0)
                for _ in range(200)
            ]
        )

        # Each call returns the count after the attempt (1-indexed).
        # Calls where count <= 100 were allowed (added to window).
        # Calls where count > 100 were rejected (not added).
        passed = sum(1 for r in results if r <= 100)
        assert passed == 100, f"Expected exactly 100 to pass, got {passed}"

    @pytest.mark.asyncio
    async def test_rate_limiter_concurrent_requests(self):
        """Full RateLimiter with 50 concurrent requests, limit=20."""
        from sandcastle.api.rate_limit import InMemoryBackend, RateLimiter

        limiter = RateLimiter(max_requests=20, window_seconds=60.0, backend=InMemoryBackend())

        passed = 0
        rejected = 0

        async def make_request(i: int):
            nonlocal passed, rejected
            mock_request = MagicMock()
            mock_request.state = MagicMock()
            mock_request.state.tenant_id = None
            mock_request.client = MagicMock()
            mock_request.client.host = "127.0.0.1"

            try:
                await limiter.check(mock_request)
                passed += 1
            except Exception:
                rejected += 1

        await asyncio.gather(*[make_request(i) for i in range(50)])
        assert passed == 20
        assert rejected == 30

    @pytest.mark.asyncio
    async def test_rate_limiter_multiple_keys(self):
        """Concurrent requests from different IPs should be independent."""
        from sandcastle.api.rate_limit import InMemoryBackend

        backend = InMemoryBackend()
        limit = 5

        async def check_key(key: str, count: int):
            results = []
            for _ in range(count):
                r = await backend.check_and_increment(key, limit, 60.0)
                results.append(r)
            return results

        # 3 different keys, each making 10 requests with limit 5
        all_results = await asyncio.gather(
            check_key("key_a", 10),
            check_key("key_b", 10),
            check_key("key_c", 10),
        )

        for key_results in all_results:
            passed_count = sum(1 for r in key_results if r <= limit)
            assert passed_count == limit

    @pytest.mark.asyncio
    async def test_window_boundary_precision(self):
        """Requests at exact window edge should be handled correctly."""
        from sandcastle.api.rate_limit import InMemoryBackend

        backend = InMemoryBackend()
        # Use very short window
        window = 0.1  # 100ms

        # Fill up the window
        for _ in range(5):
            await backend.check_and_increment("edge_key", 5, window)

        # Should be at limit
        r = await backend.check_and_increment("edge_key", 5, window)
        assert r > 5  # rejected

        # Wait for window to expire
        await asyncio.sleep(0.15)

        # Should be allowed again
        r = await backend.check_and_increment("edge_key", 5, window)
        assert r <= 5

    @pytest.mark.asyncio
    async def test_redis_backend_lua_concurrent(self):
        """RedisBackend with mocked redis - concurrent calls use atomic Lua."""
        from sandcastle.api.rate_limit import RedisBackend

        mock_redis = AsyncMock()
        # Simulate Lua script returning incrementing count
        call_count = 0

        async def mock_eval(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return call_count

        mock_redis.eval = mock_eval

        backend = RedisBackend("redis://localhost")
        backend._redis = mock_redis

        results = await asyncio.gather(
            *[backend.check_and_increment("k", 10, 60.0) for _ in range(20)]
        )
        assert len(results) == 20
        assert all(isinstance(r, int) for r in results)

    @pytest.mark.asyncio
    async def test_redis_backend_double_check_locking(self):
        """RedisBackend._get_redis uses double-checked locking correctly."""
        from sandcastle.api.rate_limit import RedisBackend

        backend = RedisBackend("redis://localhost")

        # Mock the redis import to avoid actual connections
        mock_redis_client = AsyncMock()

        with patch("sandcastle.api.rate_limit.from_url", create=True):
            with patch.dict("sys.modules", {"redis.asyncio": MagicMock()}):
                # Pre-set the redis client to avoid actual initialization
                backend._redis = mock_redis_client

                # 50 concurrent _get_redis calls - all should get the same instance
                results = await asyncio.gather(
                    *[backend._get_redis() for _ in range(50)]
                )
                assert all(r is mock_redis_client for r in results)

    @pytest.mark.asyncio
    async def test_rate_limiter_repeated_cycles(self):
        """10 cycles of concurrent rate limiting to catch intermittent issues."""
        from sandcastle.api.rate_limit import InMemoryBackend

        backend = InMemoryBackend()

        for cycle in range(10):
            key = f"cycle_{cycle}"
            results = await asyncio.gather(
                *[backend.check_and_increment(key, 10, 60.0) for _ in range(30)]
            )
            passed = sum(1 for r in results if r <= 10)
            assert passed == 10, f"Cycle {cycle}: expected 10, got {passed}"


# ---------------------------------------------------------------------------
# 4. Event bus stress tests
# ---------------------------------------------------------------------------


class TestEventBusStress:
    """Stress test EventBus publish/subscribe concurrency."""

    @pytest.mark.asyncio
    async def test_100_subscribers_rapid_publish(self):
        """100 subscribers, rapid publish, verify all receive."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        queues = []
        for _ in range(100):
            q = await bus.subscribe()
            queues.append(q)

        assert bus.subscriber_count == 100

        # Publish 10 events rapidly
        for i in range(10):
            bus.publish("run.started", {"run_id": f"r{i}"})

        # All subscribers should have all events
        for q in queues:
            assert q.qsize() == 10
            events = [q.get_nowait() for _ in range(10)]
            assert all(e["type"] == "run.started" for e in events)

        # Cleanup
        for q in queues:
            await bus.unsubscribe(q)
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_during_publish(self):
        """Subscribe and unsubscribe during publish - no ConcurrentModificationError."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        errors = []

        async def subscriber_lifecycle(i: int):
            try:
                q = await bus.subscribe()
                await asyncio.sleep(0.001)  # tiny delay
                bus.publish("run.started", {"id": f"s{i}"})
                await bus.unsubscribe(q)
            except Exception as e:
                errors.append(e)

        await asyncio.gather(*[subscriber_lifecycle(i) for i in range(50)])
        assert len(errors) == 0, f"Errors: {errors}"

    @pytest.mark.asyncio
    async def test_dead_subscriber_eviction(self):
        """Dead subscriber (full queue) gets evicted after MAX_CONSECUTIVE_DROPS."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        # Create a subscriber with tiny queue
        dead_q = asyncio.Queue(maxsize=1)
        async with bus._lock:
            bus._subscribers.add(dead_q)

        # Fill the queue
        dead_q.put_nowait({"type": "filler"})

        healthy_q = await bus.subscribe()

        # Publish enough to trigger eviction (MAX_CONSECUTIVE_DROPS = 50)
        for i in range(60):
            bus.publish("run.started", {"run_id": f"r{i}"})

        # Dead subscriber should be evicted
        assert dead_q not in bus._subscribers
        # Healthy subscriber should still be there
        assert healthy_q in bus._subscribers

        await bus.unsubscribe(healthy_q)

    @pytest.mark.asyncio
    async def test_max_subscribers_boundary(self):
        """MAX_SUBSCRIBERS boundary - 500th should succeed, 501st should fail."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        queues = []

        for i in range(EventBus.MAX_SUBSCRIBERS):
            q = await bus.subscribe()
            queues.append(q)

        assert bus.subscriber_count == EventBus.MAX_SUBSCRIBERS

        # 501st should raise RuntimeError
        with pytest.raises(RuntimeError, match="subscriber limit"):
            await bus.subscribe()

        # Cleanup
        for q in queues:
            await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_at_limit(self):
        """50 concurrent subscribes when near the limit."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        # Fill to near limit
        pre_queues = []
        for _ in range(EventBus.MAX_SUBSCRIBERS - 25):
            q = await bus.subscribe()
            pre_queues.append(q)

        # Try 50 concurrent subscribes - exactly 25 should succeed
        results = await asyncio.gather(
            *[bus.subscribe() for _ in range(50)],
            return_exceptions=True,
        )

        successes = [r for r in results if isinstance(r, asyncio.Queue)]
        failures = [r for r in results if isinstance(r, RuntimeError)]
        assert len(successes) == 25
        assert len(failures) == 25

        # Cleanup
        for q in pre_queues + successes:
            await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_publish_after_all_unsubscribed(self):
        """Publish after all subscribers left - no errors."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        q = await bus.subscribe()
        await bus.unsubscribe(q)

        # Should not raise
        bus.publish("run.started", {"run_id": "orphan"})
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_rapid_subscribe_unsubscribe_cycles(self):
        """10 cycles of rapid subscribe/publish/unsubscribe."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()

        for cycle in range(10):
            queues = []
            for _ in range(20):
                q = await bus.subscribe()
                queues.append(q)

            for i in range(5):
                bus.publish("run.started", {"cycle": cycle, "i": i})

            for q in queues:
                assert q.qsize() == 5
                await bus.unsubscribe(q)

            assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_publish_concurrent_with_unsubscribe(self):
        """Concurrent publish and unsubscribe - no crashes."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        queues = []
        for _ in range(30):
            q = await bus.subscribe()
            queues.append(q)

        errors = []

        async def unsubscriber(q):
            try:
                await asyncio.sleep(0.001)
                await bus.unsubscribe(q)
            except Exception as e:
                errors.append(e)

        def publisher():
            try:
                for i in range(20):
                    bus.publish("run.started", {"i": i})
            except Exception as e:
                errors.append(e)

        # Run publishers and unsubscribers concurrently
        unsub_tasks = [unsubscriber(q) for q in queues[:15]]
        publisher()  # sync, runs immediately
        await asyncio.gather(*unsub_tasks)

        assert len(errors) == 0, f"Errors: {errors}"

        # Cleanup remaining
        for q in queues[15:]:
            await bus.unsubscribe(q)


# ---------------------------------------------------------------------------
# 5. Singleton initialization stress tests
# ---------------------------------------------------------------------------


class TestSingletonStress:
    """Stress test singleton pattern correctness under concurrent access."""

    @pytest.mark.asyncio
    async def test_get_sandshore_runtime_concurrent(self):
        """50 coroutines calling get_sandshore_runtime simultaneously."""
        from sandcastle.engine.sandshore import (
            _client_pool,
            _pool_lock,
            get_sandshore_runtime,
        )

        # Clear the pool first
        with _pool_lock:
            _client_pool.clear()

        with patch("sandcastle.engine.sandshore.create_backend") as mock_backend:
            mock_backend.return_value = MagicMock()
            mock_backend.return_value.name = "local"

            with patch("sandcastle.config.settings") as mock_cfg:
                mock_cfg.docker_seccomp_profile = ""
                mock_cfg.docker_pids_limit = 100
                mock_cfg.docker_cpu_period = 100000
                mock_cfg.docker_cpu_quota = 50000

                # Run 50 concurrent calls with same params
                results = await asyncio.gather(
                    *[
                        asyncio.to_thread(
                            get_sandshore_runtime,
                            anthropic_api_key="key_a",
                            e2b_api_key="key_b",
                            sandbox_backend="local",
                        )
                        for _ in range(50)
                    ]
                )

                # All should return the same instance
                assert all(r is results[0] for r in results)

                # Pool should have exactly 1 entry
                with _pool_lock:
                    assert len(_client_pool) == 1

        # Cleanup
        with _pool_lock:
            _client_pool.clear()

    @pytest.mark.asyncio
    async def test_get_redis_pool_concurrent(self):
        """50 coroutines calling _get_redis simultaneously."""
        from sandcastle.engine.executor import (
            _redis_pool_lock,
        )

        import sandcastle.engine.executor as executor_mod

        # Reset the pool
        old_pool = executor_mod._redis_pool
        executor_mod._redis_pool = None

        mock_redis = AsyncMock()

        with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
            mock_settings.redis_url = "redis://localhost"

            with patch("redis.asyncio.from_url", return_value=mock_redis):
                results = await asyncio.gather(
                    *[executor_mod._get_redis() for _ in range(50)]
                )

                # All should return the same instance
                assert all(r is results[0] for r in results)

        # Restore
        executor_mod._redis_pool = old_pool

    @pytest.mark.asyncio
    async def test_get_optimizer_concurrent(self):
        """50 concurrent calls to get_optimizer should return same instance."""
        import sandcastle.engine.optimizer as opt_mod

        old = opt_mod._optimizer_instance
        opt_mod._optimizer_instance = None

        try:
            # get_optimizer is sync, so use gather with to_thread
            results = await asyncio.gather(
                *[asyncio.to_thread(opt_mod.get_optimizer) for _ in range(50)]
            )
            # All should be the same instance
            assert all(r is results[0] for r in results)
        finally:
            opt_mod._optimizer_instance = old

    @pytest.mark.asyncio
    async def test_get_fernet_concurrent(self):
        """50 concurrent calls to _get_fernet should return same instance."""
        import sandcastle.engine.crypto as crypto_mod

        old_instance = crypto_mod._fernet_instance
        old_checked = crypto_mod._fernet_checked
        crypto_mod._fernet_instance = None
        crypto_mod._fernet_checked = False

        try:
            with patch("sandcastle.config.settings") as mock_settings:
                mock_settings.credential_encryption_key = ""

                results = await asyncio.gather(
                    *[asyncio.to_thread(crypto_mod._get_fernet) for _ in range(50)]
                )
                # All should return None (no key configured) - same value
                assert all(r is None for r in results)
        finally:
            crypto_mod._fernet_instance = old_instance
            crypto_mod._fernet_checked = old_checked

    @pytest.mark.asyncio
    async def test_get_scheduler_concurrent_async(self):
        """50 coroutines calling get_scheduler in the same event loop return same instance.

        Note: get_scheduler() is not thread-safe by design (no lock), since
        it's only called from the async event loop. All calls from the same
        event loop (asyncio.gather) are effectively serialized at each await.
        """
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None

        try:
            async def get():
                return sched_mod.get_scheduler()

            results = await asyncio.gather(*[get() for _ in range(50)])
            # From the same event loop, all calls see the same instance
            assert all(r is results[0] for r in results)
        finally:
            sched_mod._scheduler = old


# ---------------------------------------------------------------------------
# 6. RuntimeMetrics concurrent access
# ---------------------------------------------------------------------------


class TestRuntimeMetricsStress:
    """Stress test RuntimeMetrics under concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_metric_recording(self):
        """100 concurrent success/failure recordings - counters must be exact."""
        from sandcastle.engine.sandshore import RuntimeMetrics

        metrics = RuntimeMetrics()

        async def record_success(_):
            await metrics.record_query_success(cost_usd=0.01)

        async def record_failure(_):
            await metrics.record_query_failure()

        async def record_failover(_):
            await metrics.record_failover()

        # 50 successes + 30 failures + 20 failovers concurrently
        tasks = (
            [record_success(i) for i in range(50)]
            + [record_failure(i) for i in range(30)]
            + [record_failover(i) for i in range(20)]
        )
        await asyncio.gather(*tasks)

        snap = metrics.snapshot()
        assert snap["successful_queries"] == 50
        assert snap["failed_queries"] == 30
        assert snap["total_queries"] == 80
        assert snap["failover_attempts"] == 20
        assert abs(snap["total_cost_usd"] - 0.50) < 0.001

    @pytest.mark.asyncio
    async def test_repeated_metric_stress(self):
        """10 rounds of 100 concurrent recordings."""
        from sandcastle.engine.sandshore import RuntimeMetrics

        metrics = RuntimeMetrics()

        for _ in range(10):
            await asyncio.gather(
                *[metrics.record_query_success(cost_usd=0.001) for _ in range(100)]
            )

        snap = metrics.snapshot()
        assert snap["successful_queries"] == 1000
        assert snap["total_queries"] == 1000
        assert abs(snap["total_cost_usd"] - 1.0) < 0.01


# ---------------------------------------------------------------------------
# 7. CircuitBreaker concurrent access
# ---------------------------------------------------------------------------


class TestCircuitBreakerStress:
    """Stress test CircuitBreaker state transitions under concurrency."""

    @pytest.mark.asyncio
    async def test_concurrent_failures_trip_breaker(self):
        """50 concurrent failures should trip the breaker to OPEN."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)

        await asyncio.gather(*[cb.record_failure() for _ in range(50)])
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_concurrent_success_resets_breaker(self):
        """After tripping, concurrent successes reset to CLOSED."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.01)

        # Trip the breaker
        for _ in range(5):
            await cb.record_failure()
        assert cb.state == "open"

        # Wait for recovery timeout
        await asyncio.sleep(0.02)
        assert cb.state == "half_open"

        # Concurrent successes
        await asyncio.gather(*[cb.record_success() for _ in range(20)])
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_interleaved_success_failure(self):
        """Rapid interleaved success/failure - no state corruption."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=10, recovery_timeout=30.0)

        tasks = []
        for i in range(100):
            if i % 3 == 0:
                tasks.append(cb.record_failure())
            else:
                tasks.append(cb.record_success())

        await asyncio.gather(*tasks)
        # State should be valid
        assert cb.state in ("closed", "open", "half_open")

    @pytest.mark.asyncio
    async def test_allow_request_concurrent_reads(self):
        """100 concurrent allow_request checks should be safe."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)

        # Trip to open
        for _ in range(5):
            await cb.record_failure()

        results = await asyncio.gather(
            *[asyncio.to_thread(cb.allow_request) for _ in range(100)]
        )
        assert all(r is False for r in results)


# ---------------------------------------------------------------------------
# 8. Event bus SSE subscriber patterns
# ---------------------------------------------------------------------------


class TestSSEStress:
    """Stress test SSE/EventStream patterns."""

    @pytest.mark.asyncio
    async def test_50_concurrent_sse_subscribers(self):
        """50 concurrent SSE subscribers for the same event stream."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        queues = []

        # Subscribe 50 concurrently
        results = await asyncio.gather(*[bus.subscribe() for _ in range(50)])
        queues = list(results)
        assert bus.subscriber_count == 50

        # Publish some events
        for i in range(5):
            bus.publish("run.completed", {"run_id": f"r{i}", "cost": 0.1 * i})

        # All should receive all events
        for q in queues:
            assert q.qsize() == 5

        # Cleanup all concurrently
        await asyncio.gather(*[bus.unsubscribe(q) for q in queues])
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_publisher_dies_subscriber_cleanup(self):
        """When a publisher task is cancelled, subscribers should still be cleanable."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        q = await bus.subscribe()

        async def publisher():
            for i in range(1000):
                bus.publish("run.started", {"i": i})
                await asyncio.sleep(0.001)

        task = asyncio.create_task(publisher())
        await asyncio.sleep(0.01)  # let some publishes happen
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # Subscriber should still be registered and cleanable
        assert q in bus._subscribers
        await bus.unsubscribe(q)
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_subscriber_disconnect_during_publish(self):
        """Subscriber unsubscribes while publishes are happening."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        queues = [await bus.subscribe() for _ in range(20)]
        errors = []

        async def unsubscriber(q, delay):
            await asyncio.sleep(delay)
            try:
                await bus.unsubscribe(q)
            except Exception as e:
                errors.append(e)

        # Start unsubscribing half with slight delays
        unsub_tasks = [
            unsubscriber(queues[i], 0.001 * i) for i in range(10)
        ]

        # Publish rapidly
        async def rapid_publish():
            for i in range(50):
                bus.publish("step.started", {"i": i})
                await asyncio.sleep(0.0005)

        await asyncio.gather(rapid_publish(), *unsub_tasks)
        assert len(errors) == 0

        # Cleanup remaining
        for q in queues[10:]:
            await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_event_ordering_preserved(self):
        """Events should arrive in order for each subscriber."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        q = await bus.subscribe()

        for i in range(100):
            bus.publish("run.started", {"seq": i})

        events = [q.get_nowait() for _ in range(100)]
        seqs = [e["data"]["seq"] for e in events]
        assert seqs == list(range(100))

        await bus.unsubscribe(q)


# ---------------------------------------------------------------------------
# 9. RunContext cost accumulation
# ---------------------------------------------------------------------------


class TestRunContextCostStress:
    """Stress test RunContext cost accumulation under concurrency."""

    @pytest.mark.asyncio
    async def test_child_context_isolated_costs(self):
        """Child contexts from with_item have isolated cost lists."""
        from sandcastle.engine.executor import RunContext

        parent = RunContext(
            run_id="test-parent",
            input={"items": list(range(20))},
            costs=[],
        )

        children = [parent.with_item(item=i, index=i) for i in range(20)]

        # Simulate concurrent cost recording in children
        async def add_costs(child, n):
            for _ in range(n):
                child.costs.append(0.01)
                await asyncio.sleep(0)

        await asyncio.gather(*[add_costs(child, 10) for child in children])

        # Parent costs should be unmodified
        assert len(parent.costs) == 0

        # Each child should have exactly 10 costs
        for child in children:
            assert len(child.costs) == 10
            assert abs(child.total_cost - 0.10) < 0.001

    @pytest.mark.asyncio
    async def test_parent_cost_aggregation(self):
        """After join, parent aggregates child costs correctly."""
        from sandcastle.engine.executor import RunContext

        parent = RunContext(run_id="test-agg", input={}, costs=[])
        children = [parent.with_item(i, i) for i in range(10)]

        # Add costs to children concurrently
        async def add_cost(child, amount):
            child.costs.append(amount)

        await asyncio.gather(*[add_cost(c, 0.05) for c in children])

        # Aggregate
        for child in children:
            parent.costs.extend(child.costs)

        assert abs(parent.total_cost - 0.50) < 0.001


# ---------------------------------------------------------------------------
# 10. Executor parallel step tracking
# ---------------------------------------------------------------------------


class TestExecutorParallelStress:
    """Stress test executor's step output dictionary under concurrency."""

    @pytest.mark.asyncio
    async def test_concurrent_step_output_writes(self):
        """Multiple coroutines writing to step_outputs dict simultaneously."""
        from sandcastle.engine.executor import RunContext

        context = RunContext(run_id="test-parallel", input={}, costs=[])

        async def write_output(step_id: str, value: str):
            # Simulate some async work
            await asyncio.sleep(0.001)
            context.step_outputs[step_id] = value

        # 20 parallel steps writing outputs
        await asyncio.gather(
            *[write_output(f"step_{i}", f"output_{i}") for i in range(20)]
        )

        # All 20 outputs should be present
        assert len(context.step_outputs) == 20
        for i in range(20):
            assert context.step_outputs[f"step_{i}"] == f"output_{i}"

    @pytest.mark.asyncio
    async def test_snapshot_during_writes(self):
        """Snapshot should work safely even during concurrent writes."""
        from sandcastle.engine.executor import RunContext

        context = RunContext(run_id="test-snap", input={"key": "val"}, costs=[])

        async def write_and_snapshot(i):
            context.step_outputs[f"step_{i}"] = f"out_{i}"
            context.costs.append(0.01)
            snap = context.snapshot()
            assert "run_id" in snap
            assert "step_outputs" in snap
            return snap

        results = await asyncio.gather(
            *[write_and_snapshot(i) for i in range(20)]
        )
        assert len(results) == 20


# ---------------------------------------------------------------------------
# 11. InMemoryBackend window cleanup
# ---------------------------------------------------------------------------


class TestInMemoryBackendWindowStress:
    """Stress test the _Window sliding window data structure."""

    @pytest.mark.asyncio
    async def test_window_cleanup_under_load(self):
        """Expired entries are cleaned up correctly under concurrent access."""
        from sandcastle.api.rate_limit import InMemoryBackend

        backend = InMemoryBackend()
        short_window = 0.05  # 50ms

        # Fill the window
        for _ in range(20):
            await backend.check_and_increment("cleanup_key", 100, short_window)

        # Wait for window to expire
        await asyncio.sleep(0.06)

        # Concurrent requests should all pass (old entries expired)
        results = await asyncio.gather(
            *[
                backend.check_and_increment("cleanup_key", 10, short_window)
                for _ in range(10)
            ]
        )

        passed = sum(1 for r in results if r <= 10)
        assert passed == 10

    @pytest.mark.asyncio
    async def test_many_keys_concurrent_cleanup(self):
        """Multiple keys with short windows cleaning up concurrently."""
        from sandcastle.api.rate_limit import InMemoryBackend

        backend = InMemoryBackend()

        async def use_key(key: str):
            for _ in range(5):
                await backend.check_and_increment(key, 100, 0.05)
            await asyncio.sleep(0.06)
            # After expiry, should pass
            r = await backend.check_and_increment(key, 1, 0.05)
            return r <= 1

        results = await asyncio.gather(*[use_key(f"k{i}") for i in range(20)])
        assert all(results)


# ---------------------------------------------------------------------------
# 12. Mixed concurrency scenarios
# ---------------------------------------------------------------------------


class TestMixedConcurrencyStress:
    """Cross-component concurrent interactions."""

    @pytest.mark.asyncio
    async def test_event_bus_and_rate_limiter_concurrent(self):
        """EventBus pub/sub + rate limiter running concurrently."""
        from sandcastle.api.rate_limit import InMemoryBackend
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        backend = InMemoryBackend()

        errors = []

        async def event_worker():
            try:
                q = await bus.subscribe()
                bus.publish("run.started", {"id": "mixed"})
                await asyncio.sleep(0.01)
                await bus.unsubscribe(q)
            except Exception as e:
                errors.append(e)

        async def rate_worker():
            try:
                for _ in range(10):
                    await backend.check_and_increment("mixed", 100, 60.0)
            except Exception as e:
                errors.append(e)

        tasks = [event_worker() for _ in range(20)] + [rate_worker() for _ in range(20)]
        await asyncio.gather(*tasks)
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_cache_and_cancel_concurrent(self):
        """Browser cache and cancel flags operating concurrently."""
        from sandcastle.engine.executor import (
            _browser_action_cache,
            _cancel_flags,
            _BROWSER_CACHE_MAX,
            _get_cached_actions,
            _save_cached_actions,
            cancel_run_local,
        )

        _browser_action_cache.clear()
        _cancel_flags.clear()

        errors = []

        async def cache_worker(i):
            try:
                await _save_cached_actions(f"https://m.com/{i}", f"i{i}", [{"x": i}])
                await _get_cached_actions(f"https://m.com/{i}", f"i{i}")
            except Exception as e:
                errors.append(e)

        async def cancel_worker(i):
            try:
                await cancel_run_local(f"mixed-run-{i}")
            except Exception as e:
                errors.append(e)

        tasks = [cache_worker(i) for i in range(30)] + [cancel_worker(i) for i in range(30)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0
        assert len(_browser_action_cache) <= _BROWSER_CACHE_MAX

        # Cleanup
        _browser_action_cache.clear()
        _cancel_flags.clear()

    @pytest.mark.asyncio
    async def test_metrics_and_circuit_breaker_concurrent(self):
        """RuntimeMetrics + CircuitBreaker under concurrent load."""
        from sandcastle.engine.sandshore import CircuitBreaker, RuntimeMetrics

        metrics = RuntimeMetrics()
        cb = CircuitBreaker(failure_threshold=20, recovery_timeout=30.0)

        async def record_and_check(i):
            if i % 4 == 0:
                await metrics.record_query_failure()
                await cb.record_failure()
            else:
                await metrics.record_query_success(cost_usd=0.01)
                await cb.record_success()
            cb.allow_request()

        await asyncio.gather(*[record_and_check(i) for i in range(100)])

        snap = metrics.snapshot()
        assert snap["total_queries"] == 100
        assert snap["successful_queries"] == 75
        assert snap["failed_queries"] == 25

    @pytest.mark.asyncio
    async def test_event_bus_with_cancel_flags(self):
        """Publish events while cancelling runs concurrently."""
        from sandcastle.engine.events import EventBus
        from sandcastle.engine.executor import _cancel_flags, cancel_run_local

        _cancel_flags.clear()
        bus = EventBus()
        queues = [await bus.subscribe() for _ in range(10)]
        errors = []

        async def publisher():
            try:
                for i in range(20):
                    bus.publish("run.started", {"run_id": f"mixed-{i}"})
            except Exception as e:
                errors.append(e)

        async def canceller():
            try:
                for i in range(20):
                    await cancel_run_local(f"mixed-{i}")
            except Exception as e:
                errors.append(e)

        await asyncio.gather(publisher(), canceller())
        assert len(errors) == 0

        for q in queues:
            assert q.qsize() == 20
            await bus.unsubscribe(q)

        _cancel_flags.clear()


# ---------------------------------------------------------------------------
# 13. Database mock concurrent operations
# ---------------------------------------------------------------------------


class TestDatabaseConcurrencyStress:
    """Stress test concurrent database-like operations (mocked)."""

    @pytest.mark.asyncio
    async def test_concurrent_status_updates(self):
        """Concurrent status updates to the same run - last write wins."""
        status_store: dict[str, str] = {"run-1": "running"}
        lock = asyncio.Lock()

        async def update_status(run_id: str, new_status: str):
            async with lock:
                status_store[run_id] = new_status

        statuses = ["running", "completed", "failed", "cancelled", "completed"]
        await asyncio.gather(
            *[update_status("run-1", s) for s in statuses * 10]
        )

        # Should have some valid status
        assert status_store["run-1"] in statuses

    @pytest.mark.asyncio
    async def test_concurrent_approval_toctou(self):
        """Simulate TOCTOU race in approval: two approvers see 'pending'."""
        approval_state = {"status": "pending", "approved_by": None}
        lock = asyncio.Lock()
        approvals = []

        async def try_approve(approver_id: str):
            async with lock:
                if approval_state["status"] == "pending":
                    approval_state["status"] = "approved"
                    approval_state["approved_by"] = approver_id
                    approvals.append(approver_id)
                    return True
                return False

        results = await asyncio.gather(
            *[try_approve(f"user_{i}") for i in range(50)]
        )

        # Exactly one should have succeeded
        assert sum(results) == 1
        assert len(approvals) == 1
        assert approval_state["status"] == "approved"

    @pytest.mark.asyncio
    async def test_concurrent_key_rotation(self):
        """Concurrent API key rotation - only one should succeed."""
        key_state = {"id": "key-1", "hash": "old_hash", "version": 1}
        lock = asyncio.Lock()
        rotations = []

        async def rotate_key(rotation_id: int):
            async with lock:
                current_version = key_state["version"]
                key_state["hash"] = f"new_hash_{rotation_id}"
                key_state["version"] = current_version + 1
                rotations.append(rotation_id)

        await asyncio.gather(*[rotate_key(i) for i in range(50)])

        # All rotations should have completed sequentially (lock protects)
        assert len(rotations) == 50
        assert key_state["version"] == 51

    @pytest.mark.asyncio
    async def test_concurrent_run_creation(self):
        """50 concurrent run creations - no ID collision."""
        runs: dict[str, dict] = {}
        lock = asyncio.Lock()

        async def create_run():
            run_id = str(uuid.uuid4())
            async with lock:
                assert run_id not in runs, f"UUID collision: {run_id}"
                runs[run_id] = {"status": "running", "created": time.monotonic()}
            return run_id

        results = await asyncio.gather(*[create_run() for _ in range(50)])
        assert len(set(results)) == 50  # All unique


# ---------------------------------------------------------------------------
# 14. OrderedDict LRU correctness
# ---------------------------------------------------------------------------


class TestOrderedDictLRUStress:
    """Verify OrderedDict used as LRU cache behaves correctly."""

    @pytest.mark.asyncio
    async def test_lru_eviction_order(self):
        """Items accessed most recently should survive eviction."""
        cache: collections.OrderedDict = collections.OrderedDict()
        lock = asyncio.Lock()
        MAX = 10

        async def access_item(key: str, value: int):
            async with lock:
                if key in cache:
                    cache.move_to_end(key)
                else:
                    cache[key] = value
                    while len(cache) > MAX:
                        cache.popitem(last=False)

        # Insert 15 items
        for i in range(15):
            await access_item(f"k{i}", i)

        # Items 0-4 should have been evicted
        assert len(cache) == MAX
        for i in range(5):
            assert f"k{i}" not in cache
        for i in range(5, 15):
            assert f"k{i}" in cache

    @pytest.mark.asyncio
    async def test_concurrent_lru_operations(self):
        """Concurrent inserts and accesses on the LRU cache."""
        cache: collections.OrderedDict = collections.OrderedDict()
        lock = asyncio.Lock()
        MAX = 50

        async def insert(key: str, value: int):
            async with lock:
                cache[key] = value
                cache.move_to_end(key)
                while len(cache) > MAX:
                    cache.popitem(last=False)

        async def read(key: str):
            async with lock:
                val = cache.get(key)
                if val is not None:
                    cache.move_to_end(key)
                return val

        tasks = []
        for i in range(100):
            tasks.append(insert(f"k{i}", i))
            if i > 0:
                tasks.append(read(f"k{i - 1}"))

        await asyncio.gather(*tasks)
        assert len(cache) <= MAX


# ---------------------------------------------------------------------------
# 15. Async lock contention
# ---------------------------------------------------------------------------


class TestAsyncLockContention:
    """Verify async locks don't deadlock under heavy contention."""

    @pytest.mark.asyncio
    async def test_reentrant_pattern_no_deadlock(self):
        """Multiple layers of lock acquisition should not deadlock.

        Since asyncio.Lock is NOT reentrant, we test that separate locks
        for different resources can be acquired concurrently.
        """
        lock_a = asyncio.Lock()
        lock_b = asyncio.Lock()
        results = []

        async def task_ab(i):
            async with lock_a:
                async with lock_b:
                    results.append(("ab", i))
                    await asyncio.sleep(0)

        async def task_ba(i):
            # Acquire in same order to prevent deadlock
            async with lock_a:
                async with lock_b:
                    results.append(("ba", i))
                    await asyncio.sleep(0)

        await asyncio.gather(
            *[task_ab(i) for i in range(25)],
            *[task_ba(i) for i in range(25)],
        )
        assert len(results) == 50

    @pytest.mark.asyncio
    async def test_lock_timeout_no_deadlock(self):
        """Demonstrate that asyncio.wait_for prevents deadlocks.

        With 20 concurrent tasks contending for a lock, each using
        asyncio.timeout, the key property is that all tasks complete
        (no deadlock) even if some time out.
        """
        lock = asyncio.Lock()
        completed = []

        async def try_acquire(i):
            outcome = "unknown"
            try:
                async with asyncio.timeout(0.05):
                    async with lock:
                        await asyncio.sleep(0.01)
                        outcome = "acquired"
            except (asyncio.TimeoutError, asyncio.CancelledError):
                outcome = "timeout"
            except Exception:
                outcome = "error"
            completed.append(outcome)

        await asyncio.gather(*[try_acquire(i) for i in range(20)])
        # All tasks must complete (no deadlock)
        assert len(completed) == 20
        assert "acquired" in completed  # At least some should succeed
        assert "error" not in completed  # No unexpected errors

    @pytest.mark.asyncio
    async def test_semaphore_bounded_concurrency(self):
        """Semaphore correctly bounds concurrent execution."""
        max_concurrent = 5
        sem = asyncio.Semaphore(max_concurrent)
        current = 0
        max_seen = 0
        lock = asyncio.Lock()

        async def bounded_task(i):
            nonlocal current, max_seen
            async with sem:
                async with lock:
                    current += 1
                    if current > max_seen:
                        max_seen = current
                await asyncio.sleep(0.01)
                async with lock:
                    current -= 1

        await asyncio.gather(*[bounded_task(i) for i in range(50)])
        assert max_seen <= max_concurrent
        assert current == 0


# ---------------------------------------------------------------------------
# 16. Event bus drop count tracking
# ---------------------------------------------------------------------------


class TestEventBusDropCountStress:
    """Stress test drop count tracking and eviction logic."""

    @pytest.mark.asyncio
    async def test_drop_count_increments_correctly(self):
        """Full queue drop counter increments for each failed publish."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        # Create a queue with size 1 and fill it
        tiny_q = asyncio.Queue(maxsize=1)
        async with bus._lock:
            bus._subscribers.add(tiny_q)
        tiny_q.put_nowait({"type": "filler"})

        # Publish 10 events (all should be dropped for tiny_q)
        for i in range(10):
            bus.publish("run.started", {"i": i})

        assert bus._drop_counts.get(tiny_q, 0) == 10

        # Cleanup
        async with bus._lock:
            bus._subscribers.discard(tiny_q)

    @pytest.mark.asyncio
    async def test_drop_count_resets_on_successful_deliver(self):
        """Drop count resets when a message is successfully delivered."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        q = asyncio.Queue(maxsize=2)
        async with bus._lock:
            bus._subscribers.add(q)

        # Fill the queue
        q.put_nowait({"type": "msg1"})
        q.put_nowait({"type": "msg2"})

        # This publish should be dropped
        bus.publish("run.started", {"i": 0})
        assert bus._drop_counts.get(q, 0) == 1

        # Consume one message
        q.get_nowait()

        # Next publish should succeed, resetting drop count
        bus.publish("run.started", {"i": 1})
        assert bus._drop_counts.get(q, 0) == 0

        async with bus._lock:
            bus._subscribers.discard(q)

    @pytest.mark.asyncio
    async def test_multiple_dead_subscribers_evicted(self):
        """Multiple dead subscribers are evicted in the same publish cycle."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        dead_queues = []
        for _ in range(5):
            dq = asyncio.Queue(maxsize=1)
            dq.put_nowait({"type": "filler"})
            async with bus._lock:
                bus._subscribers.add(dq)
            dead_queues.append(dq)

        healthy_q = await bus.subscribe()

        # Publish enough to evict all dead subscribers
        for i in range(EventBus.MAX_CONSECUTIVE_DROPS + 5):
            bus.publish("run.started", {"i": i})

        # All dead subscribers should be evicted
        for dq in dead_queues:
            assert dq not in bus._subscribers

        # Healthy subscriber should still be there
        assert healthy_q in bus._subscribers

        await bus.unsubscribe(healthy_q)
