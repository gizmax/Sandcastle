"""Comprehensive tests for license validation, telemetry, and OpenTelemetry.

Covers:
1. License (15+ tests) - key parsing, expiry, tiers, signature, caching, formatting
2. Telemetry (10+ tests) - opt-in, scrubbing, anonymization, local reports
3. OpenTelemetry (8+ tests) - init, spans, attributes, errors, service name

All Sentry and OTEL exporters are fully mocked - no external calls.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# License imports
# ---------------------------------------------------------------------------
from sandcastle.engine.license import (
    COMMUNITY_MODE,
    LicenseInfo,
    LicenseStatus,
    LicenseTier,
    _b64url_decode,
    get_license,
    reset_cache,
    validate_license_key,
)

# ---------------------------------------------------------------------------
# Telemetry imports
# ---------------------------------------------------------------------------
from sandcastle.engine.telemetry import (
    _SENSITIVE_ENV_KEYS,
    _SENSITIVE_PATTERNS,
    _anonymize_event,
    _reset as _reset_telemetry,
    _save_local_report,
    _scrub_path,
    _scrub_text,
    capture_backend_error,
    capture_step_error,
    init_sentry,
    is_enabled,
    set_workflow_context,
)

# ---------------------------------------------------------------------------
# OpenTelemetry imports
# ---------------------------------------------------------------------------
import sandcastle.engine.otel as otel_module

# ---------------------------------------------------------------------------
# Pre-generated test keys (signed with the matching Ed25519 private key)
# ---------------------------------------------------------------------------

# Valid pro key (expires 2099-12-31, licensee="Test Corp", 10 seats)
_PRO_KEY = (
    "sc_lic_eyJ2IjoxLCJ0aWVyIjoicHJvIiwibGljZW5zZWUiOiJUZXN0IENvcnAiLCJtYXhf"
    "c2VhdHMiOjEwLCJleHAiOiIyMDk5LTEyLTMxIiwiaWF0IjoiMjAyNi0wMi0yNiIsImlkIj"
    "oibGljX3Rlc3QwMDAxIn0.TuAmX7VJ33zEfIY-NLStTr2ADUIHjeWyewCVDqvpaeRluNTeP"
    "GaYbMC8TE6S5FiyY_0-xNJu0AA7woYFHUEiBA"
)

# Expired pro key (expired 2020-01-01, licensee="Expired Corp", 5 seats)
_EXPIRED_KEY = (
    "sc_lic_eyJ2IjoxLCJ0aWVyIjoicHJvIiwibGljZW5zZWUiOiJFeHBpcmVkIENvcnAiLCJt"
    "YXhfc2VhdHMiOjUsImV4cCI6IjIwMjAtMDEtMDEiLCJpYXQiOiIyMDE5LTAxLTAxIiwiaWQi"
    "OiJsaWNfdGVzdDAwMDIifQ.aZK91wxVzqS8EMFW62TxRQPkG6r5yIMfplL9PrZiFYWYDU2v"
    "Kxqc-VE2bRolF6GEyT00RoI2fA6mMcWuKpdlCg"
)

# Enterprise key (expires 2099-12-31, licensee="Enterprise Inc", 100 seats)
_ENTERPRISE_KEY = (
    "sc_lic_eyJ2IjoxLCJ0aWVyIjoiZW50ZXJwcmlzZSIsImxpY2Vuc2VlIjoiRW50ZXJwcmlz"
    "ZSBJbmMiLCJtYXhfc2VhdHMiOjEwMCwiZXhwIjoiMjA5OS0xMi0zMSIsImlhdCI6IjIwMjYt"
    "MDItMjYiLCJpZCI6ImxpY190ZXN0MDAwMyJ9.cag_hJPe7RYmBlnb0zMTOgcY0FvE4CQchG3J"
    "u13XvMDbZPgu7SllTY9OhZr67R9K0J1ZYrvVBP68hQrkBZtKBA"
)

# Bad version (v:2)
_BAD_VERSION_KEY = (
    "sc_lic_eyJ2IjoyLCJ0aWVyIjoicHJvIiwibGljZW5zZWUiOiJYIiwibWF4X3NlYXRzIjox"
    "LCJleHAiOiIyMDk5LTEyLTMxIiwiaWF0IjoiMjAyNi0wMi0yNiIsImlkIjoibGljX3Rlc3Qw"
    "MDA0In0.QVksXtofZh039-QJPFuf3SaOSlVBx-BstD8R0QmnTslDP6y0yCz2e9xEkCx5WE_z"
    "vK6JzcRhWY1_CZlV5lhbAg"
)


# =========================================================================
# SECTION 1: LICENSE VALIDATION (15+ tests)
# =========================================================================


class TestLicenseValidKey:
    """Valid license key parses correctly with all fields."""

    def test_valid_pro_key_status(self):
        result = validate_license_key(_PRO_KEY)
        assert result.status == LicenseStatus.valid

    def test_valid_pro_key_tier(self):
        result = validate_license_key(_PRO_KEY)
        assert result.tier == LicenseTier.pro

    def test_valid_pro_key_licensee(self):
        result = validate_license_key(_PRO_KEY)
        assert result.licensee == "Test Corp"

    def test_valid_pro_key_is_production(self):
        result = validate_license_key(_PRO_KEY)
        assert result.is_production is True

    def test_valid_pro_key_issued_date(self):
        result = validate_license_key(_PRO_KEY)
        assert result.issued == "2026-02-26"


class TestLicenseExpired:
    """Expired license detection."""

    def test_expired_license_detected(self):
        result = validate_license_key(_EXPIRED_KEY)
        assert result.status == LicenseStatus.expired

    def test_expired_license_detail_contains_date(self):
        result = validate_license_key(_EXPIRED_KEY)
        assert "2020-01-01" in result.detail

    def test_expired_license_not_production(self):
        result = validate_license_key(_EXPIRED_KEY)
        assert result.is_production is False

    def test_expired_license_preserves_tier(self):
        """Even when expired, the tier field should still reflect the original tier."""
        result = validate_license_key(_EXPIRED_KEY)
        assert result.tier == LicenseTier.pro


class TestLicenseInvalidSignature:
    """Invalid/tampered signature is rejected."""

    def test_invalid_signature_rejected(self):
        # Swap signature from enterprise key onto pro key payload
        payload = _PRO_KEY.rsplit(".", 1)[0]
        sig = _ENTERPRISE_KEY.rsplit(".", 1)[1]
        tampered = f"{payload}.{sig}"
        result = validate_license_key(tampered)
        assert result.status == LicenseStatus.invalid
        assert "signature" in result.detail.lower()

    def test_corrupted_payload_rejected(self):
        # Flip a character in the base64 payload
        parts = _PRO_KEY.split(".")
        payload_chars = list(parts[0])
        idx = len("sc_lic_") + 10
        payload_chars[idx] = "Z" if payload_chars[idx] != "Z" else "A"
        tampered = "".join(payload_chars) + "." + parts[1]
        result = validate_license_key(tampered)
        assert result.status == LicenseStatus.invalid


class TestLicenseTierExtraction:
    """Tier extraction for pro and enterprise keys."""

    def test_pro_tier_extraction(self):
        result = validate_license_key(_PRO_KEY)
        assert result.tier == LicenseTier.pro

    def test_enterprise_tier_extraction(self):
        result = validate_license_key(_ENTERPRISE_KEY)
        assert result.tier == LicenseTier.enterprise

    def test_enterprise_licensee(self):
        result = validate_license_key(_ENTERPRISE_KEY)
        assert result.licensee == "Enterprise Inc"


class TestLicenseSeatCount:
    """License seat count parsing."""

    def test_pro_seat_count(self):
        result = validate_license_key(_PRO_KEY)
        assert result.max_seats == 10

    def test_enterprise_seat_count(self):
        result = validate_license_key(_ENTERPRISE_KEY)
        assert result.max_seats == 100

    def test_expired_preserves_seat_count(self):
        result = validate_license_key(_EXPIRED_KEY)
        assert result.max_seats == 5


class TestLicenseNoKey:
    """License without key returns free/community tier."""

    def test_no_key_returns_community(self):
        result = validate_license_key("")
        assert result is COMMUNITY_MODE
        assert result.tier == LicenseTier.community

    def test_empty_key_returns_community(self):
        result = validate_license_key("   ")
        assert result is COMMUNITY_MODE
        assert result.status == LicenseStatus.missing

    def test_none_like_returns_community(self):
        # validate_license_key checks isinstance(key, str)
        result = validate_license_key(None)  # type: ignore[arg-type]
        assert result is COMMUNITY_MODE


class TestLicenseExpiryDate:
    """Expiry date calculation and validation."""

    def test_future_expiry_is_valid(self):
        result = validate_license_key(_PRO_KEY)
        assert result.status == LicenseStatus.valid
        assert result.expires == "2099-12-31"

    def test_past_expiry_is_expired(self):
        result = validate_license_key(_EXPIRED_KEY)
        assert result.status == LicenseStatus.expired


class TestLicenseMalformedKey:
    """Malformed license key handling."""

    def test_missing_prefix(self):
        result = validate_license_key("wrong_prefix_abc.def")
        assert result.status == LicenseStatus.invalid
        assert "prefix" in result.detail.lower()

    def test_missing_dot_separator(self):
        result = validate_license_key("sc_lic_payloadwithoutdot")
        assert result.status == LicenseStatus.invalid

    def test_empty_payload_part(self):
        result = validate_license_key("sc_lic_.sig")
        assert result.status == LicenseStatus.invalid

    def test_empty_signature_part(self):
        result = validate_license_key("sc_lic_payload.")
        assert result.status == LicenseStatus.invalid

    def test_too_many_dots(self):
        result = validate_license_key("sc_lic_a.b.c")
        assert result.status == LicenseStatus.invalid

    def test_oversized_key_rejected(self):
        # Keys over _MAX_KEY_LENGTH (4096) are rejected early
        huge_key = "sc_lic_" + "A" * 5000 + ".sig"
        result = validate_license_key(huge_key)
        assert result.status == LicenseStatus.invalid
        assert "maximum length" in result.detail.lower()

    def test_bad_base64_payload(self):
        # Invalid base64url characters
        result = validate_license_key("sc_lic_!!!invalid!!!.c2ln")
        assert result.status == LicenseStatus.invalid

    def test_unsupported_version(self):
        result = validate_license_key(_BAD_VERSION_KEY)
        assert result.status == LicenseStatus.invalid
        assert "version" in result.detail.lower()


class TestLicenseFeatureChecks:
    """Feature checks via is_production property."""

    def test_community_not_production(self):
        assert COMMUNITY_MODE.is_production is False

    def test_valid_pro_is_production(self):
        result = validate_license_key(_PRO_KEY)
        assert result.is_production is True

    def test_valid_enterprise_is_production(self):
        result = validate_license_key(_ENTERPRISE_KEY)
        assert result.is_production is True

    def test_expired_is_not_production(self):
        result = validate_license_key(_EXPIRED_KEY)
        assert result.is_production is False

    def test_invalid_is_not_production(self):
        result = validate_license_key("sc_lic_bad.bad")
        assert result.is_production is False


class TestLicenseEd25519Verification:
    """Ed25519 signature verification specifics."""

    def test_cryptography_not_installed(self):
        orig = getattr(__builtins__, "__import__", __import__)

        def mock_import(name, *args, **kwargs):
            if name.startswith("cryptography"):
                raise ImportError("mocked")
            return orig(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = validate_license_key(_PRO_KEY)
            assert result.status == LicenseStatus.invalid
            assert "cryptography" in result.detail.lower()

    def test_valid_signature_accepted(self):
        result = validate_license_key(_PRO_KEY)
        assert result.status == LicenseStatus.valid

    def test_swapped_signature_rejected(self):
        # Use enterprise payload with pro signature
        payload = _ENTERPRISE_KEY.rsplit(".", 1)[0]
        sig = _PRO_KEY.rsplit(".", 1)[1]
        result = validate_license_key(f"{payload}.{sig}")
        assert result.status == LicenseStatus.invalid


class TestLicenseCaching:
    """License caching - parsed once and reused."""

    def setup_method(self):
        reset_cache()

    def teardown_method(self):
        reset_cache()

    def test_get_license_returns_same_object_on_repeat(self):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.license_key = _PRO_KEY
            first = get_license()
            second = get_license()
            assert first is second

    def test_cache_invalidates_on_key_change(self):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.license_key = _PRO_KEY
            first = get_license()
            assert first.status == LicenseStatus.valid

            mock_s.license_key = ""
            second = get_license()
            assert second is COMMUNITY_MODE
            assert first is not second

    def test_reset_cache_forces_revalidation(self):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.license_key = _PRO_KEY
            first = get_license()
            reset_cache()
            second = get_license()
            # Same key, but different objects because cache was cleared
            assert first is not second
            assert first.status == second.status


class TestLicenseStatusFormatting:
    """License status display formatting and enum values."""

    def test_status_enum_values(self):
        assert LicenseStatus.valid.value == "valid"
        assert LicenseStatus.expired.value == "expired"
        assert LicenseStatus.invalid.value == "invalid"
        assert LicenseStatus.missing.value == "missing"

    def test_tier_enum_values(self):
        assert LicenseTier.community.value == "community"
        assert LicenseTier.pro.value == "pro"
        assert LicenseTier.enterprise.value == "enterprise"

    def test_license_info_is_frozen(self):
        with pytest.raises(AttributeError):
            COMMUNITY_MODE.status = LicenseStatus.valid  # type: ignore[misc]

    def test_license_info_detail_for_missing(self):
        assert "no license" in COMMUNITY_MODE.detail.lower()

    def test_license_id_parsed(self):
        result = validate_license_key(_PRO_KEY)
        assert result.license_id == "lic_test0001"


# =========================================================================
# SECTION 2: TELEMETRY (10+ tests)
# =========================================================================


@pytest.fixture(autouse=True)
def _reset_telemetry_state():
    """Reset telemetry module state before and after each test."""
    _reset_telemetry()
    yield
    _reset_telemetry()


class TestTelemetryDisabledByDefault:
    """Telemetry is disabled by default."""

    def test_disabled_by_default(self):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.telemetry_enabled = False
            mock_s.sentry_dsn = ""
            assert init_sentry() is False
            assert is_enabled() is False

    def test_disabled_without_dsn(self):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.telemetry_enabled = True
            mock_s.sentry_dsn = ""
            assert init_sentry() is False


class TestTelemetryEnabled:
    """Telemetry enabled with TELEMETRY_ENABLED=true."""

    def test_enabled_with_sentry_sdk(self):
        mock_sentry = MagicMock()
        mock_fastapi_integration = MagicMock()
        mock_logging_integration = MagicMock()

        with patch("sandcastle.config.settings") as mock_s:
            mock_s.telemetry_enabled = True
            mock_s.sentry_dsn = "https://key@sentry.io/123"
            mock_s.sandbox_backend = "local"
            mock_s.data_dir = "/tmp/test"

            with patch.dict(sys.modules, {
                "sentry_sdk": mock_sentry,
                "sentry_sdk.integrations": MagicMock(),
                "sentry_sdk.integrations.fastapi": MagicMock(
                    FastApiIntegration=mock_fastapi_integration
                ),
                "sentry_sdk.integrations.logging": MagicMock(
                    LoggingIntegration=mock_logging_integration
                ),
            }):
                with patch("sandcastle.__version__", "0.27.0"):
                    result = init_sentry()

            assert result is True
            assert is_enabled() is True


class TestBeforeSendStripsAPIKeys:
    """before_send hook strips API keys from events."""

    def test_strips_api_key_from_message(self):
        event = {"message": "Error: api_key=sk-abc123def456 failed"}
        result = _anonymize_event(event, {})
        assert "sk-abc123def456" not in result.get("message", "")

    def test_strips_openai_key_from_exception(self):
        event = {
            "exception": {
                "values": [{
                    "type": "APIError",
                    "value": "Failed with api_key=sk-proj-abcdef12345",
                    "stacktrace": {"frames": []},
                }]
            }
        }
        result = _anonymize_event(event, {})
        exc_val = result["exception"]["values"][0]["value"]
        assert "sk-proj-abcdef12345" not in exc_val

    def test_strips_github_token(self):
        text = "Token ghp_abc123def456ghi789 was leaked"
        assert "ghp_abc123def456ghi789" not in _scrub_text(text)

    def test_strips_gitlab_token(self):
        text = "glpat-FAKETOKEN00000000000 in config"
        assert "glpat-FAKETOKEN00000000000" not in _scrub_text(text)


class TestBeforeSendStripsFilePaths:
    """before_send hook strips absolute file paths."""

    def test_strips_home_from_abs_path(self):
        home = os.path.expanduser("~")
        result = _scrub_path(f"{home}/code/sandcastle/main.py")
        assert result.startswith("~")
        assert home not in result

    def test_scrubs_exception_frame_abs_path(self):
        home = os.path.expanduser("~")
        event = {
            "exception": {
                "values": [{
                    "type": "Error",
                    "value": "fail",
                    "stacktrace": {
                        "frames": [{
                            "abs_path": f"{home}/sandcastle/engine.py",
                        }]
                    },
                }]
            }
        }
        result = _anonymize_event(event, {})
        frame = result["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert home not in frame["abs_path"]
        assert frame["abs_path"].startswith("~")


class TestBeforeSendStripsRequestData:
    """before_send hook strips request data (IP, headers, cookies)."""

    def test_removes_request_entirely(self):
        event = {
            "request": {
                "url": "http://localhost:8080/api/run",
                "headers": {"Authorization": "Bearer secret"},
                "cookies": {"session": "abc123"},
            }
        }
        result = _anonymize_event(event, {})
        assert "request" not in result


class TestBeforeSendStripsUserData:
    """before_send hook strips user data."""

    def test_removes_user_entirely(self):
        event = {
            "user": {
                "ip_address": "192.168.1.1",
                "email": "user@example.com",
                "username": "admin",
            }
        }
        result = _anonymize_event(event, {})
        assert "user" not in result


class TestSensitiveEnvVarNames:
    """Sensitive environment variable names are blocked."""

    def test_anthropic_api_key_in_blocklist(self):
        assert "anthropic_api_key" in _SENSITIVE_ENV_KEYS

    def test_e2b_api_key_in_blocklist(self):
        assert "e2b_api_key" in _SENSITIVE_ENV_KEYS

    def test_sentry_dsn_in_blocklist(self):
        assert "sentry_dsn" in _SENSITIVE_ENV_KEYS

    def test_database_url_in_blocklist(self):
        assert "database_url" in _SENSITIVE_ENV_KEYS

    def test_license_key_in_blocklist(self):
        assert "license_key" in _SENSITIVE_ENV_KEYS

    def test_aws_keys_in_blocklist(self):
        assert "aws_access_key_id" in _SENSITIVE_ENV_KEYS
        assert "aws_secret_access_key" in _SENSITIVE_ENV_KEYS

    def test_tool_secrets_in_blocklist(self):
        assert "tool_slack_bot_token" in _SENSITIVE_ENV_KEYS
        assert "tool_github_token" in _SENSITIVE_ENV_KEYS
        assert "tool_smtp_password" in _SENSITIVE_ENV_KEYS


class TestFailedSendSavesLocally:
    """Failed send saves error report to local JSON file."""

    def test_local_report_created(self, tmp_path):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.data_dir = str(tmp_path)
            event = {
                "event_id": "evt-001",
                "timestamp": "2026-03-26T12:00:00",
                "level": "error",
                "platform": "python",
                "release": "sandcastle@0.27.0",
                "tags": {"step_type": "llm"},
                "contexts": {
                    "sandcastle": {"workflow": "test-wf"},
                    "os": {"name": "macOS"},
                    "runtime": {"name": "CPython"},
                    "browser": {"name": "Chrome"},  # should be excluded
                },
                "exception": {
                    "values": [
                        {"type": "TimeoutError", "value": "Step timed out"}
                    ]
                },
            }
            _save_local_report(event)

        reports = list((tmp_path / "error_reports").glob("*.json"))
        assert len(reports) == 1
        data = json.loads(reports[0].read_text())
        assert data["event_id"] == "evt-001"
        assert data["exceptions"][0]["type"] == "TimeoutError"
        # Only whitelisted context keys are included
        assert "browser" not in data.get("contexts", {})

    def test_local_report_no_crash_on_bad_path(self):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.data_dir = "/nonexistent/impossible/path"
            # Should not raise
            _save_local_report({"event_id": "x"})


class TestSentryDSNValidation:
    """Sentry DSN validation - missing SDK handled gracefully."""

    def test_missing_sentry_sdk(self):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.telemetry_enabled = True
            mock_s.sentry_dsn = "https://key@sentry.io/123"

            orig = getattr(__builtins__, "__import__", __import__)

            def mock_import(name, *args, **kwargs):
                if "sentry_sdk" in name:
                    raise ImportError("no sentry")
                return orig(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                assert init_sentry() is False


class TestAnonymousMode:
    """Anonymous mode - no PII in events."""

    def test_frame_vars_removed(self):
        event = {
            "exception": {
                "values": [{
                    "type": "Error",
                    "value": "fail",
                    "stacktrace": {
                        "frames": [{
                            "vars": {"api_key": "super-secret", "password": "hunter2"},
                            "abs_path": "/usr/lib/python3.12/site.py",
                        }]
                    },
                }]
            }
        }
        result = _anonymize_event(event, {})
        frame = result["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert "vars" not in frame

    def test_bearer_token_scrubbed(self):
        text = "Authorization: Bearer eyJhbGciOi.payload.sig"
        scrubbed = _scrub_text(text)
        assert "eyJhbGciOi" not in scrubbed

    def test_logentry_scrubbed(self):
        event = {"logentry": {"formatted": "secret=slack-FAKETOKEN-000"}}
        result = _anonymize_event(event, {})
        assert "slack-FAKETOKEN-000" not in result["logentry"]["formatted"]

    def test_breadcrumb_messages_scrubbed(self):
        event = {
            "breadcrumbs": {
                "values": [{
                    "message": "Connecting with token=slack-FAKEBOT-000",
                    "data": {"key": "password=hunter2"},
                }]
            }
        }
        result = _anonymize_event(event, {})
        crumb = result["breadcrumbs"]["values"][0]
        assert "slack-FAKEBOT-000" not in crumb["message"]

    def test_normal_text_preserved(self):
        text = "Step 'analyze' failed with timeout after 300s"
        assert _scrub_text(text) == text

    def test_capture_noop_when_disabled(self):
        # All capture functions are no-ops when not initialized
        assert is_enabled() is False
        capture_step_error(
            Exception("test"),
            step_id="s1", step_type="llm", model="sonnet",
        )
        capture_backend_error(
            Exception("test"),
            backend="e2b", operation="create",
        )
        set_workflow_context("wf", "run-1", "e2b")
        # No exceptions raised


class TestScrubPatterns:
    """Verify all sensitive patterns in _SENSITIVE_PATTERNS are effective."""

    def test_password_pattern(self):
        text = "password: my-secret-pass123"
        assert "my-secret-pass123" not in _scrub_text(text)

    def test_credential_pattern(self):
        text = "credential=abc123xyz"
        assert "abc123xyz" not in _scrub_text(text)

    def test_pypi_token(self):
        text = "pypi-AgEIcHlwaS5vcmcCJDY4ZDVhY2Zm"
        assert "pypi-AgEIcHlwaS5vcmcCJDY4ZDVhY2Zm" not in _scrub_text(text)

    def test_slack_token_pattern(self):
        token = "xoxb-FAKE0TEST0TOK-FAKE0TEST0TOKE"
        scrubbed = _scrub_text(f"Slack credential: {token}")
        assert token not in scrubbed
        assert "[REDACTED]" in scrubbed


# =========================================================================
# SECTION 3: OPENTELEMETRY (8+ tests)
# =========================================================================


@pytest.fixture(autouse=True)
def _reset_otel_state():
    """Reset OTEL module state before and after each test."""
    otel_module._reset()
    yield
    otel_module._reset()


def _build_otel_mocks():
    """Build a complete mock hierarchy for the OpenTelemetry SDK."""
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(
        return_value=mock_span
    )
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(
        return_value=False
    )
    mock_trace = MagicMock()
    mock_trace.get_tracer.return_value = mock_tracer

    mock_resource_cls = MagicMock()
    mock_resource_cls.create.return_value = MagicMock()

    mock_provider_cls = MagicMock(return_value=MagicMock())
    mock_exporter_cls = MagicMock(return_value=MagicMock())
    mock_processor_cls = MagicMock(return_value=MagicMock())

    fake_modules = {
        "opentelemetry": MagicMock(trace=mock_trace),
        "opentelemetry.trace": mock_trace,
        "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
        "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
        "opentelemetry.sdk.trace.export": MagicMock(
            BatchSpanProcessor=mock_processor_cls
        ),
        "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
            OTLPSpanExporter=mock_exporter_cls
        ),
    }

    return {
        "span": mock_span,
        "tracer": mock_tracer,
        "trace": mock_trace,
        "fake_modules": fake_modules,
    }


class TestOtelDisabledByDefault:
    """OTEL is disabled by default when deps are not installed."""

    def test_tracer_is_none_initially(self):
        assert otel_module._tracer is None

    def test_init_otel_noop_without_deps(self):
        # Force the opentelemetry import to fail so the no-deps no-op path is
        # exercised whether or not otel is installed in the environment (CI
        # installs the [otel] extra, which would otherwise initialize a real
        # tracer here).
        with patch.dict(sys.modules, {"opentelemetry": None}):
            otel_module.init_otel("sandcastle", "http://localhost:4318")
        assert otel_module._tracer is None

    def test_config_otel_enabled_default_false(self):
        from sandcastle.config import settings
        assert settings.otel_enabled is False


class TestOtelEnabled:
    """OTEL can be enabled with mocked dependencies."""

    def test_enabled_with_mocked_deps(self):
        mocks = _build_otel_mocks()
        with patch.dict(sys.modules, mocks["fake_modules"]):
            otel_module.init_otel("sandcastle", "http://localhost:4318")
        assert otel_module._tracer is not None

    def test_tracer_name_is_sandcastle(self):
        mocks = _build_otel_mocks()
        with patch.dict(sys.modules, mocks["fake_modules"]):
            otel_module.init_otel("sandcastle", "http://localhost:4318")
        mocks["trace"].get_tracer.assert_called_with("sandcastle")


class TestOtelWorkflowSpan:
    """Workflow span creation and attributes."""

    def test_workflow_span_yields_none_without_tracer(self):
        with otel_module.workflow_span("run-1", "test-wf") as span:
            assert span is None

    def test_workflow_span_creates_span_with_tracer(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.workflow_span("run-abc", "data-pipeline") as span:
            assert span is mocks["span"]

    def test_workflow_span_name_includes_workflow_name(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.workflow_span("run-1", "my-workflow"):
            pass

        call_args = mocks["tracer"].start_as_current_span.call_args
        assert call_args[0][0] == "workflow:my-workflow"

    def test_workflow_span_attributes_include_workflow_name(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.workflow_span("run-1", "extract-data"):
            pass

        attrs = mocks["tracer"].start_as_current_span.call_args[1]["attributes"]
        assert attrs["sandcastle.workflow"] == "extract-data"
        assert attrs["sandcastle.run_id"] == "run-1"


class TestOtelStepSpan:
    """Step span creation and attributes."""

    def test_step_span_yields_none_without_tracer(self):
        with otel_module.step_span("run-1", "step-a", "llm") as span:
            assert span is None

    def test_step_span_creates_span_with_tracer(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.step_span("run-1", "step-x", "prompt", model="claude") as span:
            assert span is mocks["span"]

    def test_step_span_attributes_include_step_id(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.step_span("run-1", "analyze-step", "llm"):
            pass

        attrs = mocks["tracer"].start_as_current_span.call_args[1]["attributes"]
        assert attrs["sandcastle.step_id"] == "analyze-step"
        assert attrs["sandcastle.step_type"] == "llm"
        assert attrs["sandcastle.run_id"] == "run-1"

    def test_step_span_name_includes_step_id(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.step_span("run-1", "my-step", "code"):
            pass

        call_name = mocks["tracer"].start_as_current_span.call_args[0][0]
        assert call_name == "step:my-step"

    def test_step_span_includes_model_when_provided(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.step_span("run-1", "s-1", "llm", model="claude-sonnet"):
            pass

        attrs = mocks["tracer"].start_as_current_span.call_args[1]["attributes"]
        assert attrs["sandcastle.model"] == "claude-sonnet"

    def test_step_span_omits_model_when_none(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.step_span("run-1", "s-1", "bash", model=None):
            pass

        attrs = mocks["tracer"].start_as_current_span.call_args[1]["attributes"]
        assert "sandcastle.model" not in attrs


class TestOtelErrorRecording:
    """Error recording in spans via record_step_result."""

    def test_record_noop_with_none_span(self):
        # Should not raise
        otel_module.record_step_result(
            None, "failed", cost=0.0, duration=1.0
        )

    def test_record_sets_status_on_span(self):
        mock_span = MagicMock()
        otel_module.record_step_result(
            mock_span, "failed", cost=0.05, duration=2.5,
            tokens_in=100, tokens_out=50,
        )
        calls = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
        assert calls["sandcastle.status"] == "failed"
        assert calls["sandcastle.cost_usd"] == 0.05
        assert calls["sandcastle.duration_s"] == 2.5
        assert calls["sandcastle.tokens_in"] == 100
        assert calls["sandcastle.tokens_out"] == 50

    def test_record_skips_zero_tokens(self):
        mock_span = MagicMock()
        otel_module.record_step_result(
            mock_span, "completed", cost=0.0, duration=0.1,
            tokens_in=0, tokens_out=0,
        )
        attr_names = [c[0][0] for c in mock_span.set_attribute.call_args_list]
        assert "sandcastle.tokens_in" not in attr_names
        assert "sandcastle.tokens_out" not in attr_names


class TestOtelServiceName:
    """Service name configuration."""

    def test_default_service_name(self):
        from sandcastle.config import settings
        assert settings.otel_service_name == "sandcastle"

    def test_custom_service_name_via_settings(self):
        from sandcastle.config import Settings
        s = Settings(
            otel_enabled=True,
            otel_endpoint="http://collector:4318",
            otel_service_name="my-custom-service",
        )
        assert s.otel_service_name == "my-custom-service"

    def test_init_passes_service_name_to_resource(self):
        mocks = _build_otel_mocks()
        resource_cls = mocks["fake_modules"]["opentelemetry.sdk.resources"].Resource

        with patch.dict(sys.modules, mocks["fake_modules"]):
            otel_module.init_otel("my-service", "http://localhost:4318")

        resource_cls.create.assert_called_once_with(
            {"service.name": "my-service"}
        )

    def test_reset_clears_tracer(self):
        mocks = _build_otel_mocks()
        otel_module._tracer = mocks["tracer"]
        assert otel_module._tracer is not None
        otel_module._reset()
        assert otel_module._tracer is None
