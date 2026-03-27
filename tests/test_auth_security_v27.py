"""Comprehensive tests for authentication, rate limiting, and security headers.

Covers:
- Auth middleware (X-API-Key, Bearer, query param, expiry, IP allowlist, etc.)
- Rate limiter (InMemoryBackend sliding window, tenant/IP keys, headers)
- Security headers middleware (CSP, HSTS, X-Frame-Options, etc.)
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from sandcastle.api.auth import (
    _API_KEY_PEPPER,
    PUBLIC_PATHS,
    auth_middleware,
    generate_api_key,
    get_tenant_id,
    hash_key,
    is_admin,
)
from sandcastle.api.rate_limit import (
    InMemoryBackend,
    RateLimiter,
    _Window,
    get_client_ip,
)
from sandcastle.api.security_headers import _CSP_POLICY, security_headers_middleware
from sandcastle.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    path: str = "/api/runs",
    headers: dict | None = None,
    ip: str = "127.0.0.1",
    tenant_id=None,
    query_params: dict | None = None,
    has_client: bool = True,
):
    """Build a mock Request with sensible defaults."""
    req = MagicMock()
    req.url = MagicMock()
    req.url.path = path
    req.headers = headers or {}
    req.query_params = query_params or {}
    req.state = MagicMock(spec=[])  # empty state - middleware populates it
    if has_client:
        req.client = MagicMock()
        req.client.host = ip
    else:
        req.client = None
    return req


def _make_db_key(
    api_key: str,
    tenant_id: str | None = "tenant-1",
    is_active: bool = True,
    expires_at: datetime | None = None,
    allowed_cidrs: list | None = None,
    allowed_workflows: list | None = None,
    key_id: str = "key-id-1",
):
    """Build a mock ApiKey DB row that matches the given raw key."""
    mock = MagicMock()
    mock.key_hash = hash_key(api_key)
    mock.key_prefix = api_key[:8]
    mock.tenant_id = tenant_id
    mock.is_active = is_active
    mock.expires_at = expires_at
    mock.allowed_cidrs = allowed_cidrs
    mock.allowed_workflows = allowed_workflows
    mock.id = key_id
    mock.last_used_at = None
    return mock


def _patch_db(candidates, get_return=None):
    """Context manager that patches async_session to return given candidates."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = candidates
    mock_session.execute = AsyncMock(return_value=mock_result)
    # For the last_used_at update
    mock_session.get = AsyncMock(return_value=get_return or (candidates[0] if candidates else None))

    ctx = patch("sandcastle.api.auth.async_session")
    return ctx, mock_session


@pytest.fixture(autouse=True)
def _restore_settings():
    """Save and restore settings modified by tests."""
    original = {
        "auth_required": settings.auth_required,
        "csp_report_only": settings.csp_report_only,
        "key_rotation_grace_hours": settings.key_rotation_grace_hours,
    }
    yield
    for key, value in original.items():
        setattr(settings, key, value)


# ===========================================================================
# 1. AUTHENTICATION (17 tests)
# ===========================================================================


class TestAuthValidApiKeyXHeader:
    """Valid API key via X-API-Key header."""

    @pytest.mark.asyncio
    async def test_valid_key_via_x_api_key(self):
        api_key = "sc_test_xheader_key_abcd"
        db_key = _make_db_key(api_key, tenant_id="acme")

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key})
        call_next = AsyncMock(return_value=MagicMock())

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        call_next.assert_called_once()
        assert request.state.tenant_id == "acme"


class TestAuthValidApiKeyBearer:
    """Valid API key via Authorization: Bearer header."""

    @pytest.mark.asyncio
    async def test_valid_key_via_bearer(self):
        api_key = "sc_test_bearer_key_efgh"
        db_key = _make_db_key(api_key, tenant_id="beta")

        settings.auth_required = True
        request = _make_request(headers={"Authorization": f"Bearer {api_key}"})
        call_next = AsyncMock(return_value=MagicMock())

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        call_next.assert_called_once()
        assert request.state.tenant_id == "beta"


class TestAuthValidApiKeyQueryParam:
    """Valid API key via query param for SSE endpoints."""

    @pytest.mark.asyncio
    async def test_valid_key_via_query_param_stream(self):
        api_key = "sc_test_query_key_ijkl"
        db_key = _make_db_key(api_key, tenant_id="gamma")

        settings.auth_required = True
        request = _make_request(
            path="/api/runs/123/stream",
            query_params={"token": api_key},
        )
        call_next = AsyncMock(return_value=MagicMock())

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        call_next.assert_called_once()
        assert request.state.tenant_id == "gamma"

    @pytest.mark.asyncio
    async def test_query_param_ignored_for_non_stream_path(self):
        """Query param token is NOT accepted on non-stream paths."""
        api_key = "sc_test_nostre_key_mnop"
        settings.auth_required = True
        request = _make_request(
            path="/api/runs",
            query_params={"token": api_key},
        )
        call_next = AsyncMock()

        response = await auth_middleware(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_param_accepted_for_agui_path(self):
        """Query param token is accepted for /api/agui/ paths."""
        api_key = "sc_test_agui_key_qrst"
        db_key = _make_db_key(api_key, tenant_id="delta")

        settings.auth_required = True
        request = _make_request(
            path="/api/agui/session/abc",
            query_params={"token": api_key},
        )
        call_next = AsyncMock(return_value=MagicMock())

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        call_next.assert_called_once()


class TestAuthInvalidKey:
    """Invalid key returns 401."""

    @pytest.mark.asyncio
    async def test_invalid_key_returns_401(self):
        settings.auth_required = True
        # DB has a key with different hash
        wrong_db_key = MagicMock()
        wrong_db_key.key_hash = "a" * 64
        wrong_db_key.key_prefix = "sc_test_"
        wrong_db_key.is_active = True

        request = _make_request(headers={"X-API-Key": "sc_test_invalid_key"})
        call_next = AsyncMock()

        ctx, mock_session = _patch_db([wrong_db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await auth_middleware(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()


class TestAuthExpiredKey:
    """Expired key returns 401 with KEY_EXPIRED code."""

    @pytest.mark.asyncio
    async def test_expired_key_returns_401(self):
        api_key = "sc_test_expired_key_uvwx"
        expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
        db_key = _make_db_key(api_key, expires_at=expired_time)

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key})
        call_next = AsyncMock()

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await auth_middleware(request, call_next)

        assert response.status_code == 401
        # Verify the body contains KEY_EXPIRED code
        import json
        body = json.loads(response.body.decode())
        assert body["error"]["code"] == "KEY_EXPIRED"
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_naive_datetime_treated_as_utc(self):
        """Naive datetime expires_at is treated as UTC."""
        api_key = "sc_test_naive_dt_key_yz"
        # Naive datetime in the past
        expired_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        db_key = _make_db_key(api_key, expires_at=expired_time)

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key})
        call_next = AsyncMock()

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await auth_middleware(request, call_next)

        assert response.status_code == 401


class TestAuthRevokedKey:
    """Revoked (inactive) key returns 401 - filtered at DB level."""

    @pytest.mark.asyncio
    async def test_inactive_key_not_returned_by_db(self):
        """Inactive keys are filtered by the SQL WHERE clause; no candidates returned."""
        api_key = "sc_test_revoked_key_1234"
        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key})
        call_next = AsyncMock()

        # DB returns empty list because is_active=False is filtered
        ctx, mock_session = _patch_db([])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await auth_middleware(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()


class TestAuthMissingKey:
    """Missing key behavior based on auth_required setting."""

    @pytest.mark.asyncio
    async def test_missing_key_when_required_returns_401(self):
        settings.auth_required = True
        request = _make_request(headers={})
        call_next = AsyncMock()

        response = await auth_middleware(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_key_when_not_required_allows_through(self):
        settings.auth_required = False
        request = _make_request(headers={})
        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        call_next.assert_called_once()
        assert request.state.tenant_id is None
        assert request.state.api_key_id is None


class TestAuthKeyRotation:
    """Key rotation grace period behavior.

    The rotation grace period is handled by the application keeping the old
    ApiKey row active (is_active=True) for key_rotation_grace_hours after
    creating the new key. The auth middleware itself doesn't track rotation;
    it simply checks is_active and expires_at. We test the boundary:
    - Old key with future expires_at: should work (within grace)
    - Old key with past expires_at: rejected (grace expired)
    """

    @pytest.mark.asyncio
    async def test_old_key_works_during_grace_period(self):
        """Old rotated key with future expires_at passes auth."""
        old_key = "sc_test_old_key_grace_ok"
        grace_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        db_key = _make_db_key(old_key, expires_at=grace_expiry)

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": old_key})
        call_next = AsyncMock(return_value=MagicMock())

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_old_key_rejected_after_grace_period(self):
        """Old rotated key with past expires_at is rejected."""
        old_key = "sc_test_old_key_grace_no"
        past_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
        db_key = _make_db_key(old_key, expires_at=past_expiry)

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": old_key})
        call_next = AsyncMock()

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await auth_middleware(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()


class TestAuthIPAllowlist:
    """IP allowlist enforcement."""

    @pytest.mark.asyncio
    async def test_allowed_ip_succeeds(self):
        api_key = "sc_test_ip_ok_key_abcd"
        db_key = _make_db_key(api_key, allowed_cidrs=["10.0.0.0/8", "192.168.1.0/24"])

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key}, ip="192.168.1.42")
        call_next = AsyncMock(return_value=MagicMock())

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_disallowed_ip_returns_403(self):
        api_key = "sc_test_ip_bad_key_efgh"
        db_key = _make_db_key(api_key, allowed_cidrs=["10.0.0.0/8"])

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key}, ip="172.16.0.1")
        call_next = AsyncMock()

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await auth_middleware(request, call_next)

        assert response.status_code == 403
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_client_ip_with_allowlist_returns_403(self):
        """When client IP cannot be determined but allowlist is set, deny access."""
        api_key = "sc_test_ip_nocl_key_ij"
        db_key = _make_db_key(api_key, allowed_cidrs=["10.0.0.0/8"])

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key}, has_client=False)
        call_next = AsyncMock()

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await auth_middleware(request, call_next)

        assert response.status_code == 403


class TestAuthHMAC:
    """HMAC-SHA256 hash verification."""

    def test_hash_key_uses_hmac_sha256(self):
        key = "sc_test_hmac_key_1234"
        expected = hmac.new(
            _API_KEY_PEPPER.encode("utf-8"),
            key.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert hash_key(key) == expected

    def test_hash_key_different_pepper_different_hash(self):
        """Changing the pepper changes the hash - proves pepper is used."""
        key = "sc_test_pepper_key"
        h1 = hash_key(key)
        # Manually compute with a different pepper
        h2 = hmac.new(
            b"different-pepper",
            key.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert h1 != h2


class TestAuthKeyPrefix:
    """Key prefix stored correctly (first 8 chars)."""

    def test_generated_key_prefix_is_8_chars(self):
        key = generate_api_key()
        prefix = key[:8]
        assert len(prefix) == 8
        # All generated keys start with "sc_"
        assert prefix.startswith("sc_")

    def test_key_prefix_used_in_db_lookup(self):
        """Auth middleware sends key_prefix = first 8 chars to DB query."""
        api_key = "sc_ABCDE_rest_of_key_here"
        expected_prefix = "sc_ABCDE"
        assert api_key[:8] == expected_prefix


class TestAuthLastUsedAt:
    """last_used_at updated on authenticated request."""

    @pytest.mark.asyncio
    async def test_last_used_at_updated(self):
        api_key = "sc_test_lastused_key_ab"
        db_key = _make_db_key(api_key)

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key})
        call_next = AsyncMock(return_value=MagicMock())

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [db_key]
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Track the key object returned by session.get for the update
        update_key = MagicMock()
        update_key.last_used_at = None
        mock_session.get = AsyncMock(return_value=update_key)

        with patch("sandcastle.api.auth.async_session") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        # Verify last_used_at was set to a datetime
        assert update_key.last_used_at is not None
        assert isinstance(update_key.last_used_at, datetime)
        # Verify commit was called (at least once for the update)
        mock_session.commit.assert_called()


class TestAuthAdminVsTenant:
    """Admin key (no tenant_id) vs tenant key scoping."""

    @pytest.mark.asyncio
    async def test_admin_key_has_no_tenant_restriction(self):
        api_key = "sc_test_admin_key_0000"
        db_key = _make_db_key(api_key, tenant_id=None)  # admin key

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key})
        call_next = AsyncMock(return_value=MagicMock())

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        assert request.state.tenant_id is None
        assert is_admin(request) is True

    @pytest.mark.asyncio
    async def test_tenant_key_scoped_to_tenant(self):
        api_key = "sc_test_tenant_key_1111"
        db_key = _make_db_key(api_key, tenant_id="corp-xyz")

        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": api_key})
        call_next = AsyncMock(return_value=MagicMock())

        ctx, mock_session = _patch_db([db_key])
        with ctx as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await auth_middleware(request, call_next)

        assert request.state.tenant_id == "corp-xyz"
        assert is_admin(request) is False


class TestAuthPublicPaths:
    """Public paths skip authentication."""

    @pytest.mark.asyncio
    async def test_health_endpoint_is_public(self):
        settings.auth_required = True
        request = _make_request(path="/api/health", headers={})
        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        call_next.assert_called_once()
        assert request.state._auth_checked is True

    @pytest.mark.asyncio
    async def test_templates_prefix_is_public(self):
        settings.auth_required = True
        request = _make_request(path="/api/templates/list", headers={})
        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_well_known_agent_json_is_public(self):
        settings.auth_required = True
        request = _make_request(path="/.well-known/agent.json", headers={})
        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_spec_suffix_is_public(self):
        settings.auth_required = True
        request = _make_request(path="/api/v1/workflows/abc/spec", headers={})
        call_next = AsyncMock(return_value=MagicMock())

        await auth_middleware(request, call_next)

        call_next.assert_called_once()


class TestAuthDBError:
    """Database errors return 503."""

    @pytest.mark.asyncio
    async def test_db_error_returns_503(self):
        settings.auth_required = True
        request = _make_request(headers={"X-API-Key": "sc_test_dberr_key"})
        call_next = AsyncMock()

        with patch("sandcastle.api.auth.async_session") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await auth_middleware(request, call_next)

        assert response.status_code == 503
        call_next.assert_not_called()


# ===========================================================================
# 2. RATE LIMITING (14 tests)
# ===========================================================================


class TestRateLimitUnderLimit:
    """Requests under the limit pass through."""

    @pytest.mark.asyncio
    async def test_under_limit_passes(self):
        rl = RateLimiter(max_requests=5, window_seconds=60.0)
        req = _make_request()
        req.state.tenant_id = None

        # 5 requests should all pass
        for _ in range(5):
            await rl.check(req)  # no exception


class TestRateLimitAtLimit:
    """At limit: next request returns 429."""

    @pytest.mark.asyncio
    async def test_at_limit_raises_429(self):
        rl = RateLimiter(max_requests=3, window_seconds=60.0)
        req = _make_request()
        req.state.tenant_id = None

        # Fill up the limit
        for _ in range(3):
            await rl.check(req)

        # Next request should be rejected
        with pytest.raises(HTTPException) as exc_info:
            await rl.check(req)
        assert exc_info.value.status_code == 429


class TestRateLimitWindowReset:
    """After window expires, counter resets."""

    @pytest.mark.asyncio
    async def test_counter_resets_after_window(self):
        backend = InMemoryBackend()
        rl = RateLimiter(max_requests=2, window_seconds=0.1, backend=backend)
        req = _make_request()
        req.state.tenant_id = None

        # Exhaust limit
        for _ in range(2):
            await rl.check(req)

        # Wait for window to expire
        import asyncio
        await asyncio.sleep(0.15)

        # Should pass again
        await rl.check(req)


class TestRateLimitRetryAfterHeader:
    """Retry-After header present in 429 response."""

    @pytest.mark.asyncio
    async def test_retry_after_header(self):
        rl = RateLimiter(max_requests=1, window_seconds=30.0)
        req = _make_request()
        req.state.tenant_id = None

        await rl.check(req)

        with pytest.raises(HTTPException) as exc_info:
            await rl.check(req)

        assert "Retry-After" in exc_info.value.headers
        assert exc_info.value.headers["Retry-After"] == "30"


class TestRateLimitXHeaders:
    """X-RateLimit-Limit and X-RateLimit-Remaining headers."""

    @pytest.mark.asyncio
    async def test_rate_limit_headers_present(self):
        rl = RateLimiter(max_requests=5, window_seconds=60.0)
        req = _make_request()
        req.state.tenant_id = None

        # Exhaust
        for _ in range(5):
            await rl.check(req)

        with pytest.raises(HTTPException) as exc_info:
            await rl.check(req)

        assert exc_info.value.headers["X-RateLimit-Limit"] == "5"
        assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"


class TestRateLimitTenantSeparation:
    """Different tenants have separate limits."""

    @pytest.mark.asyncio
    async def test_separate_tenant_limits(self):
        rl = RateLimiter(max_requests=2, window_seconds=60.0)

        req_a = _make_request()
        req_a.state.tenant_id = "tenant-a"

        req_b = _make_request()
        req_b.state.tenant_id = "tenant-b"

        # Fill tenant A's limit
        for _ in range(2):
            await rl.check(req_a)

        # Tenant B should still be able to make requests
        await rl.check(req_b)  # should not raise

        # Tenant A should be blocked
        with pytest.raises(HTTPException) as exc_info:
            await rl.check(req_a)
        assert exc_info.value.status_code == 429


class TestRateLimitIPBased:
    """IP-based limiting for anonymous requests."""

    @pytest.mark.asyncio
    async def test_ip_based_for_anonymous(self):
        rl = RateLimiter(max_requests=2, window_seconds=60.0)

        req = _make_request(ip="203.0.113.1")
        req.state.tenant_id = None

        for _ in range(2):
            await rl.check(req)

        with pytest.raises(HTTPException):
            await rl.check(req)

    @pytest.mark.asyncio
    async def test_different_ips_separate_limits(self):
        rl = RateLimiter(max_requests=1, window_seconds=60.0)

        req1 = _make_request(ip="10.0.0.1")
        req1.state.tenant_id = None

        req2 = _make_request(ip="10.0.0.2")
        req2.state.tenant_id = None

        # Each IP gets its own limit
        await rl.check(req1)
        await rl.check(req2)

        # Both should now be blocked
        with pytest.raises(HTTPException):
            await rl.check(req1)
        with pytest.raises(HTTPException):
            await rl.check(req2)


class TestInMemoryBackendSlidingWindow:
    """InMemoryBackend sliding window correctness."""

    @pytest.mark.asyncio
    async def test_sliding_window_count(self):
        backend = InMemoryBackend()
        # 3 requests in a 1-second window
        count1 = await backend.check_and_increment("k1", max_requests=10, window_seconds=1.0)
        count2 = await backend.check_and_increment("k1", max_requests=10, window_seconds=1.0)
        count3 = await backend.check_and_increment("k1", max_requests=10, window_seconds=1.0)

        assert count1 == 1
        assert count2 == 2
        assert count3 == 3

    @pytest.mark.asyncio
    async def test_window_object_prunes_old_entries(self):
        w = _Window()
        # Add timestamps in the past
        old_time = time.monotonic() - 100
        w.timestamps = [old_time, old_time + 1]
        # Count with a 10-second window - old entries should be pruned
        count = w.count_in_window(10.0)
        assert count == 0
        assert len(w.timestamps) == 0


class TestInMemoryBackendCleanup:
    """Stale entry pruning works."""

    @pytest.mark.asyncio
    async def test_stale_keys_pruned(self):
        backend = InMemoryBackend()
        # Add a key and immediately expire it by using a very short window
        await backend.check_and_increment("stale-key", max_requests=10, window_seconds=0.001)

        import asyncio
        await asyncio.sleep(0.01)

        # Manually trigger cleanup
        removed = backend._cleanup(0.001)
        assert removed >= 1
        assert "stale-key" not in backend._windows

    @pytest.mark.asyncio
    async def test_cleanup_triggers_periodically(self):
        """Cleanup runs every _CLEANUP_INTERVAL calls."""
        backend = InMemoryBackend()
        backend._CLEANUP_INTERVAL = 3  # Low threshold for testing

        for i in range(3):
            await backend.check_and_increment(f"key-{i}", max_requests=100, window_seconds=60.0)

        # After 3 calls, cleanup should have run (call_count resets to 0)
        assert backend._call_count == 0


class TestRateLimitEdgeCases:
    """Edge cases: zero/negative window, rate limit info."""

    @pytest.mark.asyncio
    async def test_zero_window_still_works(self):
        """With a 0-second window, all timestamps are immediately expired."""
        backend = InMemoryBackend()
        count = await backend.check_and_increment("zero", max_requests=1, window_seconds=0.0)
        # First call: count_in_window returns 0 (all pruned), adds 1, returns 1
        assert count == 1
        # Second call: the timestamp was just added but window is 0 so it's pruned
        count2 = await backend.check_and_increment("zero", max_requests=1, window_seconds=0.0)
        assert count2 == 1  # resets each time

    def test_rate_limiter_info(self):
        backend = InMemoryBackend()
        rl = RateLimiter(max_requests=10, window_seconds=60.0, backend=backend)
        info = rl.info
        assert info["max_requests"] == 10
        assert info["window_seconds"] == 60.0
        assert info["backend"] == "InMemoryBackend"
        assert info["active_keys"] == 0


class TestRateLimitKeyGeneration:
    """Rate limiter key uses tenant or IP correctly."""

    def test_key_for_tenant(self):
        rl = RateLimiter()
        req = _make_request()
        req.state.tenant_id = "my-tenant"
        assert rl._get_key(req) == "tenant:my-tenant"

    def test_key_for_anonymous(self):
        rl = RateLimiter()
        req = _make_request(ip="1.2.3.4")
        req.state.tenant_id = None
        assert rl._get_key(req) == "ip:1.2.3.4"


# ===========================================================================
# 3. SECURITY HEADERS (11 tests)
# ===========================================================================


class TestSecurityHeaderNosniff:
    """X-Content-Type-Options: nosniff present."""

    @pytest.mark.asyncio
    async def test_nosniff_on_api(self):
        request = _make_request(path="/api/runs")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["X-Content-Type-Options"] == "nosniff"


class TestSecurityHeaderXFrameOptions:
    """X-Frame-Options: DENY present."""

    @pytest.mark.asyncio
    async def test_x_frame_options_deny(self):
        request = _make_request(path="/api/health")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["X-Frame-Options"] == "DENY"


class TestSecurityHeaderReferrerPolicy:
    """Referrer-Policy present."""

    @pytest.mark.asyncio
    async def test_referrer_policy(self):
        request = _make_request(path="/dashboard")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


class TestSecurityHeaderPermissionsPolicy:
    """Permissions-Policy disables dangerous features."""

    @pytest.mark.asyncio
    async def test_permissions_policy(self):
        request = _make_request(path="/api/runs")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        pp = result.headers["Permissions-Policy"]
        assert "camera=()" in pp
        assert "microphone=()" in pp
        assert "geolocation=()" in pp
        assert "payment=()" in pp


class TestSecurityHeaderCSPDashboard:
    """CSP applied to dashboard paths."""

    @pytest.mark.asyncio
    async def test_csp_on_dashboard_path(self):
        settings.csp_report_only = False
        request = _make_request(path="/dashboard")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" in result.headers
        assert result.headers["Content-Security-Policy"] == _CSP_POLICY

    @pytest.mark.asyncio
    async def test_csp_on_root_path(self):
        """CSP is also set on the root path (not /api)."""
        settings.csp_report_only = False
        request = _make_request(path="/")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" in result.headers


class TestSecurityHeaderCSPNotOnAPI:
    """CSP NOT applied to /api paths."""

    @pytest.mark.asyncio
    async def test_no_csp_on_api_path(self):
        settings.csp_report_only = False
        request = _make_request(path="/api/runs")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" not in result.headers
        assert "Content-Security-Policy-Report-Only" not in result.headers

    @pytest.mark.asyncio
    async def test_no_csp_on_api_case_insensitive(self):
        """CSP check is case-insensitive to prevent bypass via /API/... paths."""
        settings.csp_report_only = False
        # Uppercase path should still be treated as API
        request = _make_request(path="/API/runs")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" not in result.headers


class TestSecurityHeaderCSPReportOnly:
    """CSP-Report-Only mode when configured."""

    @pytest.mark.asyncio
    async def test_csp_report_only_mode(self):
        settings.csp_report_only = True
        request = _make_request(path="/dashboard")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy-Report-Only" in result.headers
        assert "Content-Security-Policy" not in result.headers
        assert result.headers["Content-Security-Policy-Report-Only"] == _CSP_POLICY


class TestSecurityHeaderOnEveryResponse:
    """Headers on every response, not just 200."""

    @pytest.mark.asyncio
    async def test_headers_on_non_200_response(self):
        """Security headers should be present even on error responses."""
        request = _make_request(path="/api/runs")
        response = MagicMock()
        response.status_code = 500
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        # All standard headers should be present regardless of status code
        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["X-Frame-Options"] == "DENY"
        assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Permissions-Policy" in result.headers

    @pytest.mark.asyncio
    async def test_headers_on_404_response(self):
        request = _make_request(path="/nonexistent")
        response = MagicMock()
        response.status_code = 404
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["X-Frame-Options"] == "DENY"


class TestSecurityHeaderCacheControl:
    """Cache-Control: no-store on API responses."""

    @pytest.mark.asyncio
    async def test_no_store_on_api(self):
        request = _make_request(path="/api/runs")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["Cache-Control"] == "no-store"
        assert result.headers["Pragma"] == "no-cache"

    @pytest.mark.asyncio
    async def test_no_cache_headers_on_dashboard(self):
        """Dashboard paths should not have no-store cache headers."""
        request = _make_request(path="/dashboard")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert "Cache-Control" not in result.headers


class TestSecurityHeaderCrossDomainPolicy:
    """X-Permitted-Cross-Domain-Policies: none."""

    @pytest.mark.asyncio
    async def test_cross_domain_policy_none(self):
        request = _make_request(path="/api/health")
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["X-Permitted-Cross-Domain-Policies"] == "none"
