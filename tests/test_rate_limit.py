"""Tests for RateLimiter and sliding window counter."""

import time
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from sandcastle.api.rate_limit import RateLimiter, _Window


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
# RateLimiter
# ------------------------------------------------------------------


def _mock_request(tenant_id=None, ip="127.0.0.1"):
    req = MagicMock()
    req.state = MagicMock()
    req.state.tenant_id = tenant_id
    req.client = MagicMock()
    req.client.host = ip
    return req


class TestRateLimiter:

    def test_allows_under_limit(self):
        rl = RateLimiter(max_requests=5, window_seconds=60.0)
        req = _mock_request()
        for _ in range(5):
            rl.check(req)

    def test_rejects_over_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60.0)
        req = _mock_request()
        rl.check(req)
        rl.check(req)
        rl.check(req)
        with pytest.raises(HTTPException) as exc:
            rl.check(req)
        assert exc.value.status_code == 429
        assert "Rate limit exceeded" in exc.value.detail

    def test_different_tenants_independent(self):
        rl = RateLimiter(max_requests=2, window_seconds=60.0)
        req1 = _mock_request(tenant_id="tenant-a")
        req2 = _mock_request(tenant_id="tenant-b")
        rl.check(req1)
        rl.check(req1)
        # tenant-a is now at limit, but tenant-b should be fine
        rl.check(req2)
        with pytest.raises(HTTPException):
            rl.check(req1)

    def test_different_ips_independent(self):
        rl = RateLimiter(max_requests=1, window_seconds=60.0)
        req1 = _mock_request(ip="10.0.0.1")
        req2 = _mock_request(ip="10.0.0.2")
        rl.check(req1)
        rl.check(req2)
        with pytest.raises(HTTPException):
            rl.check(req1)

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
        assert info["active_keys"] == 0

    def test_info_tracks_active_keys(self):
        rl = RateLimiter(max_requests=100, window_seconds=60.0)
        req1 = _mock_request(ip="1.1.1.1")
        req2 = _mock_request(ip="2.2.2.2")
        rl.check(req1)
        rl.check(req2)
        assert rl.info["active_keys"] == 2

    def test_429_headers(self):
        rl = RateLimiter(max_requests=1, window_seconds=30.0)
        req = _mock_request()
        rl.check(req)
        with pytest.raises(HTTPException) as exc:
            rl.check(req)
        assert exc.value.headers["Retry-After"] == "30"
        assert exc.value.headers["X-RateLimit-Limit"] == "1"
        assert exc.value.headers["X-RateLimit-Remaining"] == "0"
