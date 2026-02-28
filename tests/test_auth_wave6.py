"""Wave 6 deep audit tests: auth timing attacks, rate limit IP safety,
tenant filter hardening, and CORS origin restriction.
"""

from __future__ import annotations

import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_request(tenant_id=None, ip="127.0.0.1", path="/api/runs", auth_checked=True):
    """Create a mock request with configurable state."""
    req = MagicMock()
    req.state = MagicMock()
    req.state.tenant_id = tenant_id
    req.state._auth_checked = auth_checked
    req.client = MagicMock()
    req.client.host = ip
    req.url = MagicMock()
    req.url.path = path
    return req


def _mock_request_no_auth_checked(tenant_id=None, ip="127.0.0.1", path="/api/runs"):
    """Create a mock request where auth middleware did NOT run."""
    req = MagicMock()
    req.state = MagicMock(spec=[])  # no _auth_checked, no tenant_id
    req.client = MagicMock()
    req.client.host = ip
    req.url = MagicMock()
    req.url.path = path
    return req


@pytest.fixture(autouse=True)
def _restore_settings():
    """Save and restore settings modified by tests."""
    original = {
        "auth_required": settings.auth_required,
    }
    yield
    for key, value in original.items():
        setattr(settings, key, value)


# ---------------------------------------------------------------------------
# Bug #1: API key hash comparison should use constant-time compare
# ---------------------------------------------------------------------------


class TestConstantTimeKeyComparison:
    """Verify auth middleware uses hmac.compare_digest for hash comparison."""

    def test_hash_key_produces_hex_string(self):
        """hash_key returns a 64-char hex string (SHA-256)."""
        from sandcastle.api.auth import hash_key

        h = hash_key("sc_test_key_12345678")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_key_deterministic(self):
        """Same input produces the same hash."""
        from sandcastle.api.auth import hash_key

        h1 = hash_key("sc_test_key_abcdefgh")
        h2 = hash_key("sc_test_key_abcdefgh")
        assert h1 == h2

    def test_hash_key_uses_hmac_sha256(self):
        """hash_key uses HMAC-SHA256 with the pepper (not bare SHA-256)."""
        from sandcastle.api.auth import hash_key, _API_KEY_PEPPER

        import hashlib

        key = "sc_test_key_xyz"
        expected = hmac.new(
            _API_KEY_PEPPER.encode("utf-8"),
            key.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert hash_key(key) == expected

    @pytest.mark.asyncio
    async def test_auth_middleware_uses_compare_digest(self):
        """Auth middleware should use hmac.compare_digest, not == for hashes."""
        from sandcastle.api.auth import auth_middleware, hash_key

        settings.auth_required = True

        api_key = "sc_test_constant_time_key"
        key_hash = hash_key(api_key)
        key_prefix = api_key[:8]

        # Create a mock DB key
        mock_db_key = MagicMock()
        mock_db_key.key_hash = key_hash
        mock_db_key.key_prefix = key_prefix
        mock_db_key.is_active = True
        mock_db_key.expires_at = None
        mock_db_key.allowed_cidrs = None
        mock_db_key.tenant_id = "test-tenant"
        mock_db_key.id = "test-key-id"

        # Mock the request
        request = MagicMock()
        request.url.path = "/api/runs"
        request.headers = {"X-API-Key": api_key}
        request.state = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.query_params = {}

        call_next = AsyncMock(return_value=MagicMock())

        # Mock the DB session to return our candidate
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_db_key]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.get = AsyncMock(return_value=mock_db_key)

        with patch("sandcastle.api.auth.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            # Patch hmac.compare_digest to track its usage
            with patch("sandcastle.api.auth._hmac.compare_digest", wraps=hmac.compare_digest) as mock_compare:
                await auth_middleware(request, call_next)

                # Verify compare_digest was called (not ==)
                mock_compare.assert_called_once_with(key_hash, key_hash)

    @pytest.mark.asyncio
    async def test_auth_middleware_filters_by_key_prefix(self):
        """Auth middleware should query DB by key_prefix, not full hash."""
        from sandcastle.api.auth import auth_middleware

        settings.auth_required = True

        api_key = "sc_test_prefix_filter"

        request = MagicMock()
        request.url.path = "/api/runs"
        request.headers = {"X-API-Key": api_key}
        request.state = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.query_params = {}

        call_next = AsyncMock(return_value=MagicMock())

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("sandcastle.api.auth.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            response = await auth_middleware(request, call_next)

            # Verify the SQL query was executed
            mock_session.execute.assert_called_once()
            # The query should filter by key_prefix, not key_hash
            call_args = mock_session.execute.call_args
            stmt = call_args[0][0]
            # Compile the statement to check it queries by key_prefix
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
            assert "key_prefix" in compiled
            # Should NOT have key_hash in WHERE (it's checked via compare_digest)
            # Note: key_hash column may appear in SELECT, but NOT in WHERE
            where_clause = compiled.split("WHERE")[1] if "WHERE" in compiled else ""
            assert "key_hash =" not in where_clause or "key_hash" not in where_clause.split("AND")[0]

    @pytest.mark.asyncio
    async def test_auth_rejects_wrong_hash_even_with_same_prefix(self):
        """When prefix matches but hash doesn't, auth should reject."""
        from sandcastle.api.auth import auth_middleware, hash_key

        settings.auth_required = True

        api_key = "sc_test_wrong_hash_key"
        key_prefix = api_key[:8]

        # Create a candidate with the same prefix but different hash
        mock_db_key = MagicMock()
        mock_db_key.key_hash = "a" * 64  # wrong hash
        mock_db_key.key_prefix = key_prefix
        mock_db_key.is_active = True

        request = MagicMock()
        request.url.path = "/api/runs"
        request.headers = {"X-API-Key": api_key}
        request.state = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.query_params = {}

        call_next = AsyncMock()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_db_key]
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("sandcastle.api.auth.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            response = await auth_middleware(request, call_next)

            # Should return 401 because hash doesn't match
            assert response.status_code == 401
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_selects_correct_candidate_among_many(self):
        """When multiple keys share a prefix, the correct one is matched."""
        from sandcastle.api.auth import auth_middleware, hash_key

        settings.auth_required = True

        api_key = "sc_test_multi_candidate"
        key_hash = hash_key(api_key)
        key_prefix = api_key[:8]

        # Create multiple candidates with same prefix
        wrong_key = MagicMock()
        wrong_key.key_hash = "b" * 64
        wrong_key.key_prefix = key_prefix

        correct_key = MagicMock()
        correct_key.key_hash = key_hash
        correct_key.key_prefix = key_prefix
        correct_key.is_active = True
        correct_key.expires_at = None
        correct_key.allowed_cidrs = None
        correct_key.tenant_id = "correct-tenant"
        correct_key.id = "correct-id"

        request = MagicMock()
        request.url.path = "/api/runs"
        request.headers = {"X-API-Key": api_key}
        request.state = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.query_params = {}

        call_next = AsyncMock(return_value=MagicMock())

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [wrong_key, correct_key]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.get = AsyncMock(return_value=correct_key)

        with patch("sandcastle.api.auth.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await auth_middleware(request, call_next)

            # Verify the correct tenant was set
            assert request.state.tenant_id == "correct-tenant"
            call_next.assert_called_once()


# ---------------------------------------------------------------------------
# Bug #2: Rate limit key uses socket IP, not spoofable headers
# ---------------------------------------------------------------------------


class TestRateLimitIPSafety:
    """Verify rate limiter uses socket IP, not X-Forwarded-For."""

    def test_get_client_ip_uses_socket(self):
        """get_client_ip returns request.client.host (socket IP)."""
        from sandcastle.api.rate_limit import get_client_ip

        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "192.168.1.100"
        assert get_client_ip(req) == "192.168.1.100"

    def test_get_client_ip_returns_unknown_when_no_client(self):
        """get_client_ip returns 'unknown' when request.client is None."""
        from sandcastle.api.rate_limit import get_client_ip

        req = MagicMock()
        req.client = None
        assert get_client_ip(req) == "unknown"

    def test_rate_limiter_ignores_forwarded_for(self):
        """Rate limiter key should not change based on X-Forwarded-For header."""
        from sandcastle.api.rate_limit import RateLimiter

        rl = RateLimiter()

        # Two requests from the same socket IP but different X-Forwarded-For
        req1 = MagicMock()
        req1.state = MagicMock()
        req1.state.tenant_id = None
        req1.client = MagicMock()
        req1.client.host = "10.0.0.1"
        req1.headers = {"X-Forwarded-For": "1.2.3.4"}

        req2 = MagicMock()
        req2.state = MagicMock()
        req2.state.tenant_id = None
        req2.client = MagicMock()
        req2.client.host = "10.0.0.1"
        req2.headers = {"X-Forwarded-For": "5.6.7.8"}

        key1 = rl._get_key(req1)
        key2 = rl._get_key(req2)

        # Same socket IP should produce the same rate limit key
        assert key1 == key2
        assert key1 == "ip:10.0.0.1"

    def test_rate_limiter_uses_get_client_ip(self):
        """Rate limiter _get_key uses get_client_ip helper for IP extraction."""
        from sandcastle.api.rate_limit import RateLimiter

        rl = RateLimiter()

        req = MagicMock()
        req.state = MagicMock()
        req.state.tenant_id = None
        req.client = MagicMock()
        req.client.host = "172.16.0.42"

        with patch("sandcastle.api.rate_limit.get_client_ip", return_value="172.16.0.42") as mock_get_ip:
            key = rl._get_key(req)
            mock_get_ip.assert_called_once_with(req)
            assert key == "ip:172.16.0.42"

    @pytest.mark.asyncio
    async def test_spoofed_ip_does_not_bypass_rate_limit(self):
        """Requests from same socket IP should share rate limit bucket
        regardless of spoofed headers."""
        from sandcastle.api.rate_limit import RateLimiter

        rl = RateLimiter(max_requests=2, window_seconds=60.0)

        # All from the same socket IP, different "forwarded" headers
        for i in range(2):
            req = MagicMock()
            req.state = MagicMock()
            req.state.tenant_id = None
            req.client = MagicMock()
            req.client.host = "10.0.0.1"
            req.headers = {"X-Forwarded-For": f"192.168.{i}.1"}
            await rl.check(req)

        # Third request should be rate limited
        from fastapi import HTTPException

        req = MagicMock()
        req.state = MagicMock()
        req.state.tenant_id = None
        req.client = MagicMock()
        req.client.host = "10.0.0.1"
        req.headers = {"X-Forwarded-For": "192.168.99.1"}

        with pytest.raises(HTTPException) as exc:
            await rl.check(req)
        assert exc.value.status_code == 429

    def test_get_client_ip_ipv6(self):
        """get_client_ip works with IPv6 addresses."""
        from sandcastle.api.rate_limit import get_client_ip

        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "::1"
        assert get_client_ip(req) == "::1"

        req.client.host = "2001:db8::1"
        assert get_client_ip(req) == "2001:db8::1"


# ---------------------------------------------------------------------------
# Bug #3: _apply_tenant_filter and auth bypass detection
# ---------------------------------------------------------------------------


class TestTenantFilterSafety:
    """Verify tenant filter does not silently leak data on auth bypass."""

    def test_apply_tenant_filter_with_tenant(self):
        """When auth is required and tenant_id is set, filter is applied."""
        from sandcastle.api.routes import _apply_tenant_filter

        settings.auth_required = True
        stmt = MagicMock()
        _apply_tenant_filter(stmt, "acme-corp", MagicMock())
        stmt.where.assert_called_once()

    def test_apply_tenant_filter_admin_sees_all(self):
        """When auth is required and tenant_id is None (admin), no filter applied."""
        from sandcastle.api.routes import _apply_tenant_filter

        settings.auth_required = True
        stmt = MagicMock()
        result = _apply_tenant_filter(stmt, None, MagicMock())
        stmt.where.assert_not_called()
        assert result is stmt

    def test_apply_tenant_filter_auth_disabled(self):
        """When auth is not required, no filter applied."""
        from sandcastle.api.routes import _apply_tenant_filter

        settings.auth_required = False
        stmt = MagicMock()
        result = _apply_tenant_filter(stmt, None, MagicMock())
        stmt.where.assert_not_called()
        assert result is stmt

    def test_get_tenant_id_raises_when_auth_not_checked(self):
        """get_tenant_id raises RuntimeError when auth middleware didn't run."""
        from sandcastle.api.auth import get_tenant_id

        settings.auth_required = True
        req = _mock_request_no_auth_checked()

        with pytest.raises(RuntimeError, match="Auth middleware did not run"):
            get_tenant_id(req)

    def test_get_tenant_id_ok_when_auth_checked(self):
        """get_tenant_id works normally when auth middleware ran."""
        from sandcastle.api.auth import get_tenant_id

        settings.auth_required = True
        req = _mock_request(tenant_id="tenant-xyz", auth_checked=True)
        assert get_tenant_id(req) == "tenant-xyz"

    def test_get_tenant_id_ok_when_auth_disabled(self):
        """get_tenant_id works when auth is not required (even without _auth_checked)."""
        from sandcastle.api.auth import get_tenant_id

        settings.auth_required = False
        req = _mock_request_no_auth_checked()
        # Should NOT raise even without _auth_checked because auth is disabled
        result = get_tenant_id(req)
        assert result is None

    def test_get_tenant_id_admin_returns_none(self):
        """Admin keys (tenant_id=None) return None from get_tenant_id."""
        from sandcastle.api.auth import get_tenant_id

        settings.auth_required = True
        req = _mock_request(tenant_id=None, auth_checked=True)
        assert get_tenant_id(req) is None

    def test_auth_checked_set_on_public_paths(self):
        """Auth middleware sets _auth_checked even for public paths."""
        from sandcastle.api.auth import PUBLIC_PATHS

        # This is tested indirectly - the middleware sets _auth_checked
        # for all code paths. We verify the constant is as expected.
        assert "/api/health" in PUBLIC_PATHS
        assert "/api/docs" in PUBLIC_PATHS

    @pytest.mark.asyncio
    async def test_auth_middleware_sets_auth_checked_when_disabled(self):
        """When auth_required=False, middleware still sets _auth_checked=True."""
        from sandcastle.api.auth import auth_middleware

        settings.auth_required = False

        request = MagicMock()
        request.url.path = "/api/runs"
        request.state = MagicMock(spec=[])  # empty state
        request.headers = {}

        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        assert request.state._auth_checked is True
        assert request.state.tenant_id is None

    @pytest.mark.asyncio
    async def test_auth_middleware_sets_auth_checked_on_public_path(self):
        """Auth middleware sets _auth_checked for public API paths."""
        from sandcastle.api.auth import auth_middleware

        settings.auth_required = True

        request = MagicMock()
        request.url.path = "/api/health"
        request.state = MagicMock(spec=[])

        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        assert request.state._auth_checked is True

    @pytest.mark.asyncio
    async def test_auth_middleware_sets_auth_checked_on_non_api_path(self):
        """Auth middleware sets _auth_checked for non-API paths (dashboard)."""
        from sandcastle.api.auth import auth_middleware

        settings.auth_required = True

        request = MagicMock()
        request.url.path = "/dashboard"
        request.state = MagicMock(spec=[])

        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        assert request.state._auth_checked is True

    @pytest.mark.asyncio
    async def test_auth_middleware_sets_auth_checked_on_public_prefix(self):
        """Auth middleware sets _auth_checked for public prefix paths."""
        from sandcastle.api.auth import auth_middleware

        settings.auth_required = True

        request = MagicMock()
        request.url.path = "/api/templates/list"
        request.state = MagicMock(spec=[])

        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        assert request.state._auth_checked is True

    @pytest.mark.asyncio
    async def test_auth_middleware_sets_auth_checked_on_success(self):
        """Auth middleware sets _auth_checked when API key is valid."""
        from sandcastle.api.auth import auth_middleware, hash_key

        settings.auth_required = True

        api_key = "sc_test_auth_checked_key"
        key_hash = hash_key(api_key)

        mock_db_key = MagicMock()
        mock_db_key.key_hash = key_hash
        mock_db_key.key_prefix = api_key[:8]
        mock_db_key.expires_at = None
        mock_db_key.allowed_cidrs = None
        mock_db_key.tenant_id = "t1"
        mock_db_key.id = "k1"

        request = MagicMock()
        request.url.path = "/api/runs"
        request.headers = {"X-API-Key": api_key}
        request.state = MagicMock(spec=[])
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.query_params = {}

        call_next = AsyncMock(return_value=MagicMock())

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_db_key]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.get = AsyncMock(return_value=mock_db_key)

        with patch("sandcastle.api.auth.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await auth_middleware(request, call_next)

            assert request.state._auth_checked is True
            assert request.state.tenant_id == "t1"


# ---------------------------------------------------------------------------
# Bug #4: CORS origins should be restricted
# ---------------------------------------------------------------------------


class TestCORSRestriction:
    """Verify CORS origins are limited to necessary ports."""

    def test_cors_origins_limited(self):
        """CORS should not include more than 3 origins (dashboard + 2 Vite ports)."""
        from sandcastle.main import _cors_origins

        # Should contain at most: dashboard_origin, localhost:5173, localhost:5174
        localhost_origins = [o for o in _cors_origins if "localhost" in o]
        assert len(localhost_origins) <= 2, (
            f"Too many localhost CORS origins: {localhost_origins}. "
            f"Expected at most localhost:5173 and localhost:5174."
        )

    def test_cors_does_not_include_high_ports(self):
        """CORS should not include localhost ports 5175-5180."""
        from sandcastle.main import _cors_origins

        for port in range(5175, 5181):
            origin = f"http://localhost:{port}"
            assert origin not in _cors_origins, (
                f"CORS includes unnecessary origin {origin}"
            )

    def test_cors_includes_vite_default_ports(self):
        """CORS should include the standard Vite dev server ports."""
        from sandcastle.main import _cors_origins

        assert "http://localhost:5173" in _cors_origins
        assert "http://localhost:5174" in _cors_origins

    def test_cors_includes_dashboard_origin(self):
        """CORS should include the configured dashboard_origin."""
        from sandcastle.main import _cors_origins

        # dashboard_origin defaults to http://localhost:5173 which may
        # be deduplicated, but it should be present
        assert settings.dashboard_origin in _cors_origins or (
            settings.dashboard_origin == "*" and "*" not in _cors_origins
        )

    def test_cors_wildcard_filtered(self):
        """Wildcard origin should be filtered out (invalid with credentials)."""
        from sandcastle.main import _cors_origins

        assert "*" not in _cors_origins


# ---------------------------------------------------------------------------
# Integration: is_admin with auth bypass detection
# ---------------------------------------------------------------------------


class TestIsAdminWithAuthBypass:
    """Verify is_admin correctly handles auth bypass scenarios."""

    def test_is_admin_calls_get_tenant_id(self):
        """is_admin delegates to get_tenant_id which checks _auth_checked."""
        from sandcastle.api.auth import is_admin

        settings.auth_required = True
        req = _mock_request(tenant_id=None, auth_checked=True)
        # Admin when tenant_id is None and auth was checked
        assert is_admin(req) is True

    def test_is_admin_raises_on_unchecked_auth(self):
        """is_admin should raise if auth middleware didn't run."""
        from sandcastle.api.auth import is_admin

        settings.auth_required = True
        req = _mock_request_no_auth_checked()

        with pytest.raises(RuntimeError, match="Auth middleware did not run"):
            is_admin(req)

    def test_is_admin_always_true_when_auth_disabled(self):
        """When auth is disabled, everyone is admin (no _auth_checked needed)."""
        from sandcastle.api.auth import is_admin

        settings.auth_required = False
        req = _mock_request_no_auth_checked()
        assert is_admin(req) is True
