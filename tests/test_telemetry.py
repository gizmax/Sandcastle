"""Tests for opt-in Sentry error reporting / telemetry module."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from sandcastle.engine.telemetry import (
    _anonymize_event,
    _reset,
    _save_local_report,
    _scrub_path,
    _scrub_text,
    capture_backend_error,
    capture_step_error,
    init_sentry,
    is_enabled,
    set_workflow_context,
)


@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Reset telemetry state before each test."""
    _reset()
    yield
    _reset()


# --- init_sentry ---


class TestInitSentry:
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

    def test_import_error_graceful(self):
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


# --- _scrub_text ---


class TestScrubText:
    def test_strips_api_key(self):
        text = "Error: api_key=sk-abc123def456 failed"
        assert "sk-abc123def456" not in _scrub_text(text)

    def test_strips_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOi.payload.sig"
        assert "eyJhbGciOi" not in _scrub_text(text)

    def test_strips_secret_pattern(self):
        text = "secret: my-super-secret-value"
        assert "my-super-secret-value" not in _scrub_text(text)

    def test_strips_github_token(self):
        text = "Token ghp_abc123def456ghi789"
        assert "ghp_abc123def456ghi789" not in _scrub_text(text)

    def test_strips_pypi_token(self):
        text = "pypi-AgEIcHlwaS5vcmcCJDY4ZDVhY2Zm"
        assert "pypi-AgEIcHlwaS5vcmcCJDY4ZDVhY2Zm" not in _scrub_text(text)

    def test_preserves_normal_text(self):
        text = "Step 'analyze' failed with timeout after 300s"
        assert _scrub_text(text) == text

    def test_strips_slack_token(self):
        text = "token=xoxb-123456789-abcdefgh"
        assert "xoxb-123456789" not in _scrub_text(text)


# --- _scrub_path ---


class TestScrubPath:
    def test_strips_home_dir(self):
        home = os.path.expanduser("~")
        result = _scrub_path(f"{home}/projects/sandcastle/main.py")
        assert result.startswith("~")
        assert home not in result

    def test_preserves_relative(self):
        assert _scrub_path("src/sandcastle/main.py") == "src/sandcastle/main.py"


# --- _anonymize_event ---


class TestAnonymizeEvent:
    def test_removes_request(self):
        event = {
            "request": {"url": "http://localhost", "headers": {"auth": "s"}},
        }
        result = _anonymize_event(event, {})
        assert "request" not in result

    def test_removes_user(self):
        event = {"user": {"ip": "1.2.3.4", "email": "test@example.com"}}
        result = _anonymize_event(event, {})
        assert "user" not in result

    def test_scrubs_exception_values(self):
        event = {
            "exception": {
                "values": [{
                    "type": "ConnectionError",
                    "value": "Failed with api_key=sk-secret123",
                    "stacktrace": {"frames": []},
                }]
            }
        }
        result = _anonymize_event(event, {})
        exc_val = result["exception"]["values"][0]["value"]
        assert "sk-secret123" not in exc_val

    def test_removes_frame_vars(self):
        event = {
            "exception": {
                "values": [{
                    "type": "Error",
                    "value": "fail",
                    "stacktrace": {
                        "frames": [{
                            "vars": {"api_key": "secret"},
                            "abs_path": "/usr/local/lib/python3.12/site.py",
                        }]
                    },
                }]
            }
        }
        result = _anonymize_event(event, {})
        frame = result["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert "vars" not in frame

    def test_scrubs_breadcrumbs(self):
        event = {
            "breadcrumbs": {
                "values": [{
                    "message": "Connecting with token=xoxb-secret",
                    "data": {"key": "password=hunter2"},
                }]
            }
        }
        result = _anonymize_event(event, {})
        crumb = result["breadcrumbs"]["values"][0]
        assert "xoxb-secret" not in crumb["message"]

    def test_scrubs_message_string(self):
        event = {"message": "Error: api_key=sk-12345 invalid"}
        result = _anonymize_event(event, {})
        assert "sk-12345" not in result["message"]

    def test_scrubs_logentry(self):
        event = {"logentry": {"formatted": "secret=xoxp-abc123"}}
        result = _anonymize_event(event, {})
        assert "xoxp-abc123" not in result["logentry"]["formatted"]


# --- _save_local_report ---


class TestSaveLocalReport:
    def test_writes_json(self, tmp_path):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.data_dir = str(tmp_path)
            event = {
                "event_id": "abc123",
                "timestamp": "2026-02-25T10:00:00",
                "level": "error",
                "platform": "python",
                "release": "sandcastle@0.15.0",
                "tags": {"step_type": "llm"},
                "contexts": {
                    "sandcastle": {"workflow": "test"},
                    "os": {"name": "macOS"},
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
            assert data["event_id"] == "abc123"
            assert data["exceptions"][0]["type"] == "TimeoutError"

    def test_no_crash_on_error(self, tmp_path):
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.data_dir = "/nonexistent/impossible/path"
            # Should not raise
            _save_local_report({"event_id": "x"})


# --- capture functions (no-op when disabled) ---


class TestCaptureNoOp:
    def test_step_error_noop(self):
        capture_step_error(
            Exception("test"),
            step_id="step1",
            step_type="llm",
            model="sonnet",
        )

    def test_backend_error_noop(self):
        capture_backend_error(
            Exception("test"),
            backend="e2b",
            operation="create_sandbox",
        )

    def test_set_context_noop(self):
        set_workflow_context("wf", "run-1", "e2b")


# --- is_enabled ---


class TestIsEnabled:
    def test_false_by_default(self):
        assert is_enabled() is False
