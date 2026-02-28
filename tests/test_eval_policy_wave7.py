"""Wave 7 deep audit: Policy engine and evaluation framework correctness and security.

Covers:
- Policy creation, validation, severity checks
- Pattern matching (regex, keyword, builtin)
- ReDoS protection (heuristic checks and length limits)
- Output redaction (strings, dicts, nested, edge cases)
- Unicode and encoding bypass attempts
- Condition trigger evaluation with limits
- Template resolution safety
- Violation logging
- Eval framework lifecycle (parsing, assertions, suite execution)
- Eval regex_match assertion ReDoS protection
- Credential pattern redaction
- Policy resolver logic
- Concurrent eval execution safety
"""

from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.eval import (
    AssertionDef,
    AssertionResult,
    CaseResult,
    EvalCase,
    EvalSuiteDef,
    SuiteResult,
    _summarize_output,
    check_assertion,
    parse_eval_suite_string,
    run_eval_suite,
)
from sandcastle.engine.policy import (
    BUILTIN_PATTERNS,
    VALID_SEVERITIES,
    PolicyAction,
    PolicyDefinition,
    PolicyEngine,
    PolicyEvalResult,
    PolicyPattern,
    PolicyTrigger,
    PolicyViolation,
    _MAX_EXPRESSION_LENGTH,
    _MAX_REGEX_LENGTH,
    _get_pattern_regex,
    _has_redos_risk,
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
    patterns: list | None = None,
    expression: str | None = None,
    action_type: str = "redact",
    replacement: str = "[REDACTED]",
    severity: str = "medium",
    message: str | None = None,
    approval_config: dict | None = None,
    apply_to: list[str] | None = None,
) -> PolicyDefinition:
    """Helper to create a policy definition for testing."""
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
        ),
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Test: Severity validation
# ---------------------------------------------------------------------------


class TestSeverityValidation:
    """PolicyEngine must reject invalid severity values."""

    def test_valid_severities_accepted(self):
        for sev in VALID_SEVERITIES:
            p = _make_policy(severity=sev, patterns=[{"type": "email"}])
            engine = PolicyEngine([p])
            assert len(engine.policies) == 1

    def test_invalid_severity_rejected(self):
        p = _make_policy(severity="extreme", patterns=[{"type": "email"}])
        with pytest.raises(ValueError, match="Invalid severity"):
            PolicyEngine([p])

    def test_empty_severity_rejected(self):
        p = _make_policy(severity="", patterns=[{"type": "email"}])
        with pytest.raises(ValueError, match="Invalid severity"):
            PolicyEngine([p])

    def test_case_sensitive_severity(self):
        p = _make_policy(severity="HIGH", patterns=[{"type": "email"}])
        with pytest.raises(ValueError, match="Invalid severity"):
            PolicyEngine([p])


# ---------------------------------------------------------------------------
# Test: ReDoS protection
# ---------------------------------------------------------------------------


class TestReDoSProtection:
    """ReDoS detection must catch nested-quantifier patterns."""

    def test_simple_nested_quantifier(self):
        assert _has_redos_risk("(a+)+") is True

    def test_nested_star_plus(self):
        assert _has_redos_risk("(a*)+") is True

    def test_nested_plus_star(self):
        assert _has_redos_risk("(a+)*") is True

    def test_nested_star_star(self):
        assert _has_redos_risk("(.*)*") is True

    def test_nested_with_braces(self):
        assert _has_redos_risk("(a+){2,}") is True

    def test_character_class_nested(self):
        assert _has_redos_risk("([a-z]+)+") is True

    def test_safe_pattern_no_nesting(self):
        assert _has_redos_risk(r"\d{3}-\d{4}") is False

    def test_safe_single_quantifier(self):
        assert _has_redos_risk("a+b*c?") is False

    def test_safe_non_capturing_group(self):
        assert _has_redos_risk("(?:abc)+") is False

    def test_pattern_length_limit(self):
        long_pattern = "a" * (_MAX_REGEX_LENGTH + 1)
        pat = PolicyPattern(type="regex", pattern=long_pattern)
        with pytest.raises(ValueError, match="too long"):
            _get_pattern_regex(pat)

    def test_pattern_at_max_length_ok(self):
        pattern = "a" * _MAX_REGEX_LENGTH
        pat = PolicyPattern(type="regex", pattern=pattern)
        # Should not raise
        result = _get_pattern_regex(pat)
        assert result is not None

    def test_redos_pattern_rejected_in_get_pattern_regex(self):
        pat = PolicyPattern(type="regex", pattern="(a+)+")
        with pytest.raises(ValueError, match="ReDoS"):
            _get_pattern_regex(pat)

    def test_empty_regex_pattern_rejected(self):
        pat = PolicyPattern(type="regex", pattern=None)
        with pytest.raises(ValueError, match="requires a 'pattern' field"):
            _get_pattern_regex(pat)

    def test_unknown_pattern_type_rejected(self):
        pat = PolicyPattern(type="nonexistent_type")
        with pytest.raises(ValueError, match="Unknown pattern type"):
            _get_pattern_regex(pat)


# ---------------------------------------------------------------------------
# Test: Expression length limit
# ---------------------------------------------------------------------------


class TestExpressionLengthLimit:
    """_safe_eval must reject overly long expressions."""

    def test_expression_at_limit(self):
        # Build a valid expression at exactly the limit
        expr = "x > " + "1" * (_MAX_EXPRESSION_LENGTH - 4)
        # Should not raise (may fail eval, but not due to length)
        try:
            _safe_eval(expr, {"x": 5})
        except ValueError:
            pytest.fail("Should not reject expression at max length")
        except Exception:
            pass  # Other eval errors are OK

    def test_expression_over_limit(self):
        expr = "x" * (_MAX_EXPRESSION_LENGTH + 1)
        with pytest.raises(ValueError, match="Expression too long"):
            _safe_eval(expr, {"x": 5})

    def test_normal_expression_works(self):
        result = _safe_eval("step_cost_usd > 0.50", {"step_cost_usd": 0.75})
        assert result is True


# ---------------------------------------------------------------------------
# Test: Pattern matching - builtin types
# ---------------------------------------------------------------------------


class TestBuiltinPatterns:
    """All builtin patterns must be present and functional."""

    def test_builtin_patterns_exist(self):
        for key in ("email", "phone", "ssn", "credit_card"):
            assert key in BUILTIN_PATTERNS

    @pytest.mark.asyncio
    async def test_ssn_detection(self):
        policy = _make_policy(patterns=[{"type": "ssn"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="SSN is 123-45-6789",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert "ssn" in result.violations[0].trigger_details.lower()
        assert "123-45-6789" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_ssn_no_match(self):
        policy = _make_policy(patterns=[{"type": "ssn"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="No SSN here: 12345",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_email_with_plus_addressing(self):
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="Email: user+tag@example.com",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert "user+tag@example.com" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_multiple_pattern_types(self):
        policy = _make_policy(patterns=[{"type": "email"}, {"type": "ssn"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="Contact john@test.com, SSN: 123-45-6789",
            context={"step_id": "test", "run_id": "r1"},
        )
        # Both patterns should trigger a single violation (first match wins)
        assert len(result.violations) == 1
        assert "john@test.com" not in str(result.modified_output)
        assert "123-45-6789" not in str(result.modified_output)


# ---------------------------------------------------------------------------
# Test: Redaction correctness
# ---------------------------------------------------------------------------


class TestRedaction:
    """Redaction must work correctly across data types and preserve structure."""

    @pytest.mark.asyncio
    async def test_redaction_preserves_dict_structure(self):
        policy = _make_policy(
            patterns=[{"type": "email"}],
            replacement="[PII]",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"contact": "user@test.com", "count": 42, "nested": {"key": "val"}},
            context={"step_id": "test", "run_id": "r1"},
        )
        assert isinstance(result.modified_output, dict)
        assert result.modified_output["count"] == 42
        assert result.modified_output["nested"]["key"] == "val"
        assert "[PII]" in result.modified_output["contact"]

    @pytest.mark.asyncio
    async def test_redaction_on_string(self):
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="Contact admin@example.com now",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert isinstance(result.modified_output, str)
        assert "admin@example.com" not in result.modified_output
        assert "[REDACTED]" in result.modified_output

    @pytest.mark.asyncio
    async def test_redaction_multiple_occurrences(self):
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="a@b.com and c@d.com and e@f.com",
            context={"step_id": "test", "run_id": "r1"},
        )
        # All emails should be redacted
        assert "a@b.com" not in result.modified_output
        assert "c@d.com" not in result.modified_output
        assert "e@f.com" not in result.modified_output
        assert result.modified_output.count("[REDACTED]") == 3

    @pytest.mark.asyncio
    async def test_redaction_empty_output(self):
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0
        assert result.modified_output == ""

    @pytest.mark.asyncio
    async def test_redaction_none_output(self):
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output=None,
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_redacted_output_for_storage(self):
        """When apply_to is set, redacted_output is built separately from original."""
        policy = _make_policy(
            patterns=[{"type": "email"}],
            replacement="***",
            apply_to=["storage", "webhook"],
        )
        engine = PolicyEngine([policy])
        original = {"email": "user@example.com", "data": "safe"}
        result = await engine.evaluate(
            step_id="test",
            output=original,
            context={"step_id": "test", "run_id": "r1"},
        )
        assert "user@example.com" not in str(result.redacted_output)
        assert "***" in str(result.redacted_output)

    @pytest.mark.asyncio
    async def test_block_action_also_redacts(self):
        """Block action should redact the matching content from modified_output."""
        policy = _make_policy(
            patterns=[{"type": "regex", "pattern": r"SECRET_[A-Z0-9]{20}"}],
            action_type="block",
            message="Secret found",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="Key: SECRET_ABCDEFGHIJ1234567890",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert result.should_block is True
        assert "SECRET_ABCDEFGHIJ1234567890" not in str(result.modified_output)
        assert "[BLOCKED]" in str(result.modified_output)


# ---------------------------------------------------------------------------
# Test: Unicode and encoding bypass attempts
# ---------------------------------------------------------------------------


class TestUnicodeBypass:
    """Policy engine must handle Unicode content without bypass."""

    @pytest.mark.asyncio
    async def test_email_in_unicode_text(self):
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        # Email within Unicode context
        result = await engine.evaluate(
            step_id="test",
            output="Kontaktujte user@example.com pro informace",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert "user@example.com" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_regex_on_json_encoded_output(self):
        """When output is a dict, it gets JSON-serialized for matching.
        Verify that JSON-encoded strings are still matched."""
        policy = _make_policy(
            patterns=[{"type": "regex", "pattern": r"sk-ant-[A-Za-z0-9]+"}],
            action_type="redact",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"key": "sk-ant-abc123def456"},
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert "sk-ant-abc123def456" not in str(result.modified_output)

    @pytest.mark.asyncio
    async def test_output_with_special_json_chars(self):
        """Redaction must handle outputs with special JSON characters."""
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"msg": 'Quote: "user@test.com" in output'},
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert isinstance(result.modified_output, dict)

    @pytest.mark.asyncio
    async def test_output_with_newlines(self):
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="Line1\nuser@test.com\nLine3",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert "user@test.com" not in result.modified_output


# ---------------------------------------------------------------------------
# Test: Condition triggers
# ---------------------------------------------------------------------------


class TestConditionTriggers:
    """Condition-based triggers with safe expression evaluation."""

    @pytest.mark.asyncio
    async def test_cost_threshold_trigger(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="step_cost_usd > 1.0",
            action_type="alert",
            message="Expensive!",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="ok",
            context={"step_id": "test", "run_id": "r1"},
            step_cost_usd=1.5,
        )
        assert len(result.violations) == 1

    @pytest.mark.asyncio
    async def test_cost_threshold_no_trigger(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="step_cost_usd > 1.0",
            action_type="alert",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="ok",
            context={"step_id": "test", "run_id": "r1"},
            step_cost_usd=0.5,
        )
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_condition_with_output_access(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="output.score < 0.3",
            action_type="log",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"score": 0.1},
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1

    @pytest.mark.asyncio
    async def test_condition_empty_expression(self):
        policy = _make_policy(
            trigger_type="condition",
            expression=None,
            action_type="alert",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="ok",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_condition_invalid_expression_no_crash(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="undefined_var > 10",
            action_type="alert",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="ok",
            context={"step_id": "test", "run_id": "r1"},
        )
        # Should not crash, just no violation
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_condition_boolean_operators(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="step_cost_usd > 0.1 and step_cost_usd < 1.0",
            action_type="alert",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="ok",
            context={"step_id": "test", "run_id": "r1"},
            step_cost_usd=0.5,
        )
        assert len(result.violations) == 1

    @pytest.mark.asyncio
    async def test_unknown_trigger_type(self):
        policy = _make_policy(
            trigger_type="nonexistent",
            action_type="alert",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="ok",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0


# ---------------------------------------------------------------------------
# Test: Template resolution
# ---------------------------------------------------------------------------


class TestTemplateResolution:
    """_resolve_policy_template must safely resolve placeholders."""

    def test_basic_output_resolution(self):
        result = _resolve_policy_template(
            "Score: {output.score}",
            {"score": 0.95},
            {},
        )
        assert result == "Score: 0.95"

    def test_nested_output_resolution(self):
        result = _resolve_policy_template(
            "Name: {output.user.name}",
            {"user": {"name": "Alice"}},
            {},
        )
        assert result == "Name: Alice"

    def test_input_resolution(self):
        result = _resolve_policy_template(
            "Input was: {input.query}",
            {},
            {"input": {"query": "test query"}},
        )
        assert result == "Input was: test query"

    def test_missing_field_returns_placeholder(self):
        result = _resolve_policy_template(
            "Value: {output.missing}",
            {"other": "data"},
            {},
        )
        assert result == "Value: {output.missing}"

    def test_non_dict_output_returns_placeholder(self):
        result = _resolve_policy_template(
            "Field: {output.field}",
            "just a string",
            {},
        )
        assert result == "Field: {output.field}"

    def test_unknown_root_returns_placeholder(self):
        result = _resolve_policy_template(
            "Unknown: {context.key}",
            {"data": "x"},
            {"context": {"key": "val"}},
        )
        # 'context' is not a recognized root, so placeholder stays
        assert result == "Unknown: {context.key}"

    def test_deeply_nested_path_truncated(self):
        """Paths deeper than _max_depth should return the placeholder."""
        deep = ".".join(["output"] + [f"level{i}" for i in range(15)])
        template = f"{{{deep}}}"
        result = _resolve_policy_template(
            template,
            {"level0": {"level1": "data"}},
            {},
        )
        # Should return original placeholder since depth exceeds max
        assert result == template

    def test_large_resolved_value_truncated(self):
        """Very large resolved values must be truncated."""
        huge_value = "x" * 2000
        result = _resolve_policy_template(
            "Value: {output.data}",
            {"data": huge_value},
            {},
        )
        # The resolved portion should be truncated to _max_resolved_len (500)
        assert len(result) < len(huge_value) + 20

    def test_no_placeholders(self):
        result = _resolve_policy_template("Plain text", {}, {})
        assert result == "Plain text"


# ---------------------------------------------------------------------------
# Test: Approval injection
# ---------------------------------------------------------------------------


class TestApprovalInjection:
    """inject_approval action must correctly set approval config."""

    @pytest.mark.asyncio
    async def test_inject_approval_on_condition(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="output.risk == 'high'",
            action_type="inject_approval",
            approval_config={
                "message": "High risk detected in {output.risk}",
                "timeout_hours": 12,
            },
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"risk": "high", "data": "stuff"},
            context={"step_id": "test", "run_id": "r1"},
        )
        assert result.should_inject_approval is True
        assert result.approval_config is not None
        assert "High risk detected" in result.approval_config["message"]
        assert result.approval_config["timeout_hours"] == 12

    @pytest.mark.asyncio
    async def test_no_approval_when_condition_false(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="output.risk == 'high'",
            action_type="inject_approval",
            approval_config={"message": "Review needed"},
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"risk": "low"},
            context={"step_id": "test", "run_id": "r1"},
        )
        assert result.should_inject_approval is False


# ---------------------------------------------------------------------------
# Test: Log and alert actions
# ---------------------------------------------------------------------------


class TestLogAndAlertActions:
    """Log and alert actions must record violations without modifying output."""

    @pytest.mark.asyncio
    async def test_log_action(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="step_cost_usd > 0",
            action_type="log",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"data": "original"},
            context={"step_id": "test", "run_id": "r1"},
            step_cost_usd=0.1,
        )
        assert len(result.violations) == 1
        assert result.violations[0].action_taken == "log"
        # Output should not be modified
        assert result.modified_output == {"data": "original"}

    @pytest.mark.asyncio
    async def test_alert_action(self):
        policy = _make_policy(
            trigger_type="condition",
            expression="step_cost_usd > 0.5",
            action_type="alert",
            message="Cost warning",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="data",
            context={"step_id": "test", "run_id": "r1"},
            step_cost_usd=1.0,
        )
        assert len(result.violations) == 1
        assert result.violations[0].action_taken == "alert"
        assert result.modified_output == "data"  # Unchanged


# ---------------------------------------------------------------------------
# Test: Multiple policies evaluation order
# ---------------------------------------------------------------------------


class TestMultiplePolicies:
    """Multiple policies must evaluate in order and aggregate correctly."""

    @pytest.mark.asyncio
    async def test_redact_then_alert(self):
        policies = [
            _make_policy(
                policy_id="redact-emails",
                patterns=[{"type": "email"}],
                action_type="redact",
            ),
            _make_policy(
                policy_id="cost-alert",
                trigger_type="condition",
                expression="step_cost_usd > 1.0",
                action_type="alert",
                message="Expensive!",
            ),
        ]
        engine = PolicyEngine(policies)
        result = await engine.evaluate(
            step_id="test",
            output={"contact": "a@b.com"},
            context={"step_id": "test", "run_id": "r1"},
            step_cost_usd=1.5,
        )
        assert len(result.violations) == 2
        assert result.violations[0].policy_id == "redact-emails"
        assert result.violations[1].policy_id == "cost-alert"
        assert result.violations[0].output_modified is True
        assert result.violations[1].output_modified is False

    @pytest.mark.asyncio
    async def test_block_stops_after_first_match(self):
        """Block action should be set even if other policies follow."""
        policies = [
            _make_policy(
                policy_id="block-secret",
                patterns=[{"type": "regex", "pattern": r"TOKEN_[A-Z]{20}"}],
                action_type="block",
                message="Secret blocked",
            ),
            _make_policy(
                policy_id="redact-email",
                patterns=[{"type": "email"}],
                action_type="redact",
            ),
        ]
        engine = PolicyEngine(policies)
        result = await engine.evaluate(
            step_id="test",
            output="TOKEN_ABCDEFGHIJKLMNOPQRST and user@test.com",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert result.should_block is True
        # Both policies should still be evaluated
        assert len(result.violations) == 2

    @pytest.mark.asyncio
    async def test_no_policies_no_violations(self):
        engine = PolicyEngine([])
        result = await engine.evaluate(
            step_id="test",
            output="anything",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0
        assert result.modified_output == "anything"


# ---------------------------------------------------------------------------
# Test: Violation dataclass
# ---------------------------------------------------------------------------


class TestPolicyViolation:
    """PolicyViolation dataclass correctness."""

    def test_violation_has_timestamp(self):
        v = PolicyViolation(
            policy_id="test",
            severity="high",
            trigger_details="matched",
            action_taken="redact",
        )
        assert v.timestamp is not None
        assert v.output_modified is False

    def test_violation_output_modified_flag(self):
        v = PolicyViolation(
            policy_id="test",
            severity="medium",
            trigger_details="matched",
            action_taken="redact",
            output_modified=True,
        )
        assert v.output_modified is True


# ---------------------------------------------------------------------------
# Test: PolicyEvalResult
# ---------------------------------------------------------------------------


class TestPolicyEvalResult:
    """PolicyEvalResult dataclass defaults."""

    def test_defaults(self):
        r = PolicyEvalResult()
        assert r.violations == []
        assert r.modified_output is None
        assert r.redacted_output is None
        assert r.should_inject_approval is False
        assert r.approval_config is None
        assert r.should_block is False
        assert r.block_reason is None


# ---------------------------------------------------------------------------
# Test: resolve_step_policies
# ---------------------------------------------------------------------------


class TestResolveStepPolicies:
    """Policy resolution from step-level references."""

    def test_none_returns_all_global(self):
        globals_ = [_make_policy(policy_id="a"), _make_policy(policy_id="b")]
        assert resolve_step_policies(None, globals_) == globals_

    def test_empty_returns_none(self):
        globals_ = [_make_policy(policy_id="a")]
        assert resolve_step_policies([], globals_) == []

    def test_string_references_resolve(self):
        globals_ = [
            _make_policy(policy_id="a"),
            _make_policy(policy_id="b"),
            _make_policy(policy_id="c"),
        ]
        result = resolve_step_policies(["a", "c"], globals_)
        assert len(result) == 2
        assert result[0].id == "a"
        assert result[1].id == "c"

    def test_missing_reference_skipped(self):
        globals_ = [_make_policy(policy_id="a")]
        result = resolve_step_policies(["a", "missing"], globals_)
        assert len(result) == 1
        assert result[0].id == "a"

    def test_inline_policy_passes_through(self):
        inline = _make_policy(policy_id="inline")
        globals_ = [_make_policy(policy_id="global")]
        result = resolve_step_policies([inline], globals_)
        assert len(result) == 1
        assert result[0].id == "inline"

    def test_mixed_string_and_inline(self):
        inline = _make_policy(policy_id="inline")
        globals_ = [_make_policy(policy_id="global")]
        result = resolve_step_policies(["global", inline], globals_)
        assert len(result) == 2
        assert result[0].id == "global"
        assert result[1].id == "inline"


# ---------------------------------------------------------------------------
# Test: Credential policy creation
# ---------------------------------------------------------------------------


class TestCredentialPolicy:
    """create_tool_credential_policy correctness."""

    def test_empty_tools_returns_none(self):
        assert create_tool_credential_policy([]) is None

    def test_known_tool_has_patterns(self):
        policy = create_tool_credential_policy(["slack"])
        assert policy is not None
        assert policy.id == "auto-credential-redact"
        assert policy.action.type == "redact"
        assert policy.action.replacement == "[CREDENTIAL_REDACTED]"
        # Should have Slack-specific patterns + generic Bearer
        assert len(policy.trigger.patterns) >= 2

    def test_unknown_tool_gets_generic_bearer(self):
        policy = create_tool_credential_policy(["custom_tool"])
        assert policy is not None
        # Should have at least the generic Bearer pattern
        assert len(policy.trigger.patterns) >= 1
        bearer_patterns = [
            p for p in policy.trigger.patterns
            if p.pattern and "Bearer" in p.pattern
        ]
        assert len(bearer_patterns) >= 1

    def test_deduplication(self):
        """Duplicate patterns from multiple tools are removed."""
        policy = create_tool_credential_policy(["slack", "slack"])
        assert policy is not None
        # Check no duplicate patterns
        seen = set()
        for p in policy.trigger.patterns:
            key = f"{p.type}:{p.pattern}"
            assert key not in seen, f"Duplicate pattern: {key}"
            seen.add(key)

    def test_tool_name_normalization(self):
        """Tool names with colons should only use prefix."""
        policy = create_tool_credential_policy(["stripe:payments"])
        assert policy is not None
        stripe_patterns = [
            p for p in policy.trigger.patterns
            if p.pattern and "sk_live" in p.pattern
        ]
        assert len(stripe_patterns) >= 1

    @pytest.mark.asyncio
    async def test_credential_policy_redacts_api_key(self):
        """Credential policy should actually redact matching keys."""
        policy = create_tool_credential_policy(["openai"])
        assert policy is not None
        engine = PolicyEngine([policy])
        fake_key = "sk-" + "a" * 48
        result = await engine.evaluate(
            step_id="test",
            output=f"API key is {fake_key}",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert fake_key not in str(result.modified_output)
        assert "[CREDENTIAL_REDACTED]" in str(result.modified_output)


# ---------------------------------------------------------------------------
# Test: Eval framework - YAML parsing
# ---------------------------------------------------------------------------


class TestEvalParsing:
    """Eval suite YAML parsing correctness."""

    def test_parse_basic_suite(self):
        yaml_content = """
workflow: test-workflow
description: "Test suite"
cases:
  - name: "case1"
    input:
      text: "hello"
    assertions:
      - type: not_empty
      - type: contains
        value: "result"
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.workflow == "test-workflow"
        assert suite.description == "Test suite"
        assert len(suite.cases) == 1
        assert len(suite.cases[0].assertions) == 2

    def test_parse_missing_workflow_raises(self):
        with pytest.raises(ValueError, match="workflow"):
            parse_eval_suite_string("description: no workflow\ncases: []")

    def test_parse_non_mapping_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_eval_suite_string("- list item")

    def test_parse_empty_cases(self):
        suite = parse_eval_suite_string("workflow: test\ncases: []")
        assert len(suite.cases) == 0

    def test_parse_concurrency(self):
        yaml_content = """
workflow: test
concurrency: 10
cases:
  - name: case1
    input: {}
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.concurrency == 10

    def test_parse_tags(self):
        yaml_content = """
workflow: test
cases:
  - name: tagged
    input: {}
    tags: [smoke, regression]
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.cases[0].tags == ["smoke", "regression"]

    def test_parse_step_targeted_assertion(self):
        yaml_content = """
workflow: multi
cases:
  - name: step_check
    input: {}
    assertions:
      - type: contains
        value: "enriched"
        step: enrich
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.cases[0].assertions[0].step == "enrich"

    def test_parse_default_concurrency(self):
        suite = parse_eval_suite_string("workflow: test\ncases: []")
        assert suite.concurrency == 1


# ---------------------------------------------------------------------------
# Test: Eval assertions
# ---------------------------------------------------------------------------


class TestEvalAssertions:
    """Individual assertion type checks."""

    @pytest.mark.asyncio
    async def test_contains_pass(self):
        a = AssertionDef(type="contains", value="hello")
        result = await check_assertion(a, "hello world")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_contains_fail(self):
        a = AssertionDef(type="contains", value="xyz")
        result = await check_assertion(a, "hello")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_contains_with_none(self):
        a = AssertionDef(type="contains", value="test")
        result = await check_assertion(a, None)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_not_contains_pass(self):
        a = AssertionDef(type="not_contains", value="bad")
        result = await check_assertion(a, "good content")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_not_contains_fail(self):
        a = AssertionDef(type="not_contains", value="bad")
        result = await check_assertion(a, "this is bad")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_not_empty_with_string(self):
        a = AssertionDef(type="not_empty")
        assert (await check_assertion(a, "content")).passed is True
        assert (await check_assertion(a, "")).passed is False

    @pytest.mark.asyncio
    async def test_not_empty_with_dict(self):
        a = AssertionDef(type="not_empty")
        assert (await check_assertion(a, {"key": "val"})).passed is True
        assert (await check_assertion(a, {})).passed is False

    @pytest.mark.asyncio
    async def test_not_empty_with_none(self):
        a = AssertionDef(type="not_empty")
        assert (await check_assertion(a, None)).passed is False

    @pytest.mark.asyncio
    async def test_equals_pass(self):
        a = AssertionDef(type="equals", value=42)
        assert (await check_assertion(a, 42)).passed is True

    @pytest.mark.asyncio
    async def test_equals_fail(self):
        a = AssertionDef(type="equals", value="expected")
        assert (await check_assertion(a, "actual")).passed is False

    @pytest.mark.asyncio
    async def test_regex_match_pass(self):
        a = AssertionDef(type="regex_match", value=r"\d{3}-\d{4}")
        result = await check_assertion(a, "Phone: 555-1234")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_regex_match_fail(self):
        a = AssertionDef(type="regex_match", value=r"^\d+$")
        result = await check_assertion(a, "not a number")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_regex_match_redos_rejected(self):
        """Eval regex_match must reject ReDoS patterns."""
        a = AssertionDef(type="regex_match", value="(a+)+")
        result = await check_assertion(a, "aaa")
        assert result.passed is False
        assert "ReDoS" in result.message

    @pytest.mark.asyncio
    async def test_regex_match_too_long_rejected(self):
        """Eval regex_match must reject overly long patterns."""
        a = AssertionDef(type="regex_match", value="a" * (_MAX_REGEX_LENGTH + 1))
        result = await check_assertion(a, "test")
        assert result.passed is False
        assert "too long" in result.message

    @pytest.mark.asyncio
    async def test_regex_match_none_value(self):
        a = AssertionDef(type="regex_match", value=None)
        result = await check_assertion(a, "test")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_max_cost_pass(self):
        a = AssertionDef(type="max_cost", value=1.0)
        result = await check_assertion(a, "out", {"cost_usd": 0.5})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_max_cost_fail(self):
        a = AssertionDef(type="max_cost", value=0.05)
        result = await check_assertion(a, "out", {"cost_usd": 0.10})
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_max_duration_pass(self):
        a = AssertionDef(type="max_duration", value=30)
        result = await check_assertion(a, "out", {"duration_seconds": 10.0})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_max_duration_fail(self):
        a = AssertionDef(type="max_duration", value=5)
        result = await check_assertion(a, "out", {"duration_seconds": 10.0})
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_unknown_type(self):
        a = AssertionDef(type="nonexistent")
        result = await check_assertion(a, "out")
        assert result.passed is False
        assert "Unknown" in result.message

    @pytest.mark.asyncio
    async def test_step_targeted_assertion(self):
        a = AssertionDef(type="contains", value="enriched", step="enrich")
        step_outputs = {
            "scrape": "raw",
            "enrich": "enriched data",
        }
        result = await check_assertion(a, "final", step_outputs=step_outputs)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_step_targeted_missing_step(self):
        """When targeted step doesn't exist, falls back to main output."""
        a = AssertionDef(type="contains", value="final", step="missing")
        step_outputs = {"enrich": "enriched"}
        result = await check_assertion(a, "final output", step_outputs=step_outputs)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_contains_truncates_long_actual(self):
        """Contains assertion truncates long actual values in result."""
        a = AssertionDef(type="contains", value="needle")
        long_output = "x" * 300
        result = await check_assertion(a, long_output)
        assert len(result.actual) <= 200


# ---------------------------------------------------------------------------
# Test: Eval suite execution
# ---------------------------------------------------------------------------


class TestEvalSuiteExecution:
    """Eval suite execution with mocked workflow runs."""

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_suite_all_pass(self, mock_run):
        mock_run.return_value = CaseResult(
            name="test",
            passed=True,
            assertions=[AssertionResult(type="not_empty", passed=True)],
            run_id="run-1",
            cost_usd=0.01,
            duration_seconds=1.0,
        )
        suite = EvalSuiteDef(
            workflow="test-wf",
            cases=[
                EvalCase(name="c1", input={}),
                EvalCase(name="c2", input={}),
            ],
        )
        result = await run_eval_suite(suite)
        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0
        assert result.pass_rate == 1.0

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_suite_mixed_results(self, mock_run):
        mock_run.side_effect = [
            CaseResult(name="pass", passed=True, cost_usd=0.01, duration_seconds=1.0),
            CaseResult(name="fail", passed=False, cost_usd=0.02, duration_seconds=2.0, error="err"),
        ]
        suite = EvalSuiteDef(
            workflow="test",
            cases=[
                EvalCase(name="pass", input={}),
                EvalCase(name="fail", input={}),
            ],
        )
        result = await run_eval_suite(suite)
        assert result.total == 2
        assert result.passed == 1
        assert result.failed == 1
        assert result.pass_rate == 0.5

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_suite_exception_handled(self, mock_run):
        mock_run.side_effect = RuntimeError("workflow missing")
        suite = EvalSuiteDef(
            workflow="missing",
            cases=[EvalCase(name="c1", input={})],
        )
        result = await run_eval_suite(suite)
        assert result.total == 1
        assert result.failed == 1
        assert "workflow missing" in result.cases[0].error

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_suite_tag_filter(self, mock_run):
        mock_run.return_value = CaseResult(
            name="smoke", passed=True, cost_usd=0.01, duration_seconds=1.0,
        )
        suite = EvalSuiteDef(
            workflow="test",
            cases=[
                EvalCase(name="smoke1", input={}, tags=["smoke"]),
                EvalCase(name="full1", input={}, tags=["full"]),
                EvalCase(name="both", input={}, tags=["smoke", "full"]),
            ],
        )
        result = await run_eval_suite(suite, tag_filter=["smoke"])
        assert result.total == 2  # smoke1 + both
        assert mock_run.call_count == 2

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_suite_concurrency_control(self, mock_run):
        """Concurrency parameter should be respected."""
        call_count = 0
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def _tracked_run(case, wf):
            nonlocal call_count, max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
                call_count += 1
            return CaseResult(name=case.name, passed=True, cost_usd=0, duration_seconds=0.01)

        mock_run.side_effect = _tracked_run

        suite = EvalSuiteDef(
            workflow="test",
            concurrency=2,
            cases=[
                EvalCase(name=f"c{i}", input={})
                for i in range(6)
            ],
        )
        result = await run_eval_suite(suite)
        assert result.total == 6
        assert result.passed == 6
        assert call_count == 6

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_suite_cost_aggregation(self, mock_run):
        mock_run.side_effect = [
            CaseResult(name="c1", passed=True, cost_usd=0.05, duration_seconds=5.0),
            CaseResult(name="c2", passed=True, cost_usd=0.10, duration_seconds=10.0),
        ]
        suite = EvalSuiteDef(
            workflow="test",
            cases=[
                EvalCase(name="c1", input={}),
                EvalCase(name="c2", input={}),
            ],
        )
        result = await run_eval_suite(suite)
        assert result.total_cost_usd == pytest.approx(0.15)
        assert result.total_duration_seconds == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Test: _summarize_output helper
# ---------------------------------------------------------------------------


class TestSummarizeOutput:
    """Output summarization for storage."""

    def test_none_returns_none(self):
        assert _summarize_output(None) is None

    def test_short_string_unchanged(self):
        assert _summarize_output("short") == "short"

    def test_long_string_truncated(self):
        long_str = "x" * 1000
        result = _summarize_output(long_str)
        assert len(result) == 500

    def test_dict_under_limit(self):
        d = {"key": "value"}
        assert _summarize_output(d) == d

    def test_dict_over_limit(self):
        d = {"key": "x" * 1000}
        result = _summarize_output(d)
        assert isinstance(result, str)
        assert len(result) == 500

    def test_custom_max_len(self):
        result = _summarize_output("x" * 100, max_len=50)
        assert len(result) == 50

    def test_list_converted_to_string(self):
        result = _summarize_output([1, 2, 3])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Test: Eval dataclass defaults
# ---------------------------------------------------------------------------


class TestEvalDataclasses:
    """Eval dataclass defaults and field access."""

    def test_assertion_def_defaults(self):
        a = AssertionDef(type="not_empty")
        assert a.value is None
        assert a.criteria == ""
        assert a.threshold == 0.7
        assert a.step is None

    def test_eval_case_defaults(self):
        c = EvalCase(name="test", input={})
        assert c.assertions == []
        assert c.tags == []

    def test_suite_result_defaults(self):
        s = SuiteResult(suite_name="test", workflow="test")
        assert s.total == 0
        assert s.passed == 0
        assert s.failed == 0
        assert s.pass_rate == 0.0
        assert s.cases == []

    def test_case_result_defaults(self):
        c = CaseResult(name="test", passed=True)
        assert c.assertions == []
        assert c.run_id is None
        assert c.cost_usd == 0.0
        assert c.error is None

    def test_assertion_result_with_score(self):
        r = AssertionResult(type="llm_judge", passed=True, score=0.9)
        assert r.score == 0.9
        assert r.message == ""


# ---------------------------------------------------------------------------
# Test: Deep copy safety
# ---------------------------------------------------------------------------


class TestDeepCopySafety:
    """Verify that policy evaluation doesn't mutate original output."""

    @pytest.mark.asyncio
    async def test_original_output_not_mutated(self):
        policy = _make_policy(
            patterns=[{"type": "email"}],
            replacement="[PII]",
        )
        engine = PolicyEngine([policy])
        original = {"email": "user@test.com", "data": "safe"}
        original_copy = copy.deepcopy(original)

        await engine.evaluate(
            step_id="test",
            output=original,
            context={"step_id": "test", "run_id": "r1"},
        )
        # Original must not be modified
        assert original == original_copy


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_pattern_list(self):
        """Output contains trigger with empty patterns list."""
        policy = _make_policy(
            trigger_type="output_contains",
            patterns=[],  # This will create empty patterns list
        )
        # Empty patterns after filtering means None
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="test data",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_very_large_output(self):
        """Policy engine handles large outputs without crashing."""
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        large_output = "x" * 100_000 + " user@test.com " + "y" * 100_000
        result = await engine.evaluate(
            step_id="test",
            output=large_output,
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1
        assert "user@test.com" not in result.modified_output

    @pytest.mark.asyncio
    async def test_output_is_list(self):
        """Non-dict, non-string output (list) is handled."""
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output=["user@test.com", "data"],
            context={"step_id": "test", "run_id": "r1"},
        )
        # list gets str()'d for matching
        assert len(result.violations) == 1

    @pytest.mark.asyncio
    async def test_output_is_number(self):
        """Numeric output doesn't crash."""
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output=42,
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_output_is_boolean(self):
        policy = _make_policy(patterns=[{"type": "email"}])
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output=True,
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 0

    def test_safe_eval_with_len(self):
        assert _safe_eval("len(output) > 2", {"output": [1, 2, 3]}) is True
        assert _safe_eval("len(output) > 5", {"output": [1, 2, 3]}) is False

    def test_safe_eval_equality_on_string(self):
        assert _safe_eval(
            "output.status == 'error'",
            {"output": {"status": "error"}},
        ) is True

    def test_safe_eval_dot_access(self):
        assert _safe_eval(
            "output.val > 10",
            {"output": {"val": 15}},
        ) is True

    @pytest.mark.asyncio
    async def test_redaction_on_json_decode_error(self):
        """When redaction breaks JSON structure, returns string instead."""
        policy = _make_policy(
            patterns=[{"type": "regex", "pattern": r'"'}],
            replacement="",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"key": "value"},
            context={"step_id": "test", "run_id": "r1"},
        )
        # After removing quotes, JSON decode fails, so result is string
        if result.violations:
            assert isinstance(result.modified_output, str)

    @pytest.mark.asyncio
    async def test_compiled_patterns_cache_used(self):
        """Verify the compiled regex cache is populated and used."""
        policy = _make_policy(
            policy_id="cache-test",
            patterns=[{"type": "email"}],
        )
        engine = PolicyEngine([policy])
        # After init, the cache should have the pattern compiled
        assert len(engine._compiled) > 0

        # Evaluate should use cached version
        result = await engine.evaluate(
            step_id="test",
            output="user@test.com",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1


# ---------------------------------------------------------------------------
# Test: Security scenarios
# ---------------------------------------------------------------------------


class TestSecurityScenarios:
    """Security-focused edge cases."""

    @pytest.mark.asyncio
    async def test_regex_injection_in_pattern(self):
        """Patterns with special regex chars should be handled safely."""
        # This is a valid regex that looks for literal parens
        policy = _make_policy(
            patterns=[{"type": "regex", "pattern": r"\(secret\)"}],
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="Found (secret) here",
            context={"step_id": "test", "run_id": "r1"},
        )
        assert len(result.violations) == 1

    @pytest.mark.asyncio
    async def test_malicious_expression_does_not_execute(self):
        """simpleeval should block dangerous expressions like __import__."""
        policy = _make_policy(
            trigger_type="condition",
            expression="__import__('os').system('echo pwned')",
            action_type="alert",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="data",
            context={"step_id": "test", "run_id": "r1"},
        )
        # Should not crash, and should not trigger (simpleeval blocks it)
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_template_injection_in_output(self):
        """Output data with curly braces should not cause template injection."""
        policy = _make_policy(
            trigger_type="condition",
            expression="step_cost_usd > 0",
            action_type="alert",
            message="Alert: {output.data}",
        )
        engine = PolicyEngine([policy])
        # Output contains a nested template-like string
        result = await engine.evaluate(
            step_id="test",
            output={"data": "{__import__('os').system('id')}"},
            context={"step_id": "test", "run_id": "r1"},
            step_cost_usd=1.0,
        )
        # The violation should record but the template-like content in the
        # resolved value should be treated as plain text
        assert len(result.violations) == 1

    def test_simpleeval_blocks_exec(self):
        """simpleeval must block exec/eval builtins."""
        with pytest.raises(Exception):
            _safe_eval("exec('import os')", {})

    def test_simpleeval_blocks_lambda(self):
        """simpleeval must block lambda expressions."""
        with pytest.raises(Exception):
            _safe_eval("(lambda: 1)()", {})
