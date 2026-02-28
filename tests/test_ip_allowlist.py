"""Tests for IP allowlisting on API keys."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.api.auth import hash_key


def _make_db_key(api_key_str, **overrides):
    """Create a mock DB key with correct key_hash for auth matching."""
    db_key = MagicMock()
    db_key.key_hash = hash_key(api_key_str)
    db_key.key_prefix = api_key_str[:8]
    db_key.expires_at = overrides.get("expires_at", None)
    db_key.is_active = overrides.get("is_active", True)
    db_key.tenant_id = overrides.get("tenant_id", None)
    db_key.id = overrides.get("id", uuid.uuid4())
    db_key.allowed_cidrs = overrides.get("allowed_cidrs", None)
    return db_key


class TestIPAllowlistAuth:
    """Auth middleware should check IP against allowed_cidrs."""

    @pytest.mark.asyncio
    async def test_ip_in_allowlist_passes(self):
        """Request from an allowed IP should pass through."""
        from sandcastle.api.auth import auth_middleware

        api_key_str = "sc_test-key"
        request = MagicMock()
        request.url.path = "/api/workflows"
        request.headers = {"X-API-Key": api_key_str}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "10.0.1.5"
        request.state = MagicMock()

        db_key = _make_db_key(api_key_str, allowed_cidrs=["10.0.0.0/8"])

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [db_key]
        mock_session.execute.return_value = mock_result
        mock_session.get.return_value = db_key

        call_next = AsyncMock()
        call_next.return_value = MagicMock()

        with (
            patch("sandcastle.api.auth.settings") as mock_settings,
            patch("sandcastle.api.auth.async_session") as mock_sf,
        ):
            mock_settings.auth_required = True
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

            await auth_middleware(request, call_next)

        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_ip_not_in_allowlist_blocked(self):
        """Request from a non-allowed IP should be blocked with 403."""
        from sandcastle.api.auth import auth_middleware

        api_key_str = "sc_test-key"
        request = MagicMock()
        request.url.path = "/api/workflows"
        request.headers = {"X-API-Key": api_key_str}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"
        request.state = MagicMock()

        db_key = _make_db_key(api_key_str, allowed_cidrs=["10.0.0.0/8"])

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [db_key]
        mock_session.execute.return_value = mock_result

        call_next = AsyncMock()

        with (
            patch("sandcastle.api.auth.settings") as mock_settings,
            patch("sandcastle.api.auth.async_session") as mock_sf,
        ):
            mock_settings.auth_required = True
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

            response = await auth_middleware(request, call_next)

        assert response.status_code == 403
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_ipv6_in_allowlist(self):
        """IPv6 addresses should work in allowlists."""
        from sandcastle.api.auth import auth_middleware

        api_key_str = "sc_test-key"
        request = MagicMock()
        request.url.path = "/api/workflows"
        request.headers = {"X-API-Key": api_key_str}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "::1"
        request.state = MagicMock()

        db_key = _make_db_key(api_key_str, allowed_cidrs=["::1/128"])

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [db_key]
        mock_session.execute.return_value = mock_result
        mock_session.get.return_value = db_key

        call_next = AsyncMock()
        call_next.return_value = MagicMock()

        with (
            patch("sandcastle.api.auth.settings") as mock_settings,
            patch("sandcastle.api.auth.async_session") as mock_sf,
        ):
            mock_settings.auth_required = True
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

            await auth_middleware(request, call_next)

        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_allowlist_allows_all(self):
        """Null/empty allowed_cidrs should allow all IPs."""
        from sandcastle.api.auth import auth_middleware

        api_key_str = "sc_test-key"
        request = MagicMock()
        request.url.path = "/api/workflows"
        request.headers = {"X-API-Key": api_key_str}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "1.2.3.4"
        request.state = MagicMock()

        db_key = _make_db_key(api_key_str, allowed_cidrs=None)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [db_key]
        mock_session.execute.return_value = mock_result
        mock_session.get.return_value = db_key

        call_next = AsyncMock()
        call_next.return_value = MagicMock()

        with (
            patch("sandcastle.api.auth.settings") as mock_settings,
            patch("sandcastle.api.auth.async_session") as mock_sf,
        ):
            mock_settings.auth_required = True
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

            await auth_middleware(request, call_next)

        call_next.assert_called_once()


class TestAllowlistSchemas:
    """Allowlist schema validation."""

    def test_valid_cidrs(self):
        from sandcastle.api.schemas import ApiKeyAllowlistRequest

        req = ApiKeyAllowlistRequest(cidrs=["10.0.0.0/8", "192.168.1.0/24", "::1/128"])
        assert len(req.cidrs) == 3

    def test_empty_cidrs_clears(self):
        from sandcastle.api.schemas import ApiKeyAllowlistRequest

        req = ApiKeyAllowlistRequest(cidrs=[])
        assert req.cidrs == []

    def test_invalid_cidr_validation_in_endpoint(self):
        """The endpoint validates CIDRs, not the schema. Schema just accepts strings."""
        import ipaddress

        from sandcastle.api.schemas import ApiKeyAllowlistRequest

        # Schema allows any string
        req = ApiKeyAllowlistRequest(cidrs=["not-a-cidr"])
        assert req.cidrs == ["not-a-cidr"]

        # But ipaddress.ip_network would raise
        with pytest.raises(ValueError):
            ipaddress.ip_network("not-a-cidr", strict=False)
