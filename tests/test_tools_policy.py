"""Tests for auto credential redaction policy."""

from __future__ import annotations

import pytest

from sandcastle.engine.policy import (
    PolicyEngine,
    create_tool_credential_policy,
)


class TestCreateToolCredentialPolicy:
    """Tests for create_tool_credential_policy()."""

    def test_returns_none_for_empty_tools(self):
        assert create_tool_credential_policy([]) is None

    def test_returns_policy_for_tools(self):
        policy = create_tool_credential_policy(["slack"])
        assert policy is not None
        assert policy.id == "auto-credential-redact"
        assert policy.severity == "high"
        assert policy.action.type == "redact"

    def test_policy_has_patterns(self):
        policy = create_tool_credential_policy(["slack", "github"])
        assert policy is not None
        assert policy.trigger.patterns
        assert len(policy.trigger.patterns) > 0


class TestCredentialRedactionEngine:
    """Tests for the credential redaction policy engine in action."""

    @pytest.mark.asyncio
    async def test_redacts_slack_token(self):
        policy = create_tool_credential_policy(["slack"])
        assert policy is not None
        engine = PolicyEngine([policy])

        result = await engine.evaluate(
            step_id="test",
            output="Here is the token: xoxb-" + "1234567890-abcdefghijklmnop",
            context={"step_id": "test", "run_id": "r1"},
        )

        assert len(result.violations) > 0
        assert "[CREDENTIAL_REDACTED]" in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_redacts_github_token(self):
        policy = create_tool_credential_policy(["github"])
        assert policy is not None
        engine = PolicyEngine([policy])

        result = await engine.evaluate(
            step_id="test",
            output="Token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef",
            context={"step_id": "test", "run_id": "r1"},
        )

        assert len(result.violations) > 0
        assert "ghp_" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_no_redaction_for_clean_output(self):
        policy = create_tool_credential_policy(["slack"])
        assert policy is not None
        engine = PolicyEngine([policy])

        result = await engine.evaluate(
            step_id="test",
            output="Message sent successfully to #general",
            context={"step_id": "test", "run_id": "r1"},
        )

        assert len(result.violations) == 0
        assert result.modified_output == "Message sent successfully to #general"

    @pytest.mark.asyncio
    async def test_redacts_bearer_token(self):
        policy = create_tool_credential_policy(["hubspot"])
        assert policy is not None
        engine = PolicyEngine([policy])

        result = await engine.evaluate(
            step_id="test",
            output="Used Bearer abc123xyz789token for auth",
            context={"step_id": "test", "run_id": "r1"},
        )

        assert len(result.violations) > 0
