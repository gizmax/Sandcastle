"""Tests for API key rotation and expiry."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
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


class TestKeyExpiry:
    """Auth middleware should reject expired keys."""

    @pytest.mark.asyncio
    async def test_expired_key_rejected(self):
        """A key with expires_at in the past should return 401."""
        from sandcastle.api.auth import auth_middleware

        api_key_str = "sc_test-key"
        request = MagicMock()
        request.url.path = "/api/workflows"
        request.headers = {"X-API-Key": api_key_str}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.state = MagicMock()

        expired_key = _make_db_key(
            api_key_str,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [expired_key]
        mock_session.execute.return_value = mock_result

        call_next = AsyncMock()

        with (
            patch("sandcastle.api.auth.settings") as mock_settings,
            patch("sandcastle.api.auth.async_session") as mock_session_factory,
        ):
            mock_settings.auth_required = True
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            response = await auth_middleware(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_expired_key_allowed(self):
        """A key with expires_at in the future should be allowed."""
        from sandcastle.api.auth import auth_middleware

        api_key_str = "sc_test-key"
        request = MagicMock()
        request.url.path = "/api/workflows"
        request.headers = {"X-API-Key": api_key_str}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.state = MagicMock()

        future_key = _make_db_key(
            api_key_str,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            tenant_id="tenant-1",
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [future_key]
        mock_session.execute.return_value = mock_result
        mock_session.get.return_value = future_key

        call_next = AsyncMock()
        expected_response = MagicMock()
        call_next.return_value = expected_response

        with (
            patch("sandcastle.api.auth.settings") as mock_settings,
            patch("sandcastle.api.auth.async_session") as mock_session_factory,
        ):
            mock_settings.auth_required = True
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await auth_middleware(request, call_next)

        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_null_expires_at_allowed(self):
        """A key with no expiry should always be allowed."""
        from sandcastle.api.auth import auth_middleware

        api_key_str = "sc_test-key"
        request = MagicMock()
        request.url.path = "/api/workflows"
        request.headers = {"X-API-Key": api_key_str}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.state = MagicMock()

        no_expiry_key = _make_db_key(api_key_str, expires_at=None)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [no_expiry_key]
        mock_session.execute.return_value = mock_result
        mock_session.get.return_value = no_expiry_key

        call_next = AsyncMock()
        call_next.return_value = MagicMock()

        with (
            patch("sandcastle.api.auth.settings") as mock_settings,
            patch("sandcastle.api.auth.async_session") as mock_session_factory,
        ):
            mock_settings.auth_required = True
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await auth_middleware(request, call_next)

        call_next.assert_called_once()


class TestRotateSchemas:
    """Rotation request/response schema validation."""

    def test_rotate_request_defaults(self):
        from sandcastle.api.schemas import ApiKeyRotateRequest

        req = ApiKeyRotateRequest()
        assert req.grace_period_hours is None

    def test_rotate_request_custom_grace(self):
        from sandcastle.api.schemas import ApiKeyRotateRequest

        req = ApiKeyRotateRequest(grace_period_hours=48)
        assert req.grace_period_hours == 48

    def test_rotate_response_fields(self):
        from sandcastle.api.schemas import ApiKeyRotateResponse

        resp = ApiKeyRotateResponse(
            new_key="sc_abc123",
            new_key_id="uuid-1",
            old_key_id="uuid-2",
            old_key_expires_at=datetime.now(timezone.utc),
            grace_period_hours=24,
        )
        assert resp.new_key == "sc_abc123"
        assert resp.grace_period_hours == 24


class TestApiKeyResponseFields:
    """ApiKeyResponse should include new fields."""

    def test_expires_at_field(self):
        from sandcastle.api.schemas import ApiKeyResponse

        resp = ApiKeyResponse(
            id="test",
            name="test key",
            is_active=True,
            expires_at=datetime.now(timezone.utc),
            allowed_cidrs=["10.0.0.0/8"],
        )
        assert resp.expires_at is not None
        assert resp.allowed_cidrs == ["10.0.0.0/8"]

    def test_defaults_none(self):
        from sandcastle.api.schemas import ApiKeyResponse

        resp = ApiKeyResponse(id="test", name="test", is_active=True)
        assert resp.expires_at is None
        assert resp.allowed_cidrs is None
