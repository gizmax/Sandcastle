"""Tests for RateLimiter, sliding window counter, and backend selection."""

import time
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from sandcastle.api.rate_limit import (
    InMemoryBackend,
    RateLimiter,
    RedisBackend,
    _Window,
)

# ------------------------------------------------------------------
# _Window
# ------------------------------------------------------------------


class TestWindow:

    def test_empty_window(self):
        w = _Window()
        assert w.count_in_window(60.0) == 0

    def test_add_increments(self):
        w = _Window()
        w.add()
        w.add()
        assert w.count_in_window(60.0) == 2

    def test_expired_entries_pruned(self):
        w = _Window()
        w.timestamps = [time.monotonic() - 120.0]  # 2 min ago
        w.add()  # now
        assert w.count_in_window(60.0) == 1


# ------------------------------------------------------------------
# InMemoryBackend
# ------------------------------------------------------------------


class TestInMemoryBackend:

    @pytest.mark.asyncio
    async def test_allows_under_limit(self):
        backend = InMemoryBackend()
        for _ in range(5):
            count = await backend.check_and_increment("key1", 5, 60.0)
        assert count <= 5

    @pytest.mark.asyncio
    async def test_exceeds_limit(self):
        backend = InMemoryBackend()
        for _ in range(5):
            await backend.check_and_increment("key1", 5, 60.0)
        # 6th request should return count > max_requests
        count = await backend.check_and_increment("key1", 5, 60.0)
        assert count > 5  # Returns 6 (attempted request number)

    @pytest.mark.asyncio
    async def test_active_keys_property(self):
        backend = InMemoryBackend()
        await backend.check_and_increment("key1", 10, 60.0)
        await backend.check_and_increment("key2", 10, 60.0)
        assert backend.active_keys == 2


# ------------------------------------------------------------------
# RateLimiter (async)
# ------------------------------------------------------------------


def _mock_request(tenant_id=None, ip="127.0.0.1"):
    req = MagicMock()
    req.state = MagicMock()
    req.state.tenant_id = tenant_id
    req.client = MagicMock()
    req.client.host = ip
    return req


class TestRateLimiter:

    @pytest.mark.asyncio
    async def test_allows_under_limit(self):
        rl = RateLimiter(max_requests=5, window_seconds=60.0)
        req = _mock_request()
        for _ in range(5):
            await rl.check(req)

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60.0)
        req = _mock_request()
        await rl.check(req)
        await rl.check(req)
        await rl.check(req)
        with pytest.raises(HTTPException) as exc:
            await rl.check(req)
        assert exc.value.status_code == 429
        assert "Rate limit exceeded" in exc.value.detail

    @pytest.mark.asyncio
    async def test_different_tenants_independent(self):
        rl = RateLimiter(max_requests=2, window_seconds=60.0)
        req1 = _mock_request(tenant_id="tenant-a")
        req2 = _mock_request(tenant_id="tenant-b")
        await rl.check(req1)
        await rl.check(req1)
        # tenant-a is now at limit, but tenant-b should be fine
        await rl.check(req2)
        with pytest.raises(HTTPException):
            await rl.check(req1)

    @pytest.mark.asyncio
    async def test_different_ips_independent(self):
        rl = RateLimiter(max_requests=1, window_seconds=60.0)
        req1 = _mock_request(ip="10.0.0.1")
        req2 = _mock_request(ip="10.0.0.2")
        await rl.check(req1)
        await rl.check(req2)
        with pytest.raises(HTTPException):
            await rl.check(req1)

    def test_tenant_key_over_ip(self):
        rl = RateLimiter(max_requests=1, window_seconds=60.0)
        req = _mock_request(tenant_id="t1", ip="192.168.1.1")
        key = rl._get_key(req)
        assert key == "tenant:t1"

    def test_ip_key_when_no_tenant(self):
        rl = RateLimiter(max_requests=1, window_seconds=60.0)
        req = _mock_request(tenant_id=None, ip="192.168.1.1")
        key = rl._get_key(req)
        assert key == "ip:192.168.1.1"

    def test_unknown_client_key(self):
        req = MagicMock()
        req.state = MagicMock()
        req.state.tenant_id = None
        req.client = None
        rl = RateLimiter()
        key = rl._get_key(req)
        assert key == "ip:unknown"

    def test_info_property(self):
        rl = RateLimiter(max_requests=10, window_seconds=30.0)
        info = rl.info
        assert info["max_requests"] == 10
        assert info["window_seconds"] == 30.0
        assert info["backend"] == "InMemoryBackend"

    @pytest.mark.asyncio
    async def test_info_tracks_active_keys(self):
        rl = RateLimiter(max_requests=100, window_seconds=60.0)
        req1 = _mock_request(ip="1.1.1.1")
        req2 = _mock_request(ip="2.2.2.2")
        await rl.check(req1)
        await rl.check(req2)
        assert rl.info["active_keys"] == 2

    @pytest.mark.asyncio
    async def test_429_headers(self):
        rl = RateLimiter(max_requests=1, window_seconds=30.0)
        req = _mock_request()
        await rl.check(req)
        with pytest.raises(HTTPException) as exc:
            await rl.check(req)
        assert exc.value.headers["Retry-After"] == "30"
        assert exc.value.headers["X-RateLimit-Limit"] == "1"
        assert exc.value.headers["X-RateLimit-Remaining"] == "0"


# ------------------------------------------------------------------
# Backend selection
# ------------------------------------------------------------------


class TestBackendSelection:

    def test_in_memory_when_no_redis(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.redis_url", "")
        from sandcastle.api.rate_limit import _create_backend

        backend = _create_backend()
        assert isinstance(backend, InMemoryBackend)

    def test_redis_when_redis_url_set(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.redis_url", "redis://localhost:6379")
        from sandcastle.api.rate_limit import _create_backend

        backend = _create_backend()
        assert isinstance(backend, RedisBackend)
