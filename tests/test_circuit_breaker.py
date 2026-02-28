"""Tests for CircuitBreaker, RuntimeMetrics, and SandshoreResult."""

import asyncio
import time

import pytest

from sandcastle.engine.sandshore import CircuitBreaker, RuntimeMetrics, SandshoreResult


# ------------------------------------------------------------------
# CircuitBreaker
# ------------------------------------------------------------------


class TestCircuitBreaker:

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitBreaker.CLOSED

    @pytest.mark.asyncio
    async def test_allow_request_when_closed(self):
        cb = CircuitBreaker()
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_stays_closed_under_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_trips_open_at_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        for _ in range(3):
            await cb.record_failure()
        assert cb._state == CircuitBreaker.OPEN
        assert cb.state == CircuitBreaker.OPEN
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_success_resets_to_closed(self):
        cb = CircuitBreaker(failure_threshold=2)
        await cb.record_failure()
        await cb.record_failure()
        assert cb._state == CircuitBreaker.OPEN
        await cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        await cb.record_failure()
        assert cb._state == CircuitBreaker.OPEN
        await asyncio.sleep(0.06)
        assert cb.state == CircuitBreaker.HALF_OPEN
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_success_after_half_open_resets(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        await asyncio.sleep(0.02)
        assert cb.state == CircuitBreaker.HALF_OPEN
        await cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_failure_count_accumulates(self):
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            await cb.record_failure()
        assert cb._failure_count == 4
        assert cb.state == CircuitBreaker.CLOSED

    @pytest.mark.asyncio
    async def test_success_between_failures_resets_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()
        await cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb._failure_count == 1


# ------------------------------------------------------------------
# RuntimeMetrics
# ------------------------------------------------------------------


class TestRuntimeMetrics:

    @pytest.mark.asyncio
    async def test_initial_snapshot(self):
        m = RuntimeMetrics()
        snap = m.snapshot()
        assert snap["total_queries"] == 0
        assert snap["successful_queries"] == 0
        assert snap["failed_queries"] == 0
        assert snap["failover_attempts"] == 0
        assert snap["circuit_breaker_rejections"] == 0
        assert snap["total_cost_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_record_success(self):
        m = RuntimeMetrics()
        await m.record_query_success(cost_usd=0.05)
        snap = m.snapshot()
        assert snap["total_queries"] == 1
        assert snap["successful_queries"] == 1
        assert snap["total_cost_usd"] == 0.05

    @pytest.mark.asyncio
    async def test_record_failure(self):
        m = RuntimeMetrics()
        await m.record_query_failure()
        snap = m.snapshot()
        assert snap["total_queries"] == 1
        assert snap["failed_queries"] == 1

    @pytest.mark.asyncio
    async def test_record_failover(self):
        m = RuntimeMetrics()
        await m.record_failover()
        assert m.snapshot()["failover_attempts"] == 1

    @pytest.mark.asyncio
    async def test_record_cb_rejection(self):
        m = RuntimeMetrics()
        await m.record_circuit_breaker_rejection()
        assert m.snapshot()["circuit_breaker_rejections"] == 1

    @pytest.mark.asyncio
    async def test_mixed_operations(self):
        m = RuntimeMetrics()
        await m.record_query_success(0.01)
        await m.record_query_success(0.02)
        await m.record_query_failure()
        await m.record_failover()
        snap = m.snapshot()
        assert snap["total_queries"] == 3
        assert snap["successful_queries"] == 2
        assert snap["failed_queries"] == 1
        assert snap["failover_attempts"] == 1
        assert snap["total_cost_usd"] == 0.03

    @pytest.mark.asyncio
    async def test_concurrent_recording(self):
        m = RuntimeMetrics()
        tasks = [m.record_query_success(0.001) for _ in range(50)]
        await asyncio.gather(*tasks)
        snap = m.snapshot()
        assert snap["total_queries"] == 50
        assert snap["successful_queries"] == 50


# ------------------------------------------------------------------
# SandshoreResult
# ------------------------------------------------------------------


class TestSandshoreResult:

    def test_defaults(self):
        r = SandshoreResult()
        assert r.text == ""
        assert r.structured_output is None
        assert r.total_cost_usd == 0.0
        assert r.num_turns == 0
        assert r.input_tokens == 0
        assert r.output_tokens == 0

    def test_with_values(self):
        r = SandshoreResult(
            text="hello",
            structured_output={"key": "value"},
            total_cost_usd=0.123,
            num_turns=3,
            input_tokens=100,
            output_tokens=200,
        )
        assert r.text == "hello"
        assert r.structured_output == {"key": "value"}
        assert r.total_cost_usd == 0.123
        assert r.num_turns == 3
        assert r.input_tokens == 100
        assert r.output_tokens == 200
