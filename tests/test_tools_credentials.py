"""Tests for the Tool Credentials resolver."""

from __future__ import annotations

import os

import pytest

from sandcastle.engine.tools.credentials import (
    CREDENTIAL_PATTERNS,
    get_tool_credentials,
    mask_credential,
    validate_tool_credentials,
)


class TestGetToolCredentials:
    """Tests for get_tool_credentials()."""

    def test_returns_set_credentials(self, monkeypatch):
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "xoxb-test-token")
        creds = get_tool_credentials(["slack"])
        assert "TOOL_SLACK_BOT_TOKEN" in creds
        assert creds["TOOL_SLACK_BOT_TOKEN"] == "xoxb-test-token"

    def test_omits_unset_credentials(self, monkeypatch):
        monkeypatch.delenv("TOOL_SLACK_BOT_TOKEN", raising=False)
        creds = get_tool_credentials(["slack"])
        assert "TOOL_SLACK_BOT_TOKEN" not in creds

    def test_unknown_tool_skipped(self):
        creds = get_tool_credentials(["nonexistent_tool"])
        assert creds == {}

    def test_multiple_tools(self, monkeypatch):
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "xoxb-123")
        monkeypatch.setenv("TOOL_GITHUB_TOKEN", "ghp_abc")
        creds = get_tool_credentials(["slack", "github"])
        assert "TOOL_SLACK_BOT_TOKEN" in creds
        assert "TOOL_GITHUB_TOKEN" in creds

    def test_webhook_has_no_default_creds(self):
        creds = get_tool_credentials(["webhook"])
        assert creds == {}

    def test_partial_credentials(self, monkeypatch):
        monkeypatch.setenv("TOOL_JIRA_API_TOKEN", "token123")
        monkeypatch.delenv("TOOL_JIRA_BASE_URL", raising=False)
        monkeypatch.delenv("TOOL_JIRA_EMAIL", raising=False)
        creds = get_tool_credentials(["jira"])
        assert "TOOL_JIRA_API_TOKEN" in creds
        assert "TOOL_JIRA_BASE_URL" not in creds


class TestValidateToolCredentials:
    """Tests for validate_tool_credentials()."""

    def test_configured_tool(self, monkeypatch):
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "xoxb-test")
        result = validate_tool_credentials(["slack"])
        assert result["slack"]["configured"] is True
        assert result["slack"]["missing"] == []
        assert "TOOL_SLACK_BOT_TOKEN" in result["slack"]["present"]

    def test_unconfigured_tool(self, monkeypatch):
        monkeypatch.delenv("TOOL_SLACK_BOT_TOKEN", raising=False)
        result = validate_tool_credentials(["slack"])
        assert result["slack"]["configured"] is False
        assert "TOOL_SLACK_BOT_TOKEN" in result["slack"]["missing"]

    def test_unknown_tool(self):
        result = validate_tool_credentials(["nonexistent"])
        assert result["nonexistent"]["configured"] is False
        assert "error" in result["nonexistent"]

    def test_webhook_no_creds_required(self):
        # webhook has no credential_env_vars - should report not configured
        # since len(credential_env_vars) == 0
        result = validate_tool_credentials(["webhook"])
        assert result["webhook"]["configured"] is False


class TestMaskCredential:
    """Tests for mask_credential()."""

    def test_long_value(self):
        masked = mask_credential("xoxb-1234567890-abcdefgh")
        assert masked.startswith("xoxb")
        assert masked.endswith("fgh")
        assert "..." in masked
        assert len(masked) < len("xoxb-1234567890-abcdefgh")

    def test_short_value(self):
        masked = mask_credential("abc")
        assert masked == "***"

    def test_exactly_8_chars(self):
        masked = mask_credential("12345678")
        assert masked == "********"

    def test_9_chars(self):
        masked = mask_credential("123456789")
        assert masked == "1234...6789"


class TestCredentialPatterns:
    """Tests for CREDENTIAL_PATTERNS regex patterns."""

    def test_patterns_list_not_empty(self):
        assert len(CREDENTIAL_PATTERNS) > 0

    def test_all_patterns_are_strings(self):
        for p in CREDENTIAL_PATTERNS:
            assert isinstance(p, str)

    def test_patterns_compile(self):
        import re
        for p in CREDENTIAL_PATTERNS:
            re.compile(p)  # Should not raise
