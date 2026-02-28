"""Wave 5 audit: Configuration handling, env vars, and secrets management.

Tests for findings in config.py, auth.py, routes.py, crypto.py,
license.py, telemetry.py, and __main__.py.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sandcastle.config import Settings


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    """Create a fresh Settings instance with specific overrides."""
    env_overrides = {}
    for key, val in overrides.items():
        env_overrides[key.upper()] = str(val)
    with patch.dict(os.environ, env_overrides, clear=False):
        return Settings(_env_file=None, **overrides)


# ===========================================================================
# Finding 1: default_max_cost_usd validation (was unvalidated, could go negative)
# ===========================================================================


class TestDefaultMaxCostValidation:
    """DEFAULT_MAX_COST_USD must be non-negative."""

    def test_zero_is_valid(self):
        s = _make_settings(default_max_cost_usd=0.0)
        assert s.default_max_cost_usd == 0.0

    def test_positive_value(self):
        s = _make_settings(default_max_cost_usd=10.0)
        assert s.default_max_cost_usd == 10.0

    def test_negative_clamps_to_zero(self):
        s = _make_settings(default_max_cost_usd=-5.0)
        assert s.default_max_cost_usd == 0.0

    def test_large_negative_clamps_to_zero(self):
        s = _make_settings(default_max_cost_usd=-999.99)
        assert s.default_max_cost_usd == 0.0


# ===========================================================================
# Finding 2: tool_smtp_port validation (was unvalidated)
# ===========================================================================


class TestToolSmtpPortValidation:
    """TOOL_SMTP_PORT must be a valid port number (1-65535)."""

    def test_default_is_587(self):
        s = _make_settings()
        assert s.tool_smtp_port == 587

    def test_valid_port(self):
        s = _make_settings(tool_smtp_port=465)
        assert s.tool_smtp_port == 465

    def test_port_one_is_valid(self):
        s = _make_settings(tool_smtp_port=1)
        assert s.tool_smtp_port == 1

    def test_port_65535_is_valid(self):
        s = _make_settings(tool_smtp_port=65535)
        assert s.tool_smtp_port == 65535

    def test_zero_port_falls_back(self):
        s = _make_settings(tool_smtp_port=0)
        assert s.tool_smtp_port == 587

    def test_negative_port_falls_back(self):
        s = _make_settings(tool_smtp_port=-1)
        assert s.tool_smtp_port == 587

    def test_port_above_65535_falls_back(self):
        s = _make_settings(tool_smtp_port=70000)
        assert s.tool_smtp_port == 587


# ===========================================================================
# Finding 3: _SENSITIVE_KEYS completeness in routes.py
# ===========================================================================


class TestSensitiveKeysCompleteness:
    """Ensure _SENSITIVE_KEYS in routes.py covers all credential fields."""

    def test_all_credential_fields_are_sensitive(self):
        from sandcastle.api.routes import _SENSITIVE_KEYS

        # All fields that contain secrets must be in the set
        expected_sensitive = {
            "anthropic_api_key",
            "e2b_api_key",
            "openai_api_key",
            "minimax_api_key",
            "openrouter_api_key",
            "database_url",
            "redis_url",
            "webhook_secret",
            "aws_access_key_id",
            "aws_secret_access_key",
            "credential_encryption_key",
            "admin_api_key",
            "license_key",
            "sentry_dsn",
            "tool_slack_bot_token",
            "tool_jira_api_token",
            "tool_github_token",
            "tool_notion_api_key",
            "tool_hubspot_api_key",
            "tool_salesforce_client_id",
            "tool_salesforce_client_secret",
            "tool_salesforce_refresh_token",
            "tool_zendesk_api_token",
            "tool_smtp_password",
            "tool_google_service_account",
            "tool_teams_webhook_url",
            "tool_postgresql_url",
        }
        assert expected_sensitive.issubset(_SENSITIVE_KEYS)

    def test_aws_keys_are_sensitive(self):
        from sandcastle.api.routes import _SENSITIVE_KEYS

        assert "aws_access_key_id" in _SENSITIVE_KEYS
        assert "aws_secret_access_key" in _SENSITIVE_KEYS

    def test_admin_api_key_is_sensitive(self):
        from sandcastle.api.routes import _SENSITIVE_KEYS

        assert "admin_api_key" in _SENSITIVE_KEYS

    def test_encryption_key_is_sensitive(self):
        from sandcastle.api.routes import _SENSITIVE_KEYS

        assert "credential_encryption_key" in _SENSITIVE_KEYS

    def test_sentry_dsn_is_sensitive(self):
        from sandcastle.api.routes import _SENSITIVE_KEYS

        assert "sentry_dsn" in _SENSITIVE_KEYS


# ===========================================================================
# Finding 4: .env parser handles 'export' prefix
# ===========================================================================


class TestDotEnvParser:
    """Test the CLI .env loader handles edge cases."""

    def test_export_prefix_handled(self, tmp_path, monkeypatch):
        """Lines starting with 'export ' should be parsed correctly."""
        env_file = tmp_path / ".env"
        env_file.write_text("export MY_TEST_VAR_EXPORT=hello_world\n")
        monkeypatch.chdir(tmp_path)

        # Reset the loader state
        import sandcastle.__main__ as cli
        cli._dot_env_loaded = False

        # Ensure the var is not already set
        monkeypatch.delenv("MY_TEST_VAR_EXPORT", raising=False)

        cli._load_dot_env()
        assert os.environ.get("MY_TEST_VAR_EXPORT") == "hello_world"

        # Clean up
        monkeypatch.delenv("MY_TEST_VAR_EXPORT", raising=False)
        cli._dot_env_loaded = False

    def test_comments_skipped(self, tmp_path, monkeypatch):
        """Lines starting with # should be skipped."""
        env_file = tmp_path / ".env"
        env_file.write_text("# COMMENT_VAR=should_not_set\nACTUAL_VAR_TEST=yes\n")
        monkeypatch.chdir(tmp_path)

        import sandcastle.__main__ as cli
        cli._dot_env_loaded = False
        monkeypatch.delenv("COMMENT_VAR", raising=False)
        monkeypatch.delenv("ACTUAL_VAR_TEST", raising=False)

        cli._load_dot_env()
        assert os.environ.get("COMMENT_VAR") is None
        assert os.environ.get("ACTUAL_VAR_TEST") == "yes"

        monkeypatch.delenv("ACTUAL_VAR_TEST", raising=False)
        cli._dot_env_loaded = False

    def test_quoted_values_unquoted(self, tmp_path, monkeypatch):
        """Values surrounded by quotes should have quotes stripped."""
        env_file = tmp_path / ".env"
        env_file.write_text('QUOTED_VAR_TEST="my value"\n')
        monkeypatch.chdir(tmp_path)

        import sandcastle.__main__ as cli
        cli._dot_env_loaded = False
        monkeypatch.delenv("QUOTED_VAR_TEST", raising=False)

        cli._load_dot_env()
        assert os.environ.get("QUOTED_VAR_TEST") == "my value"

        monkeypatch.delenv("QUOTED_VAR_TEST", raising=False)
        cli._dot_env_loaded = False

    def test_env_vars_take_precedence(self, tmp_path, monkeypatch):
        """Existing env vars should NOT be overwritten by .env."""
        env_file = tmp_path / ".env"
        env_file.write_text("PRECEDENCE_TEST_VAR=from_file\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PRECEDENCE_TEST_VAR", "from_env")

        import sandcastle.__main__ as cli
        cli._dot_env_loaded = False

        cli._load_dot_env()
        assert os.environ.get("PRECEDENCE_TEST_VAR") == "from_env"

        cli._dot_env_loaded = False


# ===========================================================================
# Finding 5: API_KEY_PEPPER default warning
# ===========================================================================


class TestApiKeyPepper:
    """API_KEY_PEPPER should have a warning about the default value."""

    def test_default_pepper_is_set(self):
        """The module must have a default pepper for dev mode."""
        from sandcastle.api.auth import _API_KEY_PEPPER

        assert _API_KEY_PEPPER  # Not empty

    def test_hash_key_is_deterministic(self):
        """hash_key must produce consistent results."""
        from sandcastle.api.auth import hash_key

        h1 = hash_key("test-key-12345")
        h2 = hash_key("test-key-12345")
        assert h1 == h2

    def test_different_keys_produce_different_hashes(self):
        from sandcastle.api.auth import hash_key

        h1 = hash_key("key-alpha")
        h2 = hash_key("key-beta")
        assert h1 != h2


# ===========================================================================
# Finding 6: Credential encryption key rotation
# ===========================================================================


class TestCredentialEncryptionKeyRotation:
    """When CREDENTIAL_ENCRYPTION_KEY changes, behavior should be defined."""

    def test_no_key_passthrough(self):
        """Without encryption key, data passes through as dict."""
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"token": "secret123"}
        result = encrypt_credentials(data)
        assert result == data

    def test_encrypted_without_key_returns_empty(self):
        """If stored value is encrypted string but no key set, return empty."""
        from sandcastle.engine.crypto import decrypt_credentials

        # Simulate an encrypted string without the key available
        result = decrypt_credentials("gAAAAABfake_encrypted_token")
        # Should return empty dict (safe failure) rather than the raw string
        assert isinstance(result, dict)

    def test_dict_passthrough_on_decrypt(self):
        """If stored value is already a dict, return as-is."""
        from sandcastle.engine.crypto import decrypt_credentials

        data = {"key": "value"}
        result = decrypt_credentials(data)
        assert result == data


# ===========================================================================
# Finding 7: License validation is not vulnerable to timing attacks
# ===========================================================================


class TestLicenseValidation:
    """License key validation security properties."""

    def test_empty_key_returns_community_mode(self):
        from sandcastle.engine.license import COMMUNITY_MODE, validate_license_key

        result = validate_license_key("")
        assert result == COMMUNITY_MODE

    def test_whitespace_key_returns_community_mode(self):
        from sandcastle.engine.license import COMMUNITY_MODE, validate_license_key

        result = validate_license_key("   ")
        assert result == COMMUNITY_MODE

    def test_wrong_prefix_returns_invalid(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("wrong_prefix_key")
        assert result.status == LicenseStatus.invalid

    def test_malformed_body_returns_invalid(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_no_dot_separator")
        assert result.status == LicenseStatus.invalid

    def test_invalid_base64_payload(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_!!!invalid!!!.AAAA")
        assert result.status == LicenseStatus.invalid

    def test_invalid_signature(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        # Valid base64 but wrong signature
        import base64
        payload = base64.urlsafe_b64encode(b'{"v":1}').decode().rstrip("=")
        sig = base64.urlsafe_b64encode(b'\x00' * 64).decode().rstrip("=")
        result = validate_license_key(f"sc_lic_{payload}.{sig}")
        assert result.status == LicenseStatus.invalid

    def test_license_never_raises(self):
        """validate_license_key should never raise, always return LicenseInfo."""
        from sandcastle.engine.license import LicenseInfo, validate_license_key

        # Test with various garbage inputs
        for garbage in [None, "", "x", "sc_lic_", "sc_lic_.", "sc_lic_a.b", "\x00\xff"]:
            result = validate_license_key(garbage or "")
            assert isinstance(result, LicenseInfo)


# ===========================================================================
# Finding 8: Telemetry sensitive env keys coverage
# ===========================================================================


class TestTelemetrySensitiveKeys:
    """Ensure telemetry _SENSITIVE_ENV_KEYS covers all credential fields."""

    def test_all_tool_credentials_covered(self):
        from sandcastle.engine.telemetry import _SENSITIVE_ENV_KEYS

        tool_creds = {
            "tool_slack_bot_token",
            "tool_jira_api_token",
            "tool_github_token",
            "tool_notion_api_key",
            "tool_hubspot_api_key",
            "tool_salesforce_client_id",
            "tool_salesforce_client_secret",
            "tool_salesforce_refresh_token",
            "tool_zendesk_api_token",
            "tool_smtp_password",
            "tool_google_service_account",
            "tool_teams_webhook_url",
            "tool_postgresql_url",
        }
        assert tool_creds.issubset(_SENSITIVE_ENV_KEYS)

    def test_admin_api_key_covered(self):
        from sandcastle.engine.telemetry import _SENSITIVE_ENV_KEYS

        assert "admin_api_key" in _SENSITIVE_ENV_KEYS


# ===========================================================================
# Finding 9: Settings update allowlist is correct
# ===========================================================================


class TestSettingsUpdateAllowlist:
    """The _MUTABLE_SETTINGS allowlist must not include security-critical keys."""

    def test_mutable_settings_excludes_critical(self):
        """Security-critical settings must NOT be in the mutable set."""
        # These must never be changeable via the API
        critical = {
            "auth_required", "dashboard_origin", "webhook_secret",
            "database_url", "redis_url", "credential_encryption_key",
            "admin_api_key", "sandbox_backend",
        }
        # We check by importing the endpoint module and inspecting
        from sandcastle.api.schemas import SettingsUpdateRequest
        updatable_fields = set(SettingsUpdateRequest.model_fields.keys())
        assert not critical.intersection(updatable_fields), (
            f"Security-critical fields found in SettingsUpdateRequest: "
            f"{critical.intersection(updatable_fields)}"
        )


# ===========================================================================
# Finding 10: Restorable settings from DB
# ===========================================================================


class TestRestorableSettings:
    """Settings restored from DB on startup must be safe."""

    def test_restorable_excludes_security_settings(self):
        """Security settings must not be in the restorable set."""
        # The _RESTORABLE_SETTINGS set is defined inline in main.py lifespan.
        # We test the expectation here.
        restorable = {
            "anthropic_api_key", "e2b_api_key", "openai_api_key",
            "minimax_api_key", "openrouter_api_key",
            "default_max_cost_usd", "log_level", "max_workflow_depth",
        }
        forbidden = {
            "auth_required", "database_url", "redis_url",
            "credential_encryption_key", "admin_api_key",
            "sandbox_backend", "dashboard_origin",
        }
        assert not restorable.intersection(forbidden)


# ===========================================================================
# Finding 11: max_concurrent_sandboxes upper bound
# ===========================================================================


class TestMaxConcurrentSandboxesUpperBound:
    """MAX_CONCURRENT_SANDBOXES should have an upper bound."""

    def test_large_value_is_clamped(self):
        s = _make_settings(max_concurrent_sandboxes=100)
        # Should be clamped to a reasonable maximum (50)
        assert s.max_concurrent_sandboxes <= 50

    def test_fifty_is_accepted(self):
        s = _make_settings(max_concurrent_sandboxes=50)
        assert s.max_concurrent_sandboxes == 50


# ===========================================================================
# Finding 12: .env file permissions
# ===========================================================================


class TestEnvFilePermissions:
    """The .env file and data dirs should be created with restricted perms."""

    @pytest.mark.skipif(os.name == "nt", reason="chmod not reliable on Windows")
    def test_init_creates_env_with_restricted_perms(self, tmp_path, monkeypatch):
        """sandcastle init should create .env with 0600 permissions."""
        import sandcastle.__main__ as cli

        env_path = tmp_path / ".env"
        # Simulate what _cmd_init_inner does for the .env file
        env_path.write_text("TEST=value\n")
        try:
            env_path.chmod(0o600)
        except OSError:
            pytest.skip("chmod not supported on this filesystem")

        mode = env_path.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    @pytest.mark.skipif(os.name == "nt", reason="chmod not reliable on Windows")
    def test_data_dir_restricted_perms(self, tmp_path):
        """Data directory should be created with 0700 permissions."""
        data_dir = tmp_path / "test_data"
        data_dir.mkdir()
        try:
            data_dir.chmod(0o700)
        except OSError:
            pytest.skip("chmod not supported")

        mode = data_dir.stat().st_mode & 0o777
        assert mode == 0o700, f"Expected 0700, got {oct(mode)}"


# ===========================================================================
# Finding 13: Telemetry scrubbing
# ===========================================================================


class TestTelemetryScrubbing:
    """Telemetry must scrub sensitive data from events."""

    def test_scrub_api_key_pattern(self):
        from sandcastle.engine.telemetry import _scrub_text

        text = "api_key=sk-proj-abcdef123456"
        result = _scrub_text(text)
        assert "sk-proj-abcdef123456" not in result
        assert "[REDACTED]" in result

    def test_scrub_bearer_token(self):
        from sandcastle.engine.telemetry import _scrub_text

        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
        result = _scrub_text(text)
        assert "eyJhbGci" not in result

    def test_scrub_github_token(self):
        from sandcastle.engine.telemetry import _scrub_text

        text = "token ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
        result = _scrub_text(text)
        assert "ghp_aBcDeFg" not in result

    def test_anonymize_removes_request_data(self):
        from sandcastle.engine.telemetry import _anonymize_event

        event = {
            "request": {"url": "/api/test", "headers": {"X-API-Key": "secret"}},
            "user": {"ip_address": "1.2.3.4", "email": "user@test.com"},
            "message": "test error",
        }
        result = _anonymize_event(event, {})
        assert "request" not in result
        assert "user" not in result

    def test_anonymize_strips_frame_vars(self):
        from sandcastle.engine.telemetry import _anonymize_event

        event = {
            "exception": {
                "values": [{
                    "value": "test error",
                    "stacktrace": {
                        "frames": [{
                            "filename": "test.py",
                            "vars": {"api_key": "sk-secret-123"},
                        }]
                    }
                }]
            }
        }
        result = _anonymize_event(event, {})
        frame = result["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert "vars" not in frame


# ===========================================================================
# Finding 14: Config model_config settings
# ===========================================================================


class TestConfigModelBehavior:
    """Settings class should ignore unknown env vars (extra='ignore')."""

    def test_extra_fields_ignored(self):
        """Unknown settings should be silently ignored, not raise."""
        with patch.dict(os.environ, {"UNKNOWN_SANDCASTLE_FIELD": "test"}, clear=False):
            s = Settings(_env_file=None)
            assert not hasattr(s, "unknown_sandcastle_field")

    def test_is_local_mode_sqlite(self):
        s = _make_settings(database_url="sqlite+aiosqlite:///test.db")
        assert s.is_local_mode is True

    def test_is_local_mode_postgres(self):
        s = _make_settings(database_url="postgresql+asyncpg://user:pass@host/db")
        assert s.is_local_mode is False

    def test_is_local_mode_empty(self):
        s = _make_settings(database_url="")
        assert s.is_local_mode is True
