"""Tests for performance features: rate limiter, semaphore, cache, cancellation."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from sandcastle.api.rate_limit import RateLimiter, execution_limiter
from sandcastle.engine.sandshore import SandshoreRuntime, get_sandshore_runtime

# ---- Rate Limiter Tests ----


class TestRateLimiter:
    def _make_request(self, ip: str = "127.0.0.1", tenant_id: str | None = None) -> Request:
        """Create a mock Request with client IP and optional tenant_id."""
        req = MagicMock(spec=Request)
        req.client = MagicMock()
        req.client.host = ip
        req.state = MagicMock()
        if tenant_id:
            req.state.tenant_id = tenant_id
        else:
            req.state.tenant_id = None
        return req

    def test_allows_under_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        req = self._make_request()
        for _ in range(5):
            limiter.check(req)  # Should not raise

    def test_blocks_over_limit(self):
        from fastapi import HTTPException
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        req = self._make_request()
        for _ in range(3):
            limiter.check(req)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check(req)
        assert exc_info.value.status_code == 429

    def test_separate_keys_per_ip(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        req1 = self._make_request(ip="1.1.1.1")
        req2 = self._make_request(ip="2.2.2.2")
        for _ in range(2):
            limiter.check(req1)
            limiter.check(req2)
        # Both should be at limit but independent

    def test_tenant_key_takes_precedence(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        req = self._make_request(ip="1.1.1.1", tenant_id="tenant-abc")
        limiter.check(req)
        limiter.check(req)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            limiter.check(req)

    def test_window_expiry(self):
        limiter = RateLimiter(max_requests=1, window_seconds=0.1)
        req = self._make_request()
        limiter.check(req)
        time.sleep(0.15)  # Wait for window to expire
        limiter.check(req)  # Should not raise

    def test_info_property(self):
        limiter = RateLimiter(max_requests=10, window_seconds=60)
        info = limiter.info
        assert info["max_requests"] == 10
        assert info["window_seconds"] == 60
        assert info["active_keys"] == 0

    def test_execution_limiter_singleton(self):
        assert execution_limiter.max_requests == 10
        assert execution_limiter.window_seconds == 60.0


# ---- Sandshore Runtime Tests ----


class TestSandshoreRuntime:
    def test_init_defaults(self):
        rt = SandshoreRuntime(
            anthropic_api_key="ak", e2b_api_key="ek"
        )
        assert rt.template == ""
        assert rt._semaphore._value == 5

    def test_init_custom_params(self):
        rt = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            template="my-template",
            max_concurrent=10,
        )
        assert rt.template == "my-template"
        assert rt._semaphore._value == 10

    def test_pool_key_includes_template(self):
        # Clear pool for test isolation
        from sandcastle.engine.sandshore import _client_pool
        _client_pool.clear()

        rt1 = get_sandshore_runtime("ak", "ek", template="")
        rt2 = get_sandshore_runtime("ak", "ek", template="custom")
        assert rt1 is not rt2

        rt3 = get_sandshore_runtime("ak", "ek", template="custom")
        assert rt2 is rt3

        _client_pool.clear()

    @pytest.mark.asyncio
    async def test_cancel_event_propagation(self):
        """Verify that cancel_event can be used (basic interface test)."""
        rt = SandshoreRuntime(
            anthropic_api_key="ak", e2b_api_key=""
        )
        # No E2B key = can't use direct mode, no proxy = should raise
        cancel = asyncio.Event()
        cancel.set()
        from sandcastle.engine.sandshore import SandshoreError
        with pytest.raises(SandshoreError, match="No runtime configured"):
            await rt.query({"prompt": "test"}, cancel_event=cancel)


# ---- Config Tests ----


class TestPerformanceConfig:
    def test_default_e2b_template(self):
        from sandcastle.config import Settings
        s = Settings(
            anthropic_api_key="test",
            e2b_api_key="test",
        )
        assert s.e2b_template == ""
        assert s.max_concurrent_sandboxes == 5

    def test_custom_e2b_template(self):
        from sandcastle.config import Settings
        s = Settings(
            e2b_template="sandcastle-runner",
            max_concurrent_sandboxes=10,
        )
        assert s.e2b_template == "sandcastle-runner"
        assert s.max_concurrent_sandboxes == 10


# ---- Step Cache Tests ----


class TestStepCache:
    def test_compute_cache_key_deterministic(self):
        from sandcastle.engine.executor import _compute_cache_key
        key1 = _compute_cache_key("wf", "step1", "prompt text", "sonnet")
        key2 = _compute_cache_key("wf", "step1", "prompt text", "sonnet")
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex digest

    def test_compute_cache_key_different_inputs(self):
        from sandcastle.engine.executor import _compute_cache_key
        key1 = _compute_cache_key("wf", "step1", "prompt A", "sonnet")
        key2 = _compute_cache_key("wf", "step1", "prompt B", "sonnet")
        assert key1 != key2

    def test_compute_cache_key_different_models(self):
        from sandcastle.engine.executor import _compute_cache_key
        key1 = _compute_cache_key("wf", "step1", "prompt", "sonnet")
        key2 = _compute_cache_key("wf", "step1", "prompt", "opus")
        assert key1 != key2


# ---- Rate Limiter Integration Test ----


class TestRateLimitIntegration:
    """Test that rate limiter correctly blocks repeated requests."""

    def test_rate_limiter_blocks_after_limit(self):
        """Verify that the limiter raises 429 after exceeding max_requests."""
        from fastapi import HTTPException

        limiter = RateLimiter(max_requests=2, window_seconds=60)
        req = MagicMock(spec=Request)
        req.client = MagicMock()
        req.client.host = "10.0.0.1"
        req.state = MagicMock()
        req.state.tenant_id = "test-tenant"

        # First two requests pass
        limiter.check(req)
        limiter.check(req)

        # Third request gets 429
        with pytest.raises(HTTPException) as exc_info:
            limiter.check(req)
        assert exc_info.value.status_code == 429
        assert "Rate limit exceeded" in str(exc_info.value.detail)
        assert exc_info.value.headers["Retry-After"] == "60"
