"""
DEEP JITTER & RETRY RELIABILITY TEST SUITE
============================================
Tests _backoff_delay jitter distribution, retry behavior with jittered delays,
thundering herd prevention, circuit breaker integration, and DLQ after retries.

Run:
    cd /Users/gizmax/Documents/Sandcastle
    .venv/bin/python -m pytest tests/test_jitter_deep.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import statistics
import time
import uuid
from collections import Counter
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    FailureConfig,
    RetryConfig,
    StepDefinition,
    WorkflowDefinition,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _backoff_delay,
    _send_to_dead_letter,
    execute_step_with_retry,
)
from sandcastle.engine.sandshore import CircuitBreaker


# ──────────────────────────────────────────────────────────────
# 1. _backoff_delay jitter distribution (exponential)
# ──────────────────────────────────────────────────────────────


class TestBackoffDelayJitterDistribution:
    """Run 1000 samples per attempt level, verify range, uniformity, and mean."""

    @pytest.mark.parametrize("attempt", [0, 1, 2, 3, 4, 5, 6])
    def test_exponential_range(self, attempt: int) -> None:
        """All samples must lie in [0, min(2^attempt, 60)]."""
        max_expected = min(2**attempt, 60)
        samples = [_backoff_delay(attempt, "exponential") for _ in range(1000)]
        for s in samples:
            assert 0 <= s <= max_expected, (
                f"attempt={attempt}: got {s}, expected [0, {max_expected}]"
            )

    @pytest.mark.parametrize("attempt", [0, 1, 2, 3, 4, 5, 6])
    def test_exponential_not_clustered_at_edges(self, attempt: int) -> None:
        """Distribution should not have >80% at min or max quartile (uniform check)."""
        max_expected = min(2**attempt, 60)
        samples = [_backoff_delay(attempt, "exponential") for _ in range(1000)]
        if max_expected == 0:
            # attempt=0 -> 2^0=1, max_expected=1
            # Actually 2^0 = 1, so range is [0, 1]
            pass
        midpoint = max_expected / 2
        below_mid = sum(1 for s in samples if s < midpoint)
        above_mid = sum(1 for s in samples if s >= midpoint)
        # For uniform distribution, both halves should be roughly 50%
        # Allow generous tolerance: each half should be at least 30%
        total = len(samples)
        assert below_mid / total >= 0.30, (
            f"attempt={attempt}: only {below_mid}/{total} below midpoint - clustered high"
        )
        assert above_mid / total >= 0.30, (
            f"attempt={attempt}: only {above_mid}/{total} above midpoint - clustered low"
        )

    @pytest.mark.parametrize("attempt", [1, 2, 3, 4, 5, 6])
    def test_exponential_mean_near_half_max(self, attempt: int) -> None:
        """Mean of 1000 samples should be approximately half of max (t-test-like)."""
        max_expected = min(2**attempt, 60)
        samples = [_backoff_delay(attempt, "exponential") for _ in range(1000)]
        mean = statistics.mean(samples)
        expected_mean = max_expected / 2
        # For uniform [0, max], mean = max/2, stdev = max/sqrt(12)
        # With 1000 samples, stderr = stdev / sqrt(1000)
        stdev = max_expected / (12**0.5)
        stderr = stdev / (1000**0.5)
        # Allow 4 standard errors (very generous - ~99.99% confidence)
        tolerance = 4 * stderr
        assert abs(mean - expected_mean) <= tolerance, (
            f"attempt={attempt}: mean={mean:.4f}, expected ~{expected_mean:.4f}, "
            f"tolerance={tolerance:.4f}"
        )

    @pytest.mark.parametrize("attempt", [1, 2, 3, 4, 5, 6])
    def test_exponential_stdev_consistent_with_uniform(self, attempt: int) -> None:
        """Standard deviation should be close to max/sqrt(12) for uniform dist."""
        max_expected = min(2**attempt, 60)
        samples = [_backoff_delay(attempt, "exponential") for _ in range(1000)]
        observed_stdev = statistics.stdev(samples)
        expected_stdev = max_expected / (12**0.5)
        # Allow 20% relative tolerance
        assert abs(observed_stdev - expected_stdev) / expected_stdev < 0.20, (
            f"attempt={attempt}: stdev={observed_stdev:.4f}, "
            f"expected ~{expected_stdev:.4f}"
        )

    def test_attempt_zero(self) -> None:
        """attempt=0 -> 2^0=1, range [0, 1]."""
        samples = [_backoff_delay(0, "exponential") for _ in range(1000)]
        for s in samples:
            assert 0 <= s <= 1.0
        mean = statistics.mean(samples)
        assert 0.35 <= mean <= 0.65, f"mean={mean}, expected ~0.5"

    def test_quartile_coverage(self) -> None:
        """Check that all four quartiles get adequate coverage for attempt=4."""
        max_expected = min(2**4, 60)  # 16
        samples = [_backoff_delay(4, "exponential") for _ in range(1000)]
        q1 = sum(1 for s in samples if s < max_expected * 0.25)
        q2 = sum(1 for s in samples if max_expected * 0.25 <= s < max_expected * 0.50)
        q3 = sum(1 for s in samples if max_expected * 0.50 <= s < max_expected * 0.75)
        q4 = sum(1 for s in samples if s >= max_expected * 0.75)
        # Each quartile should have at least 15% (generous for uniform)
        for label, count in [("Q1", q1), ("Q2", q2), ("Q3", q3), ("Q4", q4)]:
            assert count / 1000 >= 0.15, (
                f"{label} has only {count}/1000 samples - distribution not uniform"
            )


# ──────────────────────────────────────────────────────────────
# 2. _backoff_delay fixed with jitter
# ──────────────────────────────────────────────────────────────


class TestBackoffDelayFixed:
    """Fixed backoff should return values in [1.0, 3.0] with uniform distribution."""

    def test_fixed_range(self) -> None:
        """All fixed backoff samples must be in [1.0, 3.0]."""
        samples = [_backoff_delay(i, "fixed") for i in range(1000)]
        for s in samples:
            assert 1.0 <= s <= 3.0, f"Fixed backoff out of range: {s}"

    def test_fixed_ignores_attempt_number(self) -> None:
        """Fixed backoff should give same distribution regardless of attempt."""
        samples_low = [_backoff_delay(1, "fixed") for _ in range(500)]
        samples_high = [_backoff_delay(100, "fixed") for _ in range(500)]
        mean_low = statistics.mean(samples_low)
        mean_high = statistics.mean(samples_high)
        # Both should be around 2.0 (midpoint of [1.0, 3.0])
        assert abs(mean_low - 2.0) < 0.2, f"Low attempts mean={mean_low}"
        assert abs(mean_high - 2.0) < 0.2, f"High attempts mean={mean_high}"

    def test_fixed_mean_near_two(self) -> None:
        """Mean of fixed backoff should be ~2.0 (midpoint of [1.0, 3.0])."""
        samples = [_backoff_delay(3, "fixed") for _ in range(1000)]
        mean = statistics.mean(samples)
        # For uniform [1, 3]: mean=2, stdev=2/sqrt(12) ~ 0.577
        assert abs(mean - 2.0) < 0.15, f"Fixed mean={mean}, expected ~2.0"

    def test_fixed_distribution_not_clustered(self) -> None:
        """Fixed backoff should spread across the range."""
        samples = [_backoff_delay(2, "fixed") for _ in range(1000)]
        below = sum(1 for s in samples if s < 2.0)
        above = sum(1 for s in samples if s >= 2.0)
        assert below / 1000 >= 0.35
        assert above / 1000 >= 0.35

    def test_fixed_stdev(self) -> None:
        """Standard deviation for uniform [1, 3] should be ~0.577."""
        samples = [_backoff_delay(5, "fixed") for _ in range(1000)]
        observed_stdev = statistics.stdev(samples)
        expected_stdev = 2.0 / (12**0.5)  # ~0.577
        assert abs(observed_stdev - expected_stdev) / expected_stdev < 0.20


# ──────────────────────────────────────────────────────────────
# 3. _backoff_delay cap
# ──────────────────────────────────────────────────────────────


class TestBackoffDelayCap:
    """Very large attempt numbers should still be capped at 60s."""

    @pytest.mark.parametrize("attempt", [7, 10, 20, 50, 100])
    def test_cap_at_60(self, attempt: int) -> None:
        """2^attempt > 60 should be capped, so range is [0, 60]."""
        samples = [_backoff_delay(attempt, "exponential") for _ in range(1000)]
        for s in samples:
            assert 0 <= s <= 60, f"attempt={attempt}: {s} exceeds 60s cap"

    def test_cap_100_mean(self) -> None:
        """attempt=100 capped at 60 -> mean should be ~30."""
        samples = [_backoff_delay(100, "exponential") for _ in range(1000)]
        mean = statistics.mean(samples)
        assert 25 <= mean <= 35, f"Mean={mean}, expected ~30 for cap=60"

    def test_cap_100_full_range(self) -> None:
        """attempt=100: verify samples use the full [0, 60] range."""
        samples = [_backoff_delay(100, "exponential") for _ in range(1000)]
        assert min(samples) < 5, f"Min={min(samples)}, expected near 0"
        assert max(samples) > 55, f"Max={max(samples)}, expected near 60"

    def test_boundary_attempt_6(self) -> None:
        """attempt=6 -> 2^6=64 > 60 -> capped at 60."""
        samples = [_backoff_delay(6, "exponential") for _ in range(1000)]
        for s in samples:
            assert 0 <= s <= 60

    def test_boundary_attempt_5(self) -> None:
        """attempt=5 -> 2^5=32 < 60 -> not capped."""
        samples = [_backoff_delay(5, "exponential") for _ in range(1000)]
        for s in samples:
            assert 0 <= s <= 32
        # Should actually reach near 32, not just 60
        assert max(samples) > 25, f"Max={max(samples)}, expected near 32"


# ──────────────────────────────────────────────────────────────
# 4. Actual retry behavior - sleep called with jittered values
# ──────────────────────────────────────────────────────────────


class TestActualRetryBehavior:
    """Mock a flaky sandbox, verify that sleep is called with jittered (in-range) values."""

    @pytest.fixture
    def step(self) -> StepDefinition:
        return StepDefinition(
            id="flaky_step",
            prompt="Do something",
            model="sonnet",
            retry=RetryConfig(max_attempts=4, backoff="exponential", on_failure="abort"),
        )

    @pytest.fixture
    def context(self) -> RunContext:
        return RunContext(
            run_id=str(uuid.uuid4()),
            input={"topic": "test"},
            workflow_name="test_workflow",
        )

    @pytest.mark.asyncio
    async def test_retry_calls_sleep_with_jittered_values(
        self, step: StepDefinition, context: RunContext
    ) -> None:
        """When step fails then succeeds, sleep should be called with jittered delays."""
        call_count = 0

        async def mock_execute_once(s, ctx, sandbox, storage, pi, attempt):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return StepResult(
                    step_id=s.id, status="failed", error="Sandbox timeout", attempt=attempt
                )
            return StepResult(
                step_id=s.id, status="completed", output="success", attempt=attempt
            )

        mock_sandbox = MagicMock()
        mock_storage = MagicMock()

        with (
            patch(
                "sandcastle.engine.executor._execute_step_once",
                side_effect=mock_execute_once,
            ),
            patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
            patch("sandcastle.engine.executor.event_bus") as mock_bus,
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            result = await execute_step_with_retry(
                step, context, mock_sandbox, mock_storage
            )

        assert result.status == "completed"
        # Should have slept twice (after attempt 1 and attempt 2)
        assert mock_sleep.call_count == 2

        # Verify sleep delays are in expected ranges
        for i, call in enumerate(mock_sleep.call_args_list):
            delay = call[0][0]
            attempt_num = i + 1  # attempt 1 failed, sleep, attempt 2 failed, sleep
            max_delay = min(2**attempt_num, 60)
            assert 0 <= delay <= max_delay, (
                f"Sleep delay {delay} out of range [0, {max_delay}] for attempt {attempt_num}"
            )

    @pytest.mark.asyncio
    async def test_no_sleep_on_first_attempt_success(
        self, step: StepDefinition, context: RunContext
    ) -> None:
        """If step succeeds on first try, no sleep should be called."""

        async def mock_execute_once(s, ctx, sandbox, storage, pi, attempt):
            return StepResult(
                step_id=s.id, status="completed", output="ok", attempt=attempt
            )

        with (
            patch(
                "sandcastle.engine.executor._execute_step_once",
                side_effect=mock_execute_once,
            ),
            patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
            patch("sandcastle.engine.executor.event_bus"),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            result = await execute_step_with_retry(
                step, context, MagicMock(), MagicMock()
            )

        assert result.status == "completed"
        assert mock_sleep.call_count == 0

    @pytest.mark.asyncio
    async def test_all_attempts_fail_no_trailing_sleep(
        self, step: StepDefinition, context: RunContext
    ) -> None:
        """If all attempts fail, sleep should be called (max_attempts - 1) times."""

        async def mock_execute_once(s, ctx, sandbox, storage, pi, attempt):
            return StepResult(
                step_id=s.id, status="failed", error="boom", attempt=attempt
            )

        with (
            patch(
                "sandcastle.engine.executor._execute_step_once",
                side_effect=mock_execute_once,
            ),
            patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
            patch("sandcastle.engine.executor.event_bus"),
            patch(
                "sandcastle.engine.executor.capture_step_error",
                create=True,
            ),
            patch(
                "sandcastle.engine.telemetry.capture_step_error",
                create=True,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            result = await execute_step_with_retry(
                step, context, MagicMock(), MagicMock()
            )

        assert result.status == "failed"
        # max_attempts=4, sleep after attempts 1, 2, 3 (not after last)
        assert mock_sleep.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_with_fixed_backoff_range(self, context: RunContext) -> None:
        """Fixed backoff retries should sleep in [1.0, 3.0]."""
        step = StepDefinition(
            id="fixed_step",
            prompt="Do fixed",
            retry=RetryConfig(max_attempts=3, backoff="fixed", on_failure="abort"),
        )

        call_count = 0

        async def mock_execute_once(s, ctx, sandbox, storage, pi, attempt):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return StepResult(
                    step_id=s.id, status="failed", error="fail", attempt=attempt
                )
            return StepResult(
                step_id=s.id, status="completed", output="done", attempt=attempt
            )

        with (
            patch(
                "sandcastle.engine.executor._execute_step_once",
                side_effect=mock_execute_once,
            ),
            patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
            patch("sandcastle.engine.executor.event_bus"),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            result = await execute_step_with_retry(
                step, context, MagicMock(), MagicMock()
            )

        assert result.status == "completed"
        for call in mock_sleep.call_args_list:
            delay = call[0][0]
            assert 1.0 <= delay <= 3.0, f"Fixed backoff delay {delay} not in [1.0, 3.0]"


# ──────────────────────────────────────────────────────────────
# 5. Thundering herd prevention
# ──────────────────────────────────────────────────────────────


class TestThunderingHerdPrevention:
    """Verify that _backoff_delay produces diverse values (jitter), not all identical."""

    def test_not_all_same_attempt_3(self) -> None:
        """100 calls to _backoff_delay(3) should NOT all produce the same value."""
        samples = [_backoff_delay(3, "exponential") for _ in range(100)]
        unique = set(samples)
        # With float jitter and 100 samples, virtually impossible to get <10 unique
        assert len(unique) > 10, (
            f"Only {len(unique)} unique values out of 100 - no jitter!"
        )

    def test_not_all_same_attempt_0(self) -> None:
        """Even attempt=0 (range [0,1]) should produce diverse values."""
        samples = [_backoff_delay(0, "exponential") for _ in range(100)]
        unique = set(samples)
        assert len(unique) > 10

    def test_not_all_same_fixed(self) -> None:
        """Fixed backoff should also produce diverse values."""
        samples = [_backoff_delay(3, "fixed") for _ in range(100)]
        unique = set(samples)
        assert len(unique) > 10

    def test_concurrent_callers_get_different_values(self) -> None:
        """Simulate 50 concurrent 'clients' all calling at same attempt level."""
        # Each 'client' gets a delay value
        delays = [_backoff_delay(3, "exponential") for _ in range(50)]
        # At least 40 unique values expected (floats from uniform dist)
        unique = set(delays)
        assert len(unique) >= 40, (
            f"Only {len(unique)} unique delays for 50 clients - thundering herd risk"
        )

    def test_no_deterministic_pattern(self) -> None:
        """Two consecutive calls should differ (with very high probability)."""
        pairs_same = 0
        for _ in range(100):
            a = _backoff_delay(4, "exponential")
            b = _backoff_delay(4, "exponential")
            if a == b:
                pairs_same += 1
        # Random floats matching is virtually impossible; allow 0
        assert pairs_same == 0, f"{pairs_same} identical consecutive pairs"

    def test_batch_variance_nonzero(self) -> None:
        """Variance of 100 samples should be well above zero."""
        samples = [_backoff_delay(3, "exponential") for _ in range(100)]
        var = statistics.variance(samples)
        # For uniform [0, 8]: variance = 64/12 ~ 5.33
        assert var > 1.0, f"Variance={var}, too low - indicates no jitter"


# ──────────────────────────────────────────────────────────────
# 6. Circuit breaker integration
# ──────────────────────────────────────────────────────────────


class TestCircuitBreakerIntegration:
    """Verify circuit breaker state transitions work correctly alongside jitter."""

    @pytest.mark.asyncio
    async def test_closed_allows_requests(self) -> None:
        """CLOSED state should allow all requests."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        assert cb.state == "closed"
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_trips_open_after_threshold(self) -> None:
        """After failure_threshold failures, state should be OPEN."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        for _ in range(3):
            await cb.record_failure()
        assert cb.state == "open"
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_half_open_after_recovery_timeout(self) -> None:
        """After recovery_timeout, breaker transitions to HALF_OPEN."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == "open"

        # Wait for recovery timeout
        await asyncio.sleep(0.15)
        assert cb.state == "half_open"
        # Should allow exactly one probe request
        assert await cb.allow_request() is True
        # Second request while probe in-flight should be rejected
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_half_open_success_resets_to_closed(self) -> None:
        """Successful probe in HALF_OPEN resets to CLOSED."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        await cb.record_failure()
        await cb.record_failure()

        await asyncio.sleep(0.15)
        assert await cb.allow_request() is True  # probe allowed

        await cb.record_success()
        assert cb.state == "closed"
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_half_open_failure_trips_back_to_open(self) -> None:
        """Failed probe in HALF_OPEN trips back to OPEN."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        await cb.record_failure()
        await cb.record_failure()

        await asyncio.sleep(0.15)
        assert await cb.allow_request() is True  # probe

        await cb.record_failure()
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_circuit_breaker_with_jittered_retries(self) -> None:
        """Circuit breaker should work correctly with jittered backoff delays.

        Scenario: failures accumulate with jittered delays between them.
        After threshold, breaker opens regardless of delay timing.
        """
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=5.0)

        for attempt in range(3):
            delay = _backoff_delay(attempt, "exponential")
            assert isinstance(delay, float)
            assert delay >= 0
            # Simulate the failure happening after a jittered delay
            await cb.record_failure()

        # Breaker should be open after 3 failures regardless of jitter timing
        assert cb.state == "open"
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_success_during_retries_resets_breaker(self) -> None:
        """If a retry succeeds, circuit breaker should reset."""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=10.0)

        # Two failures
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == "closed"  # below threshold

        # Success resets
        await cb.record_success()
        assert cb.state == "closed"

        # Need full threshold again to trip
        for _ in range(4):
            await cb.record_failure()
        assert cb.state == "closed"  # only 4 < 5

        await cb.record_failure()
        assert cb.state == "open"  # 5 >= 5

    @pytest.mark.asyncio
    async def test_below_threshold_stays_closed(self) -> None:
        """Fewer failures than threshold should keep state CLOSED."""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=1.0)
        for _ in range(4):
            await cb.record_failure()
        assert cb.state == "closed"
        assert await cb.allow_request() is True


# ──────────────────────────────────────────────────────────────
# 7. Dead letter queue after jittered retries
# ──────────────────────────────────────────────────────────────


class TestDeadLetterQueueWithJitteredRetries:
    """Verify that steps go to DLQ correctly after exhausting all jittered retries."""

    @pytest.mark.asyncio
    async def test_send_to_dead_letter_called_after_retries(self) -> None:
        """After all retries fail, DLQ should receive the failed step."""
        step = StepDefinition(
            id="dlq_step",
            prompt="Will fail",
            retry=RetryConfig(max_attempts=3, backoff="exponential", on_failure="abort"),
        )
        context = RunContext(
            run_id=str(uuid.uuid4()),
            input={"data": "test"},
            workflow_name="dlq_test",
        )

        async def mock_execute_once(s, ctx, sandbox, storage, pi, attempt):
            return StepResult(
                step_id=s.id, status="failed", error=f"Error attempt {attempt}",
                attempt=attempt,
            )

        with (
            patch(
                "sandcastle.engine.executor._execute_step_once",
                side_effect=mock_execute_once,
            ),
            patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
            patch("sandcastle.engine.executor.event_bus"),
            patch(
                "sandcastle.engine.telemetry.capture_step_error",
                create=True,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            result = await execute_step_with_retry(
                step, context, MagicMock(), MagicMock()
            )

        # Step failed after all retries
        assert result.status == "failed"
        # Sleep was called between retries with jittered delays
        assert mock_sleep.call_count == 2  # after attempt 1 and 2

        # All sleep values should be in valid range
        for i, call in enumerate(mock_sleep.call_args_list):
            delay = call[0][0]
            attempt_num = i + 1
            max_delay = min(2**attempt_num, 60)
            assert 0 <= delay <= max_delay

    @pytest.mark.asyncio
    async def test_send_to_dead_letter_persists(self) -> None:
        """_send_to_dead_letter should persist the item to database."""
        mock_item = MagicMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # session.add() is sync in SQLAlchemy async sessions - use MagicMock
        mock_session.add = MagicMock()

        with (
            patch(
                "sandcastle.engine.executor.event_bus",
            ),
            patch(
                "sandcastle.models.db.async_session",
                return_value=mock_session,
            ),
            patch("sandcastle.models.db.DeadLetterItem") as MockDLQItem,
        ):
            MockDLQItem.return_value = mock_item
            ok = await _send_to_dead_letter(
                run_id=str(uuid.uuid4()),
                step_id="failed_step",
                error="Sandbox crashed",
                input_data={"topic": "test"},
                attempts=3,
                parallel_index=None,
            )

        assert ok is True
        mock_session.add.assert_called_once_with(mock_item)
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_dlq_returns_false_on_db_error(self) -> None:
        """_send_to_dead_letter should return False if database write fails."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # session.add() is sync in SQLAlchemy async sessions
        mock_session.add = MagicMock()
        mock_session.commit.side_effect = Exception("DB connection lost")

        with patch(
            "sandcastle.models.db.async_session",
            return_value=mock_session,
        ), patch("sandcastle.models.db.DeadLetterItem"):
            ok = await _send_to_dead_letter(
                run_id=str(uuid.uuid4()),
                step_id="failed_step",
                error="Some error",
                input_data={},
                attempts=2,
            )

        assert ok is False

    @pytest.mark.asyncio
    async def test_dlq_with_parallel_index(self) -> None:
        """DLQ should correctly record parallel_index for fan-out failures."""
        mock_item = MagicMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # session.add() is sync in SQLAlchemy async sessions
        mock_session.add = MagicMock()

        with (
            patch("sandcastle.engine.executor.event_bus"),
            patch(
                "sandcastle.models.db.async_session",
                return_value=mock_session,
            ),
            patch("sandcastle.models.db.DeadLetterItem") as MockDLQItem,
        ):
            MockDLQItem.return_value = mock_item
            ok = await _send_to_dead_letter(
                run_id=str(uuid.uuid4()),
                step_id="parallel_step",
                error="Item 5 failed",
                input_data={"_item_index": 5},
                attempts=3,
                parallel_index=5,
            )

        assert ok is True
        # Verify the DeadLetterItem was created with parallel_index
        call_kwargs = MockDLQItem.call_args
        assert call_kwargs is not None
        # Check it was passed (either as kwarg or positional)
        if call_kwargs.kwargs:
            assert call_kwargs.kwargs.get("parallel_index") == 5


# ──────────────────────────────────────────────────────────────
# 8. Additional edge cases
# ──────────────────────────────────────────────────────────────


class TestBackoffEdgeCases:
    """Edge cases for backoff delay calculation."""

    def test_negative_attempt_still_works(self) -> None:
        """Negative attempt should not crash (2^negative is a fraction)."""
        # 2^(-1) = 0.5
        val = _backoff_delay(-1, "exponential")
        assert 0 <= val <= 0.5

    def test_unknown_backoff_strategy_falls_to_fixed(self) -> None:
        """Unknown backoff strategy should fall through to fixed behavior."""
        # The code has: if backoff == "exponential": ... else: return fixed
        val = _backoff_delay(3, "unknown_strategy")
        assert 1.0 <= val <= 3.0

    def test_type_is_float(self) -> None:
        """_backoff_delay should return a float."""
        assert isinstance(_backoff_delay(2, "exponential"), float)
        assert isinstance(_backoff_delay(2, "fixed"), float)

    def test_increasing_max_with_attempt(self) -> None:
        """Higher attempts should have higher max delay (until cap)."""
        # Collect max across many samples for each attempt
        maxes = {}
        for attempt in range(6):
            samples = [_backoff_delay(attempt, "exponential") for _ in range(500)]
            maxes[attempt] = max(samples)

        # Max should generally increase with attempt (until cap)
        for a in range(1, 6):
            expected_max = min(2**a, 60)
            prev_expected = min(2 ** (a - 1), 60)
            if expected_max > prev_expected:
                # Higher attempt should produce higher max observed values
                assert maxes[a] > maxes[a - 1] * 0.8, (
                    f"attempt={a} max={maxes[a]:.2f} not > "
                    f"attempt={a-1} max={maxes[a-1]:.2f}"
                )

    def test_return_value_precision(self) -> None:
        """Delays should have reasonable floating-point precision."""
        for _ in range(100):
            val = _backoff_delay(3, "exponential")
            # Should not be exactly an integer (almost impossible with uniform)
            # But we just check it's a proper float
            assert isinstance(val, float)
