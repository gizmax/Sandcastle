"""Wave 8 security infrastructure audit tests.

Covers: security_headers.py, rate_limit.py, auth.py, main.py (CORS),
config.py (validation and safe_dump).
"""

from __future__ import annotations

import asyncio
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from sandcastle.api.rate_limit import (
    InMemoryBackend,
    RateLimiter,
    RedisBackend,
    _Window,
    get_client_ip,
)
from sandcastle.api.security_headers import (
    _CSP_POLICY,
    security_headers_middleware,
)
from sandcastle.config import Settings

from tests import _payloads as payloads


# =====================================================================
# Helpers
# =====================================================================


def _make_request(path: str = "/", is_local: bool = True) -> MagicMock:
    """Create a mock request with the given URL path."""
    request = MagicMock()
    request.url.path = path
    return request


def _make_response() -> MagicMock:
    """Create a mock response with dict-like headers."""
    response = MagicMock()
    response.headers = {}
    return response


def _mock_rate_limit_request(tenant_id=None, ip="127.0.0.1"):
    """Create a mock request for rate limiter tests."""
    req = MagicMock()
    req.state = MagicMock()
    req.state.tenant_id = tenant_id
    req.client = MagicMock()
    req.client.host = ip
    return req


# =====================================================================
# security_headers.py - CSP completeness
# =====================================================================


class TestCSPDirectiveCompleteness:
    """CSP policy must include all critical directives to prevent XSS."""

    def test_object_src_none(self):
        """object-src 'none' prevents Flash/plugin-based XSS."""
        assert "object-src 'none'" in _CSP_POLICY

    def test_base_uri_self(self):
        """base-uri 'self' prevents <base> tag injection for URL redirect."""
        assert "base-uri 'self'" in _CSP_POLICY

    def test_form_action_self(self):
        """form-action 'self' prevents form data exfiltration to external URLs."""
        assert "form-action 'self'" in _CSP_POLICY

    def test_frame_ancestors_none(self):
        """frame-ancestors 'none' prevents clickjacking via iframes."""
        assert "frame-ancestors 'none'" in _CSP_POLICY

    def test_default_src_self(self):
        """default-src 'self' is the baseline fallback."""
        assert "default-src 'self'" in _CSP_POLICY

    def test_no_unsafe_eval_in_script_src(self):
        """'unsafe-eval' must NEVER appear in script-src."""
        # Extract just the script-src directive
        for directive in _CSP_POLICY.split(";"):
            if "script-src" in directive:
                assert "unsafe-eval" not in directive

    def test_no_wildcard_in_connect_src(self):
        """connect-src must not use wildcards that allow data exfiltration."""
        for directive in _CSP_POLICY.split(";"):
            if "connect-src" in directive:
                # Allow 'self' and specific origins, but not bare *
                parts = directive.split()
                for part in parts:
                    if part == "*":
                        pytest.fail("connect-src contains wildcard *")


# =====================================================================
# security_headers.py - Case-insensitive path matching
# =====================================================================


class TestCSPPathBypass:
    """CSP routing must not be bypassable via case manipulation."""

    @pytest.mark.asyncio
    async def test_uppercase_api_no_csp(self):
        """Request to /API/... should NOT get CSP (it's an API path)."""
        request = _make_request("/API/workflows")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.csp_report_only = False
            mock_settings.is_local_mode = True
            result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" not in result.headers

    @pytest.mark.asyncio
    async def test_mixed_case_api_no_csp(self):
        """Request to /Api/workflows should NOT get CSP."""
        request = _make_request("/Api/workflows")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.csp_report_only = False
            mock_settings.is_local_mode = True
            result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" not in result.headers

    @pytest.mark.asyncio
    async def test_uppercase_api_gets_cache_control(self):
        """/API/ paths should still get Cache-Control: no-store."""
        request = _make_request("/API/runs")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = True
            result = await security_headers_middleware(request, call_next)

        assert result.headers.get("Cache-Control") == "no-store"

    @pytest.mark.asyncio
    async def test_dashboard_path_gets_csp(self):
        """Non-API path should get CSP."""
        request = _make_request("/workflows")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.csp_report_only = False
            mock_settings.is_local_mode = True
            result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" in result.headers


# =====================================================================
# security_headers.py - Additional security headers
# =====================================================================


class TestAdditionalSecurityHeaders:
    """Verify all required security headers are present."""

    @pytest.mark.asyncio
    async def test_x_permitted_cross_domain_policies(self):
        """X-Permitted-Cross-Domain-Policies should block Flash policies."""
        request = _make_request("/")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = True
            mock_settings.csp_report_only = False
            result = await security_headers_middleware(request, call_next)

        assert result.headers["X-Permitted-Cross-Domain-Policies"] == "none"

    @pytest.mark.asyncio
    async def test_hsts_in_production(self):
        """HSTS should be set when not in local mode."""
        request = _make_request("/")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = False
            mock_settings.csp_report_only = False
            result = await security_headers_middleware(request, call_next)

        assert "Strict-Transport-Security" in result.headers
        assert "max-age=31536000" in result.headers["Strict-Transport-Security"]

    @pytest.mark.asyncio
    async def test_no_hsts_in_local_mode(self):
        """HSTS should NOT be set in local mode (no TLS)."""
        request = _make_request("/")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = True
            mock_settings.csp_report_only = False
            result = await security_headers_middleware(request, call_next)

        assert "Strict-Transport-Security" not in result.headers

    @pytest.mark.asyncio
    async def test_api_cache_control_no_store(self):
        """API paths must have Cache-Control: no-store for sensitive data."""
        request = _make_request("/api/runs")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = True
            result = await security_headers_middleware(request, call_next)

        assert result.headers["Cache-Control"] == "no-store"
        assert result.headers["Pragma"] == "no-cache"

    @pytest.mark.asyncio
    async def test_dashboard_no_cache_control(self):
        """Dashboard paths should NOT have no-store Cache-Control."""
        request = _make_request("/workflows")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = True
            mock_settings.csp_report_only = False
            result = await security_headers_middleware(request, call_next)

        assert "Cache-Control" not in result.headers

    @pytest.mark.asyncio
    async def test_permissions_policy_all_paths(self):
        """Permissions-Policy should be on all paths."""
        for path in ["/", "/api/runs", "/workflows", "/api/health"]:
            request = _make_request(path)
            response = _make_response()
            call_next = AsyncMock(return_value=response)

            with patch("sandcastle.api.security_headers.settings") as mock_settings:
                mock_settings.is_local_mode = True
                mock_settings.csp_report_only = False
                result = await security_headers_middleware(request, call_next)

            assert "camera=()" in result.headers["Permissions-Policy"], f"Missing for {path}"
            assert "microphone=()" in result.headers["Permissions-Policy"], f"Missing for {path}"
            assert "geolocation=()" in result.headers["Permissions-Policy"], f"Missing for {path}"
            assert "payment=()" in result.headers["Permissions-Policy"], f"Missing for {path}"


# =====================================================================
# rate_limit.py - InMemoryBackend cleanup
# =====================================================================


class TestInMemoryBackendCleanup:
    """InMemoryBackend must clean up expired keys to prevent memory leaks."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_stale_keys(self):
        """Keys with all-expired timestamps should be removed on cleanup."""
        backend = InMemoryBackend()
        # Manually add a key with an expired timestamp
        backend._windows["old_key"] = _Window(
            timestamps=[time.monotonic() - 200.0]
        )
        backend._windows["fresh_key"] = _Window(
            timestamps=[time.monotonic()]
        )

        removed = backend._cleanup(60.0)
        assert removed == 1
        assert "old_key" not in backend._windows
        assert "fresh_key" in backend._windows

    @pytest.mark.asyncio
    async def test_cleanup_triggered_periodically(self):
        """Cleanup runs after _CLEANUP_INTERVAL calls."""
        backend = InMemoryBackend()
        backend._CLEANUP_INTERVAL = 5  # Low threshold for test

        # Add a stale key manually
        backend._windows["stale"] = _Window(
            timestamps=[time.monotonic() - 200.0]
        )

        # Do 5 increments to trigger cleanup
        for i in range(5):
            await backend.check_and_increment(f"key_{i}", 100, 60.0)

        # Stale key should have been cleaned up
        assert "stale" not in backend._windows

    @pytest.mark.asyncio
    async def test_cleanup_preserves_active_keys(self):
        """Active keys with valid timestamps should survive cleanup."""
        backend = InMemoryBackend()
        await backend.check_and_increment("active_key", 100, 60.0)

        removed = backend._cleanup(60.0)
        assert removed == 0
        assert "active_key" in backend._windows

    @pytest.mark.asyncio
    async def test_active_keys_count_after_cleanup(self):
        """active_keys property should reflect cleanup."""
        backend = InMemoryBackend()
        backend._windows["stale1"] = _Window(timestamps=[time.monotonic() - 200.0])
        backend._windows["stale2"] = _Window(timestamps=[time.monotonic() - 300.0])
        await backend.check_and_increment("active", 100, 60.0)

        assert backend.active_keys == 3
        backend._cleanup(60.0)
        assert backend.active_keys == 1


# =====================================================================
# rate_limit.py - Redis TTL calculation
# =====================================================================


class TestRedisTTLCalculation:
    """Redis TTL should use math.ceil to avoid truncation bugs."""

    @pytest.mark.asyncio
    async def test_fractional_window_ttl(self):
        """Fractional window_seconds should ceil to avoid premature TTL expiry."""
        backend = RedisBackend("redis://localhost:6379")

        # Mock Redis to capture the TTL argument
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=1)
        backend._redis = mock_redis

        await backend.check_and_increment("test", 10, 59.7)

        # redis.eval(script, 1, key, window_start, max_requests, now, member, ttl)
        # TTL is the last positional arg (index 7)
        call_args = mock_redis.eval.call_args
        ttl_arg = call_args[0][7]
        assert int(ttl_arg) == math.ceil(59.7) + 1  # Should be 61, not 60

    @pytest.mark.asyncio
    async def test_integer_window_ttl(self):
        """Integer window should produce TTL = window + 1."""
        backend = RedisBackend("redis://localhost:6379")
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=1)
        backend._redis = mock_redis

        await backend.check_and_increment("test", 10, 60.0)

        call_args = mock_redis.eval.call_args
        ttl_arg = call_args[0][7]
        assert int(ttl_arg) == 61


# =====================================================================
# rate_limit.py - Redis failure modes
# =====================================================================


class TestRedisFailureModes:
    """Redis backend must fail closed (deny) when Redis is unavailable."""

    @pytest.mark.asyncio
    async def test_fail_closed_on_connection_error(self):
        """When Redis connection fails, return count > max to fail closed."""
        backend = RedisBackend("redis://localhost:6379")
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))
        backend._redis = mock_redis

        result = await backend.check_and_increment("test", 10, 60.0)
        assert result > 10  # Should fail closed

    @pytest.mark.asyncio
    async def test_fail_closed_on_redis_none(self):
        """When Redis import fails, fail closed."""
        backend = RedisBackend("redis://localhost:6379")

        with patch.object(backend, "_get_redis", return_value=None):
            result = await backend.check_and_increment("test", 5, 60.0)
        assert result > 5

    @pytest.mark.asyncio
    async def test_fail_closed_raises_429(self):
        """When Redis is down, RateLimiter should raise 429."""
        backend = RedisBackend("redis://localhost:6379")
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))
        backend._redis = mock_redis

        rl = RateLimiter(max_requests=10, window_seconds=60.0, backend=backend)
        req = _mock_rate_limit_request()

        with pytest.raises(HTTPException) as exc:
            await rl.check(req)
        assert exc.value.status_code == 429


# =====================================================================
# rate_limit.py - get_client_ip robustness
# =====================================================================


class TestGetClientIP:
    """Client IP extraction must use socket address, not headers."""

    def test_returns_socket_ip(self):
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "10.0.0.1"
        assert get_client_ip(req) == "10.0.0.1"

    def test_returns_unknown_when_no_client(self):
        req = MagicMock()
        req.client = None
        assert get_client_ip(req) == "unknown"

    def test_ignores_xff_header(self):
        """X-Forwarded-For should NEVER be used for rate limiting."""
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "192.168.1.1"
        req.headers = {"X-Forwarded-For": "8.8.8.8"}
        # The function should return socket IP, not XFF
        assert get_client_ip(req) == "192.168.1.1"


# =====================================================================
# rate_limit.py - Sliding window edge cases
# =====================================================================


class TestSlidingWindowEdgeCases:
    """Edge cases in the sliding window counter."""

    def test_zero_window_seconds(self):
        """A zero-length window should count nothing (all expired)."""
        w = _Window()
        w.add()
        w.add()
        # With window_seconds=0, cutoff = now, so all timestamps <= cutoff
        assert w.count_in_window(0.0) == 0

    def test_negative_window_seconds(self):
        """Negative window should count nothing."""
        w = _Window()
        w.add()
        assert w.count_in_window(-1.0) == 0

    def test_very_large_window(self):
        """Very large window should include all timestamps."""
        w = _Window()
        w.timestamps = [time.monotonic() - 3600.0]  # 1 hour ago
        w.add()
        assert w.count_in_window(7200.0) == 2  # 2-hour window

    @pytest.mark.asyncio
    async def test_exact_limit_allows(self):
        """Exactly max_requests should be allowed."""
        backend = InMemoryBackend()
        for i in range(10):
            count = await backend.check_and_increment("k", 10, 60.0)

        # 10th request should be the last allowed (count=10)
        assert count == 10

    @pytest.mark.asyncio
    async def test_one_over_limit_denied(self):
        """max_requests + 1 should be denied."""
        backend = InMemoryBackend()
        for _ in range(10):
            await backend.check_and_increment("k", 10, 60.0)

        count = await backend.check_and_increment("k", 10, 60.0)
        assert count == 11  # One over limit


# =====================================================================
# rate_limit.py - Key isolation
# =====================================================================


class TestRateLimitKeyIsolation:
    """Tenant and IP rate limit keys must be fully isolated."""

    @pytest.mark.asyncio
    async def test_tenant_and_ip_different_namespaces(self):
        """A tenant named '127.0.0.1' should not collide with IP 127.0.0.1."""
        rl = RateLimiter(max_requests=1, window_seconds=60.0)

        req_tenant = _mock_rate_limit_request(tenant_id="127.0.0.1")
        req_ip = _mock_rate_limit_request(ip="127.0.0.1")

        await rl.check(req_tenant)
        # IP request should still be allowed (different namespace)
        await rl.check(req_ip)

    @pytest.mark.asyncio
    async def test_empty_tenant_uses_ip(self):
        """Empty string tenant_id should fall back to IP-based keying."""
        rl = RateLimiter(max_requests=100, window_seconds=60.0)
        req = _mock_rate_limit_request(tenant_id="", ip="10.0.0.5")
        key = rl._get_key(req)
        # Empty string is falsy, so should use IP
        assert key == "ip:10.0.0.5"


# =====================================================================
# auth.py - API key whitespace handling
# =====================================================================


class TestAuthKeyWhitespace:
    """API key extraction must handle whitespace correctly."""

    def test_hash_key_deterministic(self):
        """hash_key should produce consistent results."""
        from sandcastle.api.auth import hash_key

        h1 = hash_key("test-key")
        h2 = hash_key("test-key")
        assert h1 == h2

    def test_hash_key_different_inputs(self):
        """Different keys should produce different hashes."""
        from sandcastle.api.auth import hash_key

        assert hash_key("key-1") != hash_key("key-2")

    def test_generate_api_key_prefix(self):
        """Generated keys should have sc_ prefix."""
        from sandcastle.api.auth import generate_api_key

        key = generate_api_key()
        assert key.startswith("sc_")
        assert len(key) > 30

    def test_generate_api_key_uniqueness(self):
        """Each call should produce a unique key."""
        from sandcastle.api.auth import generate_api_key

        keys = {generate_api_key() for _ in range(50)}
        assert len(keys) == 50


class TestAuthPublicPaths:
    """Public paths and prefixes must be correctly configured."""

    def test_public_paths_include_health(self):
        from sandcastle.api.auth import PUBLIC_PATHS

        assert "/api/health" in PUBLIC_PATHS

    def test_public_paths_include_docs(self):
        from sandcastle.api.auth import PUBLIC_PATHS

        assert "/api/docs" in PUBLIC_PATHS
        assert "/api/redoc" in PUBLIC_PATHS
        assert "/api/openapi.json" in PUBLIC_PATHS

    def test_public_paths_include_a2a_discovery(self):
        from sandcastle.api.auth import PUBLIC_PATHS

        assert "/.well-known/agent.json" in PUBLIC_PATHS

    def test_a2a_rpc_not_public(self):
        """The /a2a JSON-RPC endpoint requires auth."""
        from sandcastle.api.auth import PUBLIC_PATHS

        assert "/a2a" not in PUBLIC_PATHS

    def test_public_prefixes_include_templates(self):
        from sandcastle.api.auth import PUBLIC_PREFIXES

        assert "/api/templates" in PUBLIC_PREFIXES


class TestGetTenantId:
    """get_tenant_id defense-in-depth checks."""

    def test_returns_none_when_auth_disabled(self):
        from sandcastle.api.auth import get_tenant_id

        req = MagicMock()
        req.state = MagicMock()
        req.state.tenant_id = None
        req.state._auth_checked = False

        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = False
            result = get_tenant_id(req)
        assert result is None

    def test_raises_when_auth_required_but_middleware_skipped(self):
        from sandcastle.api.auth import get_tenant_id

        req = MagicMock()
        req.state = MagicMock()
        req.state._auth_checked = False
        req.url.path = "/api/runs"

        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            with pytest.raises(RuntimeError, match="Auth middleware did not run"):
                get_tenant_id(req)

    def test_returns_tenant_when_auth_checked(self):
        from sandcastle.api.auth import get_tenant_id

        req = MagicMock()
        req.state = MagicMock()
        req.state.tenant_id = "tenant-123"
        req.state._auth_checked = True

        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            result = get_tenant_id(req)
        assert result == "tenant-123"


class TestIsAdmin:
    """is_admin must correctly identify admin vs tenant requests."""

    def test_admin_when_auth_disabled(self):
        from sandcastle.api.auth import is_admin

        req = MagicMock()
        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = False
            assert is_admin(req) is True

    def test_admin_when_no_tenant(self):
        from sandcastle.api.auth import is_admin

        req = MagicMock()
        req.state = MagicMock()
        req.state.tenant_id = None
        req.state._auth_checked = True

        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            assert is_admin(req) is True

    def test_not_admin_when_tenant_set(self):
        from sandcastle.api.auth import is_admin

        req = MagicMock()
        req.state = MagicMock()
        req.state.tenant_id = "some-tenant"
        req.state._auth_checked = True

        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            assert is_admin(req) is False


# =====================================================================
# auth.py - Middleware flow for disabled auth
# =====================================================================


class TestAuthMiddlewareDisabled:
    """When auth_required=False, middleware should pass through."""

    @pytest.mark.asyncio
    async def test_sets_tenant_none_and_auth_checked(self):
        from sandcastle.api.auth import auth_middleware

        req = MagicMock()
        req.state = MagicMock(spec=[])
        req.url.path = "/api/runs"

        call_next = AsyncMock(return_value=MagicMock())
        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = False
            await auth_middleware(req, call_next)

        assert req.state.tenant_id is None
        assert req.state._auth_checked is True
        call_next.assert_called_once()


# =====================================================================
# auth.py - Bearer token edge cases
# =====================================================================


class TestBearerTokenExtraction:
    """Bearer token parsing must handle edge cases."""

    @pytest.mark.asyncio
    async def test_bearer_with_extra_space_stripped(self):
        """'Bearer  key' (double space) should still extract the key."""
        from sandcastle.api.auth import auth_middleware

        headers_data = {
            "Authorization": "Bearer  sc_test_key_padded  ",
        }
        req = MagicMock()
        req.state = MagicMock(spec=[])
        req.url.path = "/api/runs"
        req.headers = MagicMock()
        req.headers.get = lambda k, d="": headers_data.get(k, d)
        req.query_params = MagicMock()
        req.query_params.get = MagicMock(return_value=None)

        call_next = AsyncMock(return_value=MagicMock())

        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            with patch("sandcastle.api.auth.async_session") as mock_session:
                # Simulate DB lookup failure for simplicity
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("DB unavailable"))
                mock_ctx.__aexit__ = AsyncMock()
                mock_session.return_value = mock_ctx

                result = await auth_middleware(req, call_next)

        # The key should have been extracted (even if DB lookup fails)
        # Result should be a 503 SERVICE_UNAVAILABLE, not 401 UNAUTHORIZED
        assert result.status_code == 503

    @pytest.mark.asyncio
    async def test_whitespace_only_key_rejected(self):
        """A whitespace-only API key should be rejected as unauthorized."""
        from sandcastle.api.auth import auth_middleware

        req = MagicMock()
        req.state = MagicMock(spec=[])
        req.url.path = "/api/runs"
        req.headers = {"X-API-Key": "   "}
        req.query_params = MagicMock()
        req.query_params.get = MagicMock(return_value=None)

        call_next = AsyncMock()

        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            result = await auth_middleware(req, call_next)

        assert result.status_code == 401
        call_next.assert_not_called()


# =====================================================================
# config.py - safe_dump redaction
# =====================================================================


class TestSettingsSafeDump:
    """Settings.safe_dump() must redact all sensitive fields."""

    def test_safe_dump_redacts_api_keys(self):
        s = Settings(
            anthropic_api_key="sk-ant-secret-123",
            e2b_api_key="e2b_secret_456",
        )
        dump = s.safe_dump()
        assert dump["anthropic_api_key"] == "***"
        assert dump["e2b_api_key"] == "***"

    def test_safe_dump_redacts_database_url(self):
        s = Settings(database_url="postgresql://user:pass@host/db")
        dump = s.safe_dump()
        assert dump["database_url"] == "***"

    def test_safe_dump_redacts_redis_url(self):
        s = Settings(redis_url="redis://secret@localhost:6379")
        dump = s.safe_dump()
        assert dump["redis_url"] == "***"

    def test_safe_dump_preserves_non_sensitive(self):
        s = Settings(sandbox_backend="docker", log_level="debug")
        dump = s.safe_dump()
        assert dump["sandbox_backend"] == "docker"
        assert dump["log_level"] == "debug"

    def test_safe_dump_skips_empty_sensitive(self):
        """Empty sensitive fields should remain empty, not become '***'."""
        s = Settings(anthropic_api_key="")
        dump = s.safe_dump()
        assert dump["anthropic_api_key"] == ""  # Empty stays empty

    def test_safe_dump_redacts_all_tool_credentials(self):
        s = Settings(
            tool_slack_bot_token="xoxb-test",
            tool_jira_api_token="jira-secret",
            tool_github_token="ghp_test123",
            tool_smtp_password="smtp-secret",
        )
        dump = s.safe_dump()
        assert dump["tool_slack_bot_token"] == "***"
        assert dump["tool_jira_api_token"] == "***"
        assert dump["tool_github_token"] == "***"
        assert dump["tool_smtp_password"] == "***"

    def test_safe_dump_redacts_license_key(self):
        s = Settings(license_key="sc_lic_test123")
        dump = s.safe_dump()
        assert dump["license_key"] == "***"

    def test_safe_dump_redacts_credential_encryption_key(self):
        s = Settings(credential_encryption_key="fernet-key-here")
        dump = s.safe_dump()
        assert dump["credential_encryption_key"] == "***"

    def test_model_dump_still_exposes_secrets(self):
        """model_dump() should still work normally (for internal use)."""
        s = Settings(anthropic_api_key="sk-test")
        dump = s.model_dump()
        assert dump["anthropic_api_key"] == "sk-test"


# =====================================================================
# config.py - Validator edge cases
# =====================================================================


class TestConfigValidators:
    """Config validators must handle all edge cases."""

    def test_sandbox_backend_unknown_falls_back(self):
        s = Settings(sandbox_backend="unknown_backend")
        assert s.sandbox_backend == "e2b"

    def test_sandbox_backend_case_insensitive(self):
        s = Settings(sandbox_backend="DOCKER")
        assert s.sandbox_backend == "docker"

    def test_sandbox_backend_strips_whitespace(self):
        s = Settings(sandbox_backend="  local  ")
        assert s.sandbox_backend == "local"

    def test_storage_backend_unknown_falls_back(self):
        s = Settings(storage_backend="postgres")
        assert s.storage_backend == "local"

    def test_memory_backend_unknown_falls_back(self):
        s = Settings(memory_backend="redis")
        assert s.memory_backend == "local"

    def test_log_level_unknown_falls_back(self):
        s = Settings(log_level="trace")
        assert s.log_level == "info"

    def test_log_level_case_insensitive(self):
        s = Settings(log_level="DEBUG")
        assert s.log_level == "debug"

    def test_max_concurrent_sandboxes_clamps_low(self):
        s = Settings(max_concurrent_sandboxes=0)
        assert s.max_concurrent_sandboxes == 1

    def test_max_concurrent_sandboxes_clamps_high(self):
        s = Settings(max_concurrent_sandboxes=100)
        assert s.max_concurrent_sandboxes == 50

    def test_max_concurrent_sandboxes_valid_range(self):
        s = Settings(max_concurrent_sandboxes=25)
        assert s.max_concurrent_sandboxes == 25

    def test_default_max_cost_negative(self):
        s = Settings(default_max_cost_usd=-5.0)
        assert s.default_max_cost_usd == 0.0

    def test_default_max_cost_zero_is_no_limit(self):
        s = Settings(default_max_cost_usd=0.0)
        assert s.default_max_cost_usd == 0.0

    def test_smtp_port_too_low(self):
        s = Settings(tool_smtp_port=0)
        assert s.tool_smtp_port == 587

    def test_smtp_port_too_high(self):
        s = Settings(tool_smtp_port=70000)
        assert s.tool_smtp_port == 587

    def test_smtp_port_valid(self):
        s = Settings(tool_smtp_port=465)
        assert s.tool_smtp_port == 465

    def test_memory_admit_threshold_clamps_low(self):
        s = Settings(memory_admit_threshold=-0.5)
        assert s.memory_admit_threshold == 0.0

    def test_memory_admit_threshold_clamps_high(self):
        s = Settings(memory_admit_threshold=1.5)
        assert s.memory_admit_threshold == 1.0

    def test_memory_max_age_days_negative(self):
        s = Settings(memory_max_age_days=-10)
        assert s.memory_max_age_days == 0

    def test_failover_cooldown_zero(self):
        s = Settings(failover_cooldown_seconds=0)
        assert s.failover_cooldown_seconds == 60.0

    def test_failover_cooldown_negative(self):
        s = Settings(failover_cooldown_seconds=-5.0)
        assert s.failover_cooldown_seconds == 60.0

    def test_docker_pids_limit_zero(self):
        s = Settings(docker_pids_limit=0)
        assert s.docker_pids_limit == 100

    def test_docker_cpu_period_too_low(self):
        s = Settings(docker_cpu_period=500)
        assert s.docker_cpu_period == 100_000

    def test_docker_cpu_quota_too_low(self):
        s = Settings(docker_cpu_quota=500)
        assert s.docker_cpu_quota == 50_000

    def test_key_rotation_grace_negative(self):
        s = Settings(key_rotation_grace_hours=-1)
        assert s.key_rotation_grace_hours == 24

    def test_max_workflow_depth_zero(self):
        s = Settings(max_workflow_depth=0)
        assert s.max_workflow_depth == 5

    def test_max_workflow_depth_negative(self):
        s = Settings(max_workflow_depth=-3)
        assert s.max_workflow_depth == 5

    def test_max_workflow_depth_exceeds_max(self):
        """Workflow depth > 20 should be clamped to prevent stack overflow."""
        s = Settings(max_workflow_depth=100)
        assert s.max_workflow_depth == 20

    def test_max_workflow_depth_at_upper_bound(self):
        s = Settings(max_workflow_depth=20)
        assert s.max_workflow_depth == 20

    def test_max_workflow_depth_valid(self):
        s = Settings(max_workflow_depth=10)
        assert s.max_workflow_depth == 10


# =====================================================================
# config.py - is_local_mode detection
# =====================================================================


class TestIsLocalMode:
    """is_local_mode must correctly detect local vs production."""

    def test_empty_database_url_is_local(self):
        s = Settings(database_url="")
        assert s.is_local_mode is True

    def test_sqlite_url_is_local(self):
        s = Settings(database_url="sqlite:///test.db")
        assert s.is_local_mode is True

    def test_sqlite_async_url_is_local(self):
        s = Settings(database_url="sqlite+aiosqlite:///test.db")
        assert s.is_local_mode is True

    def test_postgres_url_is_production(self):
        s = Settings(database_url="postgresql+asyncpg://user:pass@host/db")
        assert s.is_local_mode is False


# =====================================================================
# config.py - Path expansion
# =====================================================================


class TestPathExpansion:
    """data_dir and workflows_dir should expand ~ to home directory."""

    def test_data_dir_expands_tilde(self):
        s = Settings(data_dir="~/mydata")
        assert "~" not in s.data_dir
        assert s.data_dir.endswith("/mydata") or s.data_dir.endswith("\\mydata")

    def test_workflows_dir_expands_tilde(self):
        s = Settings(workflows_dir="~/workflows")
        assert "~" not in s.workflows_dir


# =====================================================================
# main.py - CORS configuration
# =====================================================================


class TestCORSConfiguration:
    """CORS configuration must not allow wildcard with credentials."""

    def test_wildcard_origin_filtered_out(self):
        """When dashboard_origin='*', it should be removed from origins list."""
        origins = ["*", "http://localhost:5173", "http://localhost:5174"]
        filtered = list(dict.fromkeys(o for o in origins if o != "*"))
        assert "*" not in filtered
        assert "http://localhost:5173" in filtered
        assert "http://localhost:5174" in filtered

    def test_duplicate_origins_deduped(self):
        """When dashboard_origin matches a Vite port, it should be deduped."""
        origins = [
            "http://localhost:5173",
            "http://localhost:5173",
            "http://localhost:5174",
        ]
        deduped = list(dict.fromkeys(o for o in origins if o != "*"))
        assert len(deduped) == 2

    def test_custom_origin_preserved(self):
        """A custom dashboard origin should be preserved."""
        origins = [
            "https://app.example.com",
            "http://localhost:5173",
            "http://localhost:5174",
        ]
        filtered = list(dict.fromkeys(o for o in origins if o != "*"))
        assert "https://app.example.com" in filtered
        assert len(filtered) == 3


# =====================================================================
# main.py - SPA fallback path traversal
# =====================================================================


class TestSPAFallbackSafety:
    """SPA fallback must not serve files outside the dashboard directory."""

    def test_resolve_prevents_dot_dot_traversal(self):
        """Path with .. should not escape the dashboard directory."""
        from pathlib import Path

        dashboard_dir = Path("/app/dashboard/dist")
        path = payloads.PATH_TRAVERSAL
        file = (dashboard_dir / path).resolve()
        assert not file.is_relative_to(dashboard_dir)

    def test_resolve_allows_valid_subpath(self):
        """Valid subpath should pass the relative_to check."""
        from pathlib import Path

        dashboard_dir = Path("/app/dashboard/dist")
        path = "assets/index.js"
        file = (dashboard_dir / path).resolve()
        assert file.is_relative_to(dashboard_dir)

    def test_api_paths_not_intercepted(self):
        """Paths starting with 'api/' should not be intercepted by SPA."""
        # The SPA fallback checks: if path.startswith("api/") or path == "api"
        assert "api/runs".startswith("api/")
        assert not "apiary".startswith("api/")  # Only exact prefix

    def test_wellknown_paths_not_intercepted(self):
        """A2A discovery paths should not be intercepted by SPA."""
        assert ".well-known/agent.json".startswith(".well-known/")


# =====================================================================
# Integration: Rate limiter with realistic request flow
# =====================================================================


class TestRateLimiterIntegration:
    """End-to-end rate limiting scenarios."""

    @pytest.mark.asyncio
    async def test_burst_then_wait_and_retry(self):
        """After exceeding limit, new window should allow requests again."""
        backend = InMemoryBackend()
        rl = RateLimiter(max_requests=2, window_seconds=0.1, backend=backend)

        req = _mock_rate_limit_request(ip="10.0.0.1")

        # Exhaust the limit
        await rl.check(req)
        await rl.check(req)

        with pytest.raises(HTTPException) as exc:
            await rl.check(req)
        assert exc.value.status_code == 429

        # Wait for the window to expire
        await asyncio.sleep(0.15)

        # Should succeed again
        await rl.check(req)

    @pytest.mark.asyncio
    async def test_concurrent_tenants_independent(self):
        """Multiple tenants hitting the limiter concurrently."""
        rl = RateLimiter(max_requests=3, window_seconds=60.0)

        reqs = [_mock_rate_limit_request(tenant_id=f"t-{i}") for i in range(10)]

        # Each tenant makes 3 requests
        for req in reqs:
            for _ in range(3):
                await rl.check(req)

        # 4th request for each should fail
        for req in reqs:
            with pytest.raises(HTTPException):
                await rl.check(req)

    @pytest.mark.asyncio
    async def test_429_response_headers(self):
        """429 response must include proper rate limit headers."""
        rl = RateLimiter(max_requests=1, window_seconds=45.0)
        req = _mock_rate_limit_request()

        await rl.check(req)

        with pytest.raises(HTTPException) as exc:
            await rl.check(req)

        assert exc.value.headers["Retry-After"] == "45"
        assert exc.value.headers["X-RateLimit-Limit"] == "1"
        assert exc.value.headers["X-RateLimit-Remaining"] == "0"

    @pytest.mark.asyncio
    async def test_info_with_redis_backend(self):
        """Info dict should show Redis backend name."""
        backend = RedisBackend("redis://localhost")
        rl = RateLimiter(max_requests=10, backend=backend)
        info = rl.info
        assert info["backend"] == "RedisBackend"
        # Redis backend should not have active_keys in info
        assert "active_keys" not in info


# =====================================================================
# Sensitive fields coverage
# =====================================================================


class TestSensitiveFieldsCoverage:
    """Verify all credential fields are listed in _SENSITIVE_FIELDS."""

    def test_all_api_key_fields_sensitive(self):
        """Every field ending with _api_key or _token should be sensitive."""
        s = Settings()
        dump = s.model_dump()
        sensitive = s._SENSITIVE_FIELDS

        for key in dump:
            if any(
                key.endswith(suffix)
                for suffix in ("_api_key", "_token", "_secret", "_password")
            ):
                assert key in sensitive, f"{key} should be in _SENSITIVE_FIELDS"

    def test_database_urls_sensitive(self):
        s = Settings()
        assert "database_url" in s._SENSITIVE_FIELDS
        assert "redis_url" in s._SENSITIVE_FIELDS
        assert "tool_postgresql_url" in s._SENSITIVE_FIELDS

    def test_license_key_sensitive(self):
        s = Settings()
        assert "license_key" in s._SENSITIVE_FIELDS
