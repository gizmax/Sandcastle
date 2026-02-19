"""Tests for the Settings API endpoints (GET /settings, PATCH /settings)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.config import settings
from sandcastle.main import app

client = TestClient(app)

# Sensitive keys that should appear masked in the response
_SENSITIVE_KEYS = frozenset({
    "anthropic_api_key",
    "e2b_api_key",
    "openai_api_key",
    "minimax_api_key",
    "openrouter_api_key",
    "database_url",
    "redis_url",
    "webhook_secret",
})


# ---------------------------------------------------------------------------
# Fixture: save and restore settings between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_settings():
    """Save original settings values before each test and restore after."""
    original = {}
    for field in (
        "sandstorm_url",
        "anthropic_api_key",
        "e2b_api_key",
        "openai_api_key",
        "minimax_api_key",
        "openrouter_api_key",
        "auth_required",
        "dashboard_origin",
        "default_max_cost_usd",
        "webhook_secret",
        "log_level",
        "max_workflow_depth",
    ):
        original[field] = getattr(settings, field)
    yield
    for field, value in original.items():
        setattr(settings, field, value)


# ---------------------------------------------------------------------------
# Tests: GET /settings
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_get_settings_returns_all_fields(self):
        """GET /settings returns a data dict with all expected setting keys."""
        response = client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()["data"]

        expected_keys = {
            "sandstorm_url",
            "anthropic_api_key",
            "e2b_api_key",
            "openai_api_key",
            "minimax_api_key",
            "openrouter_api_key",
            "auth_required",
            "dashboard_origin",
            "default_max_cost_usd",
            "webhook_secret",
            "log_level",
            "max_workflow_depth",
            "storage_backend",
            "storage_bucket",
            "storage_endpoint",
            "data_dir",
            "workflows_dir",
            "is_local_mode",
            "database_url",
            "redis_url",
        }
        assert expected_keys.issubset(data.keys())

    def test_sensitive_values_are_masked(self):
        """Sensitive settings show only the last 4 characters prefixed with ****."""
        # Set a recognizable sensitive value
        settings.anthropic_api_key = "sk-secret-key-12345678"
        settings.webhook_secret = "whsec_abcdefgh"

        response = client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()["data"]

        # Should be masked: ****<last 4 chars>
        assert data["anthropic_api_key"] == "****5678"
        assert data["webhook_secret"] == "****efgh"

    def test_empty_sensitive_values_masked_as_empty(self):
        """Empty sensitive strings should be returned as empty string, not masked."""
        settings.anthropic_api_key = ""
        settings.e2b_api_key = ""

        response = client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["anthropic_api_key"] == ""
        assert data["e2b_api_key"] == ""

    def test_response_wrapper_format(self):
        """Settings response uses the standard {data, error} wrapper."""
        response = client.get("/api/settings")
        body = response.json()
        assert "data" in body
        assert "error" in body
        assert body["error"] is None


# ---------------------------------------------------------------------------
# Tests: PATCH /settings
# ---------------------------------------------------------------------------


class TestUpdateSettings:
    def test_update_single_setting(self):
        """PATCH /settings updates a single setting and returns the new value."""
        with patch("sandcastle.api.routes.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            response = client.patch(
                "/api/settings",
                json={"log_level": "debug"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["log_level"] == "debug"
        # Verify the runtime config was updated
        assert settings.log_level == "debug"

    def test_update_multiple_settings(self):
        """PATCH /settings can update multiple fields at once."""
        with patch("sandcastle.api.routes.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            response = client.patch(
                "/api/settings",
                json={
                    "log_level": "warning",
                    "max_workflow_depth": 10,
                    "default_max_cost_usd": 5.0,
                },
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["log_level"] == "warning"
        assert data["max_workflow_depth"] == 10
        assert data["default_max_cost_usd"] == 5.0

    def test_update_empty_body_returns_current(self):
        """PATCH /settings with empty body returns current settings unchanged."""
        settings.log_level = "info"
        settings.max_workflow_depth = 5

        response = client.patch("/api/settings", json={})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["log_level"] == "info"
        assert data["max_workflow_depth"] == 5


# ---------------------------------------------------------------------------
# Tests: PATCH /settings validation
# ---------------------------------------------------------------------------


class TestSettingsValidation:
    def test_invalid_log_level_returns_422(self):
        """Invalid log_level value should be rejected with 422."""
        response = client.patch(
            "/api/settings",
            json={"log_level": "superverbose"},
        )
        assert response.status_code == 422

    def test_max_workflow_depth_too_high_returns_422(self):
        """max_workflow_depth > 20 should be rejected with 422."""
        response = client.patch(
            "/api/settings",
            json={"max_workflow_depth": 50},
        )
        assert response.status_code == 422

    def test_max_workflow_depth_too_low_returns_422(self):
        """max_workflow_depth < 1 should be rejected with 422."""
        response = client.patch(
            "/api/settings",
            json={"max_workflow_depth": 0},
        )
        assert response.status_code == 422

    def test_negative_default_max_cost_returns_422(self):
        """Negative default_max_cost_usd should be rejected with 422."""
        response = client.patch(
            "/api/settings",
            json={"default_max_cost_usd": -1.5},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Tests: Admin guard
# ---------------------------------------------------------------------------


class TestSettingsAdminGuard:
    def test_require_admin_raises_403_for_tenant(self):
        """_require_admin should raise HTTPException(403) when auth is on and tenant is set."""
        from fastapi import HTTPException

        from sandcastle.api.routes import _require_admin

        settings.auth_required = True

        mock_request = MagicMock()
        with patch("sandcastle.api.routes.is_admin", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                _require_admin(mock_request)

        assert exc_info.value.status_code == 403

    def test_require_admin_passes_for_admin(self):
        """_require_admin should pass without error when tenant_id is None (admin)."""
        from sandcastle.api.routes import _require_admin

        settings.auth_required = True

        mock_request = MagicMock()
        with patch("sandcastle.api.routes.is_admin", return_value=True):
            # Should not raise
            _require_admin(mock_request)

    def test_require_admin_passes_when_auth_disabled(self):
        """_require_admin should always pass when auth_required is False."""
        from sandcastle.api.routes import _require_admin

        settings.auth_required = False

        mock_request = MagicMock()
        with patch("sandcastle.api.routes.is_admin", return_value=True):
            # Should not raise when auth is disabled
            _require_admin(mock_request)
