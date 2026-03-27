"""Comprehensive deep tests for the Policy Engine.

Tests cover:
  1. Each policy action type (redact, block, inject_approval, alert, log)
  2. All severity levels (critical, high, medium, low)
  3. Credential detection patterns (30+ tool patterns)
  4. Policy evaluation with simpleeval expressions
  5. Policy violation recording
  6. Auto-generated credential policies for tools
  7. Edge cases: malicious expressions, nested objects, very large outputs
  8. ReDoS protection
  9. Template resolution
  10. resolve_step_policies combinations
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from sandcastle.engine.policy import (
    BUILTIN_PATTERNS,
    VALID_SEVERITIES,
    PolicyAction,
    PolicyDefinition,
    PolicyEngine,
    PolicyPattern,
    PolicyTrigger,
    PolicyViolation,
    PolicyEvalResult,
    _CREDENTIAL_PATTERNS,
    _has_redos_risk,
    _get_pattern_regex,
    _resolve_policy_template,
    _safe_eval,
    create_tool_credential_policy,
    resolve_step_policies,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(
    policy_id: str = "test-policy",
    trigger_type: str = "output_contains",
    patterns: list[dict] | None = None,
    expression: str | None = None,
    action_type: str = "redact",
    replacement: str = "[REDACTED]",
    severity: str = "medium",
    message: str | None = None,
    approval_config: dict | None = None,
    apply_to: list[str] | None = None,
    notify: list[str] | None = None,
) -> PolicyDefinition:
    """Helper to create a PolicyDefinition for testing."""
    return PolicyDefinition(
        id=policy_id,
        trigger=PolicyTrigger(
            type=trigger_type,
            patterns=[
                PolicyPattern(type=p.get("type", "regex"), pattern=p.get("pattern"))
                for p in (patterns or [])
            ] if patterns else None,
            expression=expression,
        ),
        action=PolicyAction(
            type=action_type,
            replacement=replacement if action_type == "redact" else None,
            message=message,
            approval_config=approval_config,
            apply_to=apply_to,
            notify=notify,
        ),
        severity=severity,
    )


# ===========================================================================
# 1. Policy action types
# ===========================================================================


class TestRedactAction:
    """Redact action replaces matched content with replacement string."""

    @pytest.mark.asyncio
    async def test_redact_email_in_string(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "email"}],
            action_type="redact",
            replacement="[PII_REMOVED]",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="Email: user@test.com",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert result.violations[0].output_modified is True
        assert "user@test.com" not in str(result.modified_output)
        assert "[PII_REMOVED]" in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_redact_phone_in_dict(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "phone"}],
            action_type="redact",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1",
            output={"phone": "+1-555-123-4567", "name": "Alice"},
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert isinstance(result.modified_output, dict)
        assert "+1-555-123-4567" not in json.dumps(result.modified_output)

    @pytest.mark.asyncio
    async def test_redact_ssn(self) -> None:
        policy = _make_policy(patterns=[{"type": "ssn"}], action_type="redact")
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="SSN: 123-45-6789",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert "123-45-6789" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_redact_credit_card(self) -> None:
        policy = _make_policy(patterns=[{"type": "credit_card"}], action_type="redact")
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="Card: 4111111111111111",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert "4111111111111111" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_redact_with_apply_to_targets(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "email"}],
            action_type="redact",
            apply_to=["storage", "webhook", "output"],
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1",
            output={"email": "test@x.com"},
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.redacted_output is not None
        assert "test@x.com" not in json.dumps(result.redacted_output)

    @pytest.mark.asyncio
    async def test_redact_custom_regex(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "regex", "pattern": r"SECRET_\w+"}],
            action_type="redact",
            replacement="[CENSORED]",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="Key: SECRET_abcdef123",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert "[CENSORED]" in str(result.modified_output)
        assert "SECRET_abcdef123" not in str(result.modified_output)


class TestBlockAction:
    """Block action sets should_block flag and redacts content."""

    @pytest.mark.asyncio
    async def test_block_with_message(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "regex", "pattern": r"sk-[A-Za-z0-9]{48}"}],
            action_type="block",
            message="API key leaked",
        )
        engine = PolicyEngine([policy])
        fake_key = "sk-" + "A" * 48
        result = await engine.evaluate(
            step_id="s1", output=f"Key: {fake_key}",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.should_block is True
        assert result.block_reason == "API key leaked"
        assert fake_key not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_block_default_message(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "regex", "pattern": r"DANGER_\w+"}],
            action_type="block",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="Found DANGER_xyz",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.should_block is True
        assert "Policy violation" in result.block_reason

    @pytest.mark.asyncio
    async def test_block_redacts_output(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "regex", "pattern": r"TOKEN_[a-z]+"}],
            action_type="block",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output={"secret": "TOKEN_abcdef"},
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert "[BLOCKED]" in json.dumps(result.modified_output)


class TestInjectApprovalAction:
    """Inject approval action sets the approval flag and config."""

    @pytest.mark.asyncio
    async def test_inject_approval_sets_flag(self) -> None:
        policy = _make_policy(
            trigger_type="condition",
            expression="output.risk == 'high'",
            action_type="inject_approval",
            approval_config={"message": "High risk step", "timeout_hours": 8},
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1",
            output={"risk": "high", "data": "stuff"},
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.should_inject_approval is True
        assert result.approval_config is not None
        assert "High risk" in result.approval_config.get("message", "")

    @pytest.mark.asyncio
    async def test_inject_approval_not_triggered_on_safe_output(self) -> None:
        policy = _make_policy(
            trigger_type="condition",
            expression="output.risk == 'high'",
            action_type="inject_approval",
            approval_config={"message": "Review needed"},
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1",
            output={"risk": "low", "data": "safe"},
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.should_inject_approval is False

    @pytest.mark.asyncio
    async def test_inject_approval_template_resolution(self) -> None:
        policy = _make_policy(
            trigger_type="condition",
            expression="output.score < 0.3",
            action_type="inject_approval",
            approval_config={"message": "Low score: {output.score}"},
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1",
            output={"score": 0.1},
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.should_inject_approval is True
        assert "0.1" in result.approval_config.get("message", "")


class TestAlertAction:
    """Alert action logs a warning but does not modify output."""

    @pytest.mark.asyncio
    async def test_alert_does_not_modify_output(self) -> None:
        policy = _make_policy(
            trigger_type="condition",
            expression="step_cost_usd > 1.0",
            action_type="alert",
            message="Expensive step detected",
        )
        engine = PolicyEngine([policy])
        original = {"data": "important"}
        result = await engine.evaluate(
            step_id="s1", output=original,
            context={"step_id": "s1", "run_id": "r1"},
            step_cost_usd=2.5,
        )
        assert len(result.violations) == 1
        assert result.violations[0].action_taken == "alert"
        assert result.modified_output == original

    @pytest.mark.asyncio
    async def test_alert_not_triggered_below_threshold(self) -> None:
        policy = _make_policy(
            trigger_type="condition",
            expression="step_cost_usd > 5.0",
            action_type="alert",
            message="Very expensive",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="ok",
            context={"step_id": "s1", "run_id": "r1"},
            step_cost_usd=0.01,
        )
        assert len(result.violations) == 0


class TestLogAction:
    """Log action records the violation without modifying output."""

    @pytest.mark.asyncio
    async def test_log_action_records_violation(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "email"}],
            action_type="log",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="Contact: info@corp.com",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert result.violations[0].action_taken == "log"
        # Output should not be modified by log action
        assert "info@corp.com" in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_log_action_does_not_block(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "ssn"}],
            action_type="log",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="SSN: 123-45-6789",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.should_block is False
        assert result.should_inject_approval is False


# ===========================================================================
# 2. Severity levels
# ===========================================================================


class TestSeverityLevels:
    """Each severity level is accepted and recorded on violations."""

    @pytest.mark.parametrize("severity", ["critical", "high", "medium", "low"])
    @pytest.mark.asyncio
    async def test_severity_accepted(self, severity: str) -> None:
        policy = _make_policy(
            patterns=[{"type": "email"}],
            action_type="redact",
            severity=severity,
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="a@b.com",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert result.violations[0].severity == severity

    def test_invalid_severity_raises(self) -> None:
        policy = _make_policy(severity="extreme")
        with pytest.raises(ValueError, match="Invalid severity"):
            PolicyEngine([policy])

    def test_all_valid_severities_constant(self) -> None:
        assert VALID_SEVERITIES == frozenset({"critical", "high", "medium", "low"})


# ===========================================================================
# 3. Credential detection patterns
# ===========================================================================


class TestCredentialPatterns:
    """Test that credential patterns from _CREDENTIAL_PATTERNS match real tokens."""

    CREDENTIAL_SAMPLES = [
        # Use obviously fake but regex-matching tokens.
        # Push protection won't flag these because they contain NOTREAL/FAKE.
        ("github", "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ("github", "gho_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ("linear", "lin_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ("openai", "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ("anthropic", "sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ("aws", "AKIA0000000000000000"),
        ("figma", "figd_xxxxxxxxxxxxxxxx"),
        ("firecrawl", "fc-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ("resend", "re_xxxxxxxxxxxxxxxxxxxx"),
        ("shopify", "shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ("shopify", "shppa_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ("tavily", "tvly-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
    ]

    @pytest.mark.parametrize("tool,sample", CREDENTIAL_SAMPLES)
    def test_credential_pattern_matches(self, tool: str, sample: str) -> None:
        """Each credential sample must match at least one pattern for its tool."""
        patterns = _CREDENTIAL_PATTERNS.get(tool, [])
        assert patterns, f"No patterns found for tool '{tool}'"
        matched = any(re.search(pat, sample) for pat in patterns)
        assert matched, (
            f"No pattern for '{tool}' matched sample: {sample!r}"
        )

    def test_credential_patterns_cover_major_tools(self) -> None:
        """All major tool types must be present in _CREDENTIAL_PATTERNS."""
        expected_tools = [
            "slack", "discord", "github", "stripe", "openai",
            "anthropic", "aws", "figma", "sendgrid", "twilio",
        ]
        for tool in expected_tools:
            assert tool in _CREDENTIAL_PATTERNS, f"Missing credential pattern for: {tool}"

    def test_credential_patterns_at_least_30(self) -> None:
        """There must be at least 30 tool types with credential patterns."""
        total_tools = len(_CREDENTIAL_PATTERNS)
        assert total_tools >= 30, f"Only {total_tools} tool types, expected >= 30"

    def test_plain_text_does_not_match_credential_patterns(self) -> None:
        """Normal text should not trigger any credential patterns."""
        normal_text = "Hello world, this is a normal sentence about API documentation."
        for tool, patterns in _CREDENTIAL_PATTERNS.items():
            for pat in patterns:
                matches = re.findall(pat, normal_text)
                assert matches == [], (
                    f"Pattern for '{tool}' falsely matched in normal text: {pat}"
                )


# ===========================================================================
# 4. Policy evaluation with simpleeval
# ===========================================================================


class TestSafeEval:
    """Test the _safe_eval function with various expressions."""

    def test_simple_comparison(self) -> None:
        assert _safe_eval("x > 5", {"x": 10}) is True
        assert _safe_eval("x > 5", {"x": 3}) is False

    def test_equality(self) -> None:
        assert _safe_eval("x == 'hello'", {"x": "hello"}) is True
        assert _safe_eval("x == 'hello'", {"x": "world"}) is False

    def test_inequality(self) -> None:
        assert _safe_eval("x != 0", {"x": 1}) is True
        assert _safe_eval("x != 0", {"x": 0}) is False

    def test_boolean_and(self) -> None:
        assert _safe_eval("x > 0 and y > 0", {"x": 1, "y": 2}) is True
        assert _safe_eval("x > 0 and y > 0", {"x": 1, "y": -1}) is False

    def test_boolean_or(self) -> None:
        assert _safe_eval("x > 0 or y > 0", {"x": -1, "y": 2}) is True
        assert _safe_eval("x > 0 or y > 0", {"x": -1, "y": -2}) is False

    def test_boolean_not(self) -> None:
        assert _safe_eval("not x", {"x": False}) is True
        assert _safe_eval("not x", {"x": True}) is False

    def test_len_function(self) -> None:
        assert _safe_eval("len(items) > 2", {"items": [1, 2, 3]}) is True
        assert _safe_eval("len(items) == 0", {"items": []}) is True

    def test_dot_access_on_dict(self) -> None:
        assert _safe_eval(
            "output.status == 'ok'", {"output": {"status": "ok"}}
        ) is True

    def test_nested_dot_access(self) -> None:
        data = {"output": {"result": {"score": 0.95}}}
        assert _safe_eval("output.result.score > 0.9", data) is True

    def test_arithmetic(self) -> None:
        assert _safe_eval("x + y > 10", {"x": 5, "y": 6}) is True
        assert _safe_eval("x * y", {"x": 3, "y": 4}) == 12

    def test_expression_too_long_raises(self) -> None:
        long_expr = "x > " + "0" * 1001
        with pytest.raises(ValueError, match="Expression too long"):
            _safe_eval(long_expr, {"x": 1})

    def test_undefined_variable_raises(self) -> None:
        with pytest.raises(Exception):
            _safe_eval("undefined_var > 10", {})

    @pytest.mark.asyncio
    async def test_eval_error_in_engine_does_not_crash(self) -> None:
        policy = _make_policy(
            trigger_type="condition",
            expression="__import__('os').system('echo hacked')",
            action_type="alert",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="safe",
            context={"step_id": "s1", "run_id": "r1"},
        )
        # Should not trigger (simpleeval blocks __import__)
        assert len(result.violations) == 0


# ===========================================================================
# 5. Policy violation recording
# ===========================================================================


class TestPolicyViolationRecording:
    """Verify violations are recorded correctly."""

    @pytest.mark.asyncio
    async def test_violation_has_correct_policy_id(self) -> None:
        policy = _make_policy(policy_id="my-guard", patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="a@b.com",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations[0].policy_id == "my-guard"

    @pytest.mark.asyncio
    async def test_violation_has_timestamp(self) -> None:
        from datetime import datetime, timezone
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="a@b.com",
            context={"step_id": "s1", "run_id": "r1"},
        )
        ts = result.violations[0].timestamp
        assert isinstance(ts, datetime)
        assert ts.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_violation_records_severity(self) -> None:
        policy = _make_policy(
            patterns=[{"type": "ssn"}], severity="critical",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="SSN: 123-45-6789",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_violation_trigger_details_contain_info(self) -> None:
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="Contact: a@b.com",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert "email" in result.violations[0].trigger_details.lower()

    @pytest.mark.asyncio
    async def test_multiple_violations_all_recorded(self) -> None:
        policies = [
            _make_policy(
                policy_id="email-guard",
                patterns=[{"type": "email"}],
            ),
            _make_policy(
                policy_id="ssn-guard",
                patterns=[{"type": "ssn"}],
            ),
        ]
        engine = PolicyEngine(policies)
        result = await engine.evaluate(
            step_id="s1",
            output="Email: a@b.com SSN: 123-45-6789",
            context={"step_id": "s1", "run_id": "r1"},
        )
        ids = [v.policy_id for v in result.violations]
        assert "email-guard" in ids
        assert "ssn-guard" in ids

    @pytest.mark.asyncio
    async def test_no_violations_when_no_match(self) -> None:
        policy = _make_policy(patterns=[{"type": "ssn"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="No PII here at all.",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations == []
        assert result.should_block is False
        assert result.should_inject_approval is False


# ===========================================================================
# 6. Auto-generated credential policies for tools
# ===========================================================================


class TestCreateToolCredentialPolicy:
    """Test create_tool_credential_policy helper function."""

    def test_empty_tools_returns_none(self) -> None:
        assert create_tool_credential_policy([]) is None

    def test_known_tool_creates_policy(self) -> None:
        policy = create_tool_credential_policy(["slack"])
        assert policy is not None
        assert policy.id == "auto-credential-redact"
        assert policy.severity == "high"
        assert policy.action.type == "redact"
        assert policy.action.replacement == "[CREDENTIAL_REDACTED]"

    def test_policy_has_slack_patterns(self) -> None:
        policy = create_tool_credential_policy(["slack"])
        assert policy is not None
        pattern_types = [p.type for p in policy.trigger.patterns]
        assert all(t == "regex" for t in pattern_types)
        pattern_strings = [p.pattern for p in policy.trigger.patterns]
        # Must include xoxb pattern and Bearer pattern
        assert any("xoxb" in (p or "") for p in pattern_strings)

    def test_multiple_tools_merged(self) -> None:
        policy = create_tool_credential_policy(["slack", "github", "stripe"])
        assert policy is not None
        patterns = policy.trigger.patterns
        pattern_strings = [p.pattern for p in patterns]
        assert any("xoxb" in (p or "") for p in pattern_strings)  # slack
        assert any("ghp_" in (p or "") for p in pattern_strings)  # github
        assert any("sk_live" in (p or "") for p in pattern_strings)  # stripe

    def test_unknown_tool_gets_bearer_only(self) -> None:
        policy = create_tool_credential_policy(["totally_unknown_tool"])
        assert policy is not None
        patterns = policy.trigger.patterns
        assert len(patterns) >= 1
        assert any("Bearer" in (p.pattern or "") for p in patterns)

    def test_tool_name_with_colon_uses_prefix(self) -> None:
        policy = create_tool_credential_policy(["stripe:payments"])
        assert policy is not None
        pattern_strings = [p.pattern for p in policy.trigger.patterns]
        assert any("sk_live" in (p or "") for p in pattern_strings)

    def test_deduplication(self) -> None:
        policy = create_tool_credential_policy(["slack", "slack", "slack"])
        assert policy is not None
        # Slack has 2 patterns + 1 Bearer; with dedup, should not have tripled
        unique_patterns = set()
        for p in policy.trigger.patterns:
            unique_patterns.add(f"{p.type}:{p.pattern}")
        assert len(unique_patterns) == len(policy.trigger.patterns)

    def test_apply_to_covers_all_targets(self) -> None:
        policy = create_tool_credential_policy(["openai"])
        assert policy is not None
        assert set(policy.action.apply_to) == {"storage", "webhook", "output"}

    @pytest.mark.asyncio
    async def test_credential_policy_redacts_token(self) -> None:
        """End-to-end: auto-credential policy redacts a real-looking token."""
        policy = create_tool_credential_policy(["openai"])
        assert policy is not None
        engine = PolicyEngine([policy])
        fake_key = "sk-" + "A" * 48
        result = await engine.evaluate(
            step_id="s1",
            output=f"OpenAI key: {fake_key}",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) >= 1
        assert fake_key not in str(result.modified_output)
        assert "[CREDENTIAL_REDACTED]" in str(result.modified_output)


# ===========================================================================
# 7. Edge cases
# ===========================================================================


class TestEdgeCases:
    """Edge cases: empty output, large output, nested objects, etc."""

    @pytest.mark.asyncio
    async def test_empty_string_output(self) -> None:
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations == []

    @pytest.mark.asyncio
    async def test_none_output(self) -> None:
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output=None,
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations == []

    @pytest.mark.asyncio
    async def test_integer_output(self) -> None:
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output=42,
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations == []

    @pytest.mark.asyncio
    async def test_deeply_nested_dict_output(self) -> None:
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        data = {"a": {"b": {"c": {"d": {"email": "deep@test.com"}}}}}
        result = await engine.evaluate(
            step_id="s1", output=data,
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert "deep@test.com" not in json.dumps(result.modified_output)

    @pytest.mark.asyncio
    async def test_large_output_string(self) -> None:
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        large = "x" * 50000 + " user@test.com " + "y" * 50000
        result = await engine.evaluate(
            step_id="s1", output=large,
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert "user@test.com" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_output_with_special_json_chars(self) -> None:
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        data = {"msg": 'Hello "user@test.com" says the \\ system'}
        result = await engine.evaluate(
            step_id="s1", output=data,
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) == 1

    @pytest.mark.asyncio
    async def test_no_patterns_trigger_never_matches(self) -> None:
        policy = _make_policy(
            trigger_type="output_contains",
            patterns=None,
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="anything",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations == []

    @pytest.mark.asyncio
    async def test_empty_condition_expression(self) -> None:
        policy = _make_policy(trigger_type="condition", expression=None)
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1", output="test",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations == []

    @pytest.mark.asyncio
    async def test_list_output(self) -> None:
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="s1",
            output=["safe text", "email: a@b.com"],
            context={"step_id": "s1", "run_id": "r1"},
        )
        # list is converted to str for matching
        assert len(result.violations) == 1


# ===========================================================================
# 8. ReDoS protection
# ===========================================================================


class TestReDoSProtection:
    """Verify that dangerous regex patterns are rejected."""

    def test_nested_quantifier_rejected(self) -> None:
        assert _has_redos_risk("(a+)+") is True

    def test_star_plus_rejected(self) -> None:
        assert _has_redos_risk("(a*)+") is True

    def test_plus_star_rejected(self) -> None:
        assert _has_redos_risk("(a+)*") is True

    def test_dot_star_star_rejected(self) -> None:
        assert _has_redos_risk("(.*)*") is True

    def test_bracket_class_quantifier_rejected(self) -> None:
        assert _has_redos_risk("([a-z]+)+") is True

    def test_safe_simple_quantifier(self) -> None:
        assert _has_redos_risk(r"\d{3}-\d{4}") is False

    def test_safe_alternation(self) -> None:
        assert _has_redos_risk("(?:abc|def)+") is False

    def test_safe_character_class(self) -> None:
        assert _has_redos_risk("[a-z]+") is False

    def test_pattern_too_long_raises(self) -> None:
        long_pattern = "a" * 501
        pp = PolicyPattern(type="regex", pattern=long_pattern)
        with pytest.raises(ValueError, match="too long"):
            _get_pattern_regex(pp)

    def test_redos_pattern_raises_in_get_pattern_regex(self) -> None:
        pp = PolicyPattern(type="regex", pattern="(a+)+")
        with pytest.raises(ValueError, match="catastrophic backtracking"):
            _get_pattern_regex(pp)

    def test_unknown_pattern_type_raises(self) -> None:
        pp = PolicyPattern(type="nonexistent_type")
        with pytest.raises(ValueError, match="Unknown pattern type"):
            _get_pattern_regex(pp)

    def test_regex_without_pattern_field_raises(self) -> None:
        pp = PolicyPattern(type="regex", pattern=None)
        with pytest.raises(ValueError, match="requires a 'pattern' field"):
            _get_pattern_regex(pp)


# ===========================================================================
# 9. Template resolution
# ===========================================================================


class TestTemplateResolution:
    """Test _resolve_policy_template for {output.field} placeholders."""

    def test_simple_output_field(self) -> None:
        result = _resolve_policy_template(
            "Score: {output.score}", {"score": 0.5}, {}
        )
        assert result == "Score: 0.5"

    def test_nested_output_field(self) -> None:
        result = _resolve_policy_template(
            "Status: {output.result.status}",
            {"result": {"status": "error"}},
            {},
        )
        assert result == "Status: error"

    def test_input_field(self) -> None:
        result = _resolve_policy_template(
            "Input: {input.query}",
            {},
            {"input": {"query": "test_query"}},
        )
        assert result == "Input: test_query"

    def test_unknown_placeholder_preserved(self) -> None:
        result = _resolve_policy_template(
            "Value: {unknown.path}", {}, {}
        )
        assert result == "Value: {unknown.path}"

    def test_missing_nested_key_preserved(self) -> None:
        result = _resolve_policy_template(
            "Val: {output.nonexistent.deep}", {"other": 1}, {}
        )
        assert "{output.nonexistent.deep}" in result

    def test_max_depth_protection(self) -> None:
        # A very deep path should not crash (just return the placeholder)
        deep_path = ".".join(["level"] * 20)
        result = _resolve_policy_template(
            f"Val: {{output.{deep_path}}}", {"level": {}}, {}
        )
        assert "Val:" in result

    def test_long_value_truncated(self) -> None:
        long_val = "x" * 1000
        result = _resolve_policy_template(
            "Data: {output.data}",
            {"data": long_val},
            {},
            _max_resolved_len=100,
        )
        assert len(result) < 200  # truncated

    def test_no_placeholder_passthrough(self) -> None:
        result = _resolve_policy_template("No templates here", {}, {})
        assert result == "No templates here"


# ===========================================================================
# 10. resolve_step_policies
# ===========================================================================


class TestResolveStepPolicies:
    """Test resolve_step_policies helper."""

    def test_none_returns_all_globals(self) -> None:
        g = [_make_policy(policy_id="a"), _make_policy(policy_id="b")]
        assert resolve_step_policies(None, g) == g

    def test_empty_list_returns_nothing(self) -> None:
        g = [_make_policy(policy_id="a")]
        assert resolve_step_policies([], g) == []

    def test_string_ref_resolves(self) -> None:
        g = [_make_policy(policy_id="x"), _make_policy(policy_id="y")]
        result = resolve_step_policies(["x"], g)
        assert len(result) == 1
        assert result[0].id == "x"

    def test_inline_policy_passthrough(self) -> None:
        inline = _make_policy(policy_id="inline-1")
        result = resolve_step_policies([inline], [])
        assert len(result) == 1
        assert result[0].id == "inline-1"

    def test_mixed_refs_and_inline(self) -> None:
        global_pol = _make_policy(policy_id="global-1")
        inline_pol = _make_policy(policy_id="inline-1")
        result = resolve_step_policies(["global-1", inline_pol], [global_pol])
        assert len(result) == 2
        assert result[0].id == "global-1"
        assert result[1].id == "inline-1"

    def test_unknown_ref_skipped(self) -> None:
        g = [_make_policy(policy_id="exists")]
        result = resolve_step_policies(["exists", "nonexistent"], g)
        assert len(result) == 1
        assert result[0].id == "exists"

    def test_duplicate_refs(self) -> None:
        g = [_make_policy(policy_id="a")]
        result = resolve_step_policies(["a", "a"], g)
        assert len(result) == 2  # no dedup - both references resolve


# ===========================================================================
# 11. Built-in pattern types
# ===========================================================================


class TestBuiltinPatterns:
    """Verify all BUILTIN_PATTERNS compile and match expected inputs."""

    def test_email_pattern(self) -> None:
        pp = PolicyPattern(type="email")
        regex = _get_pattern_regex(pp)
        assert regex.search("user@example.com")
        assert not regex.search("not an email")

    def test_phone_pattern(self) -> None:
        pp = PolicyPattern(type="phone")
        regex = _get_pattern_regex(pp)
        assert regex.search("+1-555-123-4567")

    def test_ssn_pattern(self) -> None:
        pp = PolicyPattern(type="ssn")
        regex = _get_pattern_regex(pp)
        assert regex.search("123-45-6789")
        assert not regex.search("12-345-6789")

    def test_credit_card_pattern(self) -> None:
        pp = PolicyPattern(type="credit_card")
        regex = _get_pattern_regex(pp)
        assert regex.search("4111111111111111")


# ===========================================================================
# 12. Multiple policies interaction
# ===========================================================================


class TestMultiplePoliciesInteraction:
    """Test how multiple policies interact during evaluation."""

    @pytest.mark.asyncio
    async def test_redact_then_block(self) -> None:
        policies = [
            _make_policy(
                policy_id="redactor",
                patterns=[{"type": "email"}],
                action_type="redact",
            ),
            _make_policy(
                policy_id="blocker",
                patterns=[{"type": "regex", "pattern": r"DANGER_\w+"}],
                action_type="block",
                message="Danger found",
            ),
        ]
        engine = PolicyEngine(policies)
        result = await engine.evaluate(
            step_id="s1",
            output="Email: a@b.com, also DANGER_xyz",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert len(result.violations) == 2
        assert result.should_block is True
        assert "a@b.com" not in str(result.modified_output)
        assert "DANGER_xyz" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_alert_and_log_dont_modify(self) -> None:
        policies = [
            _make_policy(
                policy_id="alerter",
                trigger_type="condition",
                expression="step_cost_usd > 0.01",
                action_type="alert",
                message="Cost alert",
            ),
            _make_policy(
                policy_id="logger",
                patterns=[{"type": "email"}],
                action_type="log",
            ),
        ]
        engine = PolicyEngine(policies)
        original_output = "Contact: a@b.com"
        result = await engine.evaluate(
            step_id="s1", output=original_output,
            context={"step_id": "s1", "run_id": "r1"},
            step_cost_usd=0.5,
        )
        assert len(result.violations) == 2
        # Neither alert nor log should modify the output
        assert result.modified_output == original_output

    @pytest.mark.asyncio
    async def test_empty_policy_list(self) -> None:
        engine = PolicyEngine([])
        result = await engine.evaluate(
            step_id="s1", output="anything",
            context={"step_id": "s1", "run_id": "r1"},
        )
        assert result.violations == []
        assert result.modified_output == "anything"


# ===========================================================================
# 13. PolicyEvalResult dataclass
# ===========================================================================


class TestPolicyEvalResult:
    """Test the PolicyEvalResult dataclass defaults."""

    def test_default_values(self) -> None:
        result = PolicyEvalResult()
        assert result.violations == []
        assert result.modified_output is None
        assert result.redacted_output is None
        assert result.should_inject_approval is False
        assert result.approval_config is None
        assert result.should_block is False
        assert result.block_reason is None

    def test_violation_default_timestamp(self) -> None:
        from datetime import timezone
        v = PolicyViolation(
            policy_id="test",
            severity="medium",
            trigger_details="test trigger",
            action_taken="log",
        )
        assert v.output_modified is False
        assert v.timestamp.tzinfo == timezone.utc
