"""Tests for the Policy Engine."""

from __future__ import annotations

import pytest

from sandcastle.engine.dag import (
    PolicyDefinition,
    parse_yaml_string,
)
from sandcastle.engine.policy import (
    PolicyAction as PEPolicyAction,
)
from sandcastle.engine.policy import (
    PolicyDefinition as PEPolicyDefinition,
)
from sandcastle.engine.policy import (
    PolicyEngine,
    _safe_eval,
    resolve_step_policies,
)
from sandcastle.engine.policy import (
    PolicyPattern as PEPolicyPattern,
)
from sandcastle.engine.policy import (
    PolicyTrigger as PEPolicyTrigger,
)

# --- YAML parsing ---


POLICY_WORKFLOW_YAML = """
name: policy-test
description: Workflow with policies
policies:
  - id: pii-guard
    description: "Redact PII"
    trigger:
      type: output_contains
      patterns:
        - type: email
        - type: phone
    action:
      type: redact
      replacement: "[REDACTED]"
      apply_to: [storage, webhook]
    severity: high

  - id: cost-alert
    description: "Alert on expensive steps"
    trigger:
      type: condition
      expression: "step_cost_usd > 0.50"
    action:
      type: alert
      message: "Step cost ${step_cost_usd}"
    severity: low

  - id: secret-guard
    description: "Block secrets"
    trigger:
      type: output_contains
      patterns:
        - type: regex
          pattern: 'TESTKEY-[a-zA-Z0-9]{20,}'
    action:
      type: block
      message: "Secret detected in output"
    severity: critical

steps:
  - id: scrape
    prompt: "Scrape data"
    policies: [pii-guard, secret-guard]

  - id: analyze
    prompt: "Analyze data"

  - id: safe-step
    prompt: "No policies"
    policies: []
"""


def test_parse_global_policies():
    """Global policies are parsed correctly from YAML."""
    wf = parse_yaml_string(POLICY_WORKFLOW_YAML)
    assert len(wf.policies) == 3

    pii = wf.policies[0]
    assert pii.id == "pii-guard"
    assert pii.trigger.type == "output_contains"
    assert len(pii.trigger.patterns) == 2
    assert pii.trigger.patterns[0].type == "email"
    assert pii.action.type == "redact"
    assert pii.action.replacement == "[REDACTED]"
    assert pii.action.apply_to == ["storage", "webhook"]
    assert pii.severity == "high"

    cost = wf.policies[1]
    assert cost.id == "cost-alert"
    assert cost.trigger.type == "condition"
    assert cost.trigger.expression == "step_cost_usd > 0.50"
    assert cost.action.type == "alert"

    secret = wf.policies[2]
    assert secret.id == "secret-guard"
    assert secret.trigger.type == "output_contains"
    assert len(secret.trigger.patterns) == 1
    assert secret.trigger.patterns[0].type == "regex"
    assert secret.action.type == "block"
    assert secret.severity == "critical"


def test_parse_step_policies():
    """Step-level policy references are parsed."""
    wf = parse_yaml_string(POLICY_WORKFLOW_YAML)

    scrape = wf.get_step("scrape")
    assert scrape.policies == ["pii-guard", "secret-guard"]

    analyze = wf.get_step("analyze")
    assert analyze.policies is None  # All global policies apply

    safe = wf.get_step("safe-step")
    assert safe.policies == []  # No policies apply


INLINE_POLICY_YAML = """
name: inline-test
description: Workflow with inline policy
steps:
  - id: sensitive
    prompt: "Process sensitive data"
    policies:
      - id: custom-check
        trigger:
          type: condition
          expression: "output.risk_level == 'high'"
        action:
          type: inject_approval
          approval_config:
            message: "High risk detected"
"""


def test_parse_inline_step_policy():
    """Inline policy definitions on steps are parsed."""
    wf = parse_yaml_string(INLINE_POLICY_YAML)
    step = wf.get_step("sensitive")
    assert len(step.policies) == 1
    pol = step.policies[0]
    assert isinstance(pol, PolicyDefinition)
    assert pol.id == "custom-check"
    assert pol.trigger.type == "condition"
    assert pol.action.type == "inject_approval"


# --- Safe expression evaluation ---


def test_safe_eval_comparison():
    """Simple comparisons work."""
    assert _safe_eval("step_cost_usd > 0.50", {"step_cost_usd": 0.75}) is True
    assert _safe_eval("step_cost_usd > 0.50", {"step_cost_usd": 0.25}) is False


def test_safe_eval_dot_access():
    """Dot access on nested dicts works."""
    assert _safe_eval(
        "output.confidence_score < 0.5",
        {"output": {"confidence_score": 0.3}},
    ) is True
    assert _safe_eval(
        "output.confidence_score < 0.5",
        {"output": {"confidence_score": 0.8}},
    ) is False


def test_safe_eval_equality():
    """Equality checks work."""
    assert _safe_eval(
        "output.status == 'error'",
        {"output": {"status": "error"}},
    ) is True


def test_safe_eval_len():
    """len() function works."""
    assert _safe_eval("len(output) > 2", {"output": [1, 2, 3]}) is True
    assert _safe_eval("len(output) > 5", {"output": [1, 2, 3]}) is False


def test_safe_eval_boolean():
    """Boolean operators work."""
    assert _safe_eval(
        "step_cost_usd > 0.1 and step_cost_usd < 1.0",
        {"step_cost_usd": 0.5},
    ) is True


# --- PolicyEngine ---


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
) -> PEPolicyDefinition:
    """Helper to create a policy definition for testing."""
    return PEPolicyDefinition(
        id=policy_id,
        trigger=PEPolicyTrigger(
            type=trigger_type,
            patterns=[
                PEPolicyPattern(type=p.get("type", "regex"), pattern=p.get("pattern"))
                for p in (patterns or [])
            ] if patterns else None,
            expression=expression,
        ),
        action=PEPolicyAction(
            type=action_type,
            replacement=replacement if action_type == "redact" else None,
            message=message,
            approval_config=approval_config,
        ),
        severity=severity,
    )


@pytest.mark.asyncio
async def test_pattern_matching_email():
    """Email pattern detection works."""
    policy = _make_policy(
        patterns=[{"type": "email"}],
        action_type="redact",
    )
    engine = PolicyEngine([policy])
    result = await engine.evaluate(
        step_id="test",
        output={"data": "Contact john@example.com for details"},
        context={"step_id": "test", "run_id": "r1"},
    )
    assert len(result.violations) == 1
    assert result.violations[0].policy_id == "test-policy"
    assert "email" in result.violations[0].trigger_details.lower()
    # Email should be redacted
    assert "john@example.com" not in str(result.modified_output)
    assert "[REDACTED]" in str(result.modified_output)


@pytest.mark.asyncio
async def test_pattern_matching_phone():
    """Phone pattern detection works."""
    policy = _make_policy(
        patterns=[{"type": "phone"}],
        action_type="redact",
    )
    engine = PolicyEngine([policy])
    result = await engine.evaluate(
        step_id="test",
        output="Call +1-555-123-4567 for info",
        context={"step_id": "test", "run_id": "r1"},
    )
    assert len(result.violations) == 1


@pytest.mark.asyncio
async def test_pattern_matching_custom_regex():
    """Custom regex pattern detection works."""
    policy = _make_policy(
        patterns=[{"type": "regex", "pattern": r"TESTKEY-[a-zA-Z0-9]{20,}"}],
        action_type="block",
        message="Secret detected",
    )
    engine = PolicyEngine([policy])
    result = await engine.evaluate(
        step_id="test",
        output={"key": "TESTKEY-abcdefghijklmnopqrstuvwxyz"},
        context={"step_id": "test", "run_id": "r1"},
    )
    assert len(result.violations) == 1
    assert result.should_block is True
    assert result.block_reason == "Secret detected"


@pytest.mark.asyncio
async def test_condition_trigger():
    """Condition-based triggers work."""
    policy = _make_policy(
        trigger_type="condition",
        expression="step_cost_usd > 0.50",
        action_type="alert",
        message="Expensive step",
    )
    engine = PolicyEngine([policy])

    # Should trigger
    result = await engine.evaluate(
        step_id="test",
        output={"result": "ok"},
        context={"step_id": "test", "run_id": "r1"},
        step_cost_usd=0.75,
    )
    assert len(result.violations) == 1
    assert result.violations[0].action_taken == "alert"

    # Should not trigger
    result2 = await engine.evaluate(
        step_id="test",
        output={"result": "ok"},
        context={"step_id": "test", "run_id": "r1"},
        step_cost_usd=0.25,
    )
    assert len(result2.violations) == 0


@pytest.mark.asyncio
async def test_redaction_preserves_dict():
    """Redaction on dict output returns a dict."""
    policy = _make_policy(
        patterns=[{"type": "email"}],
        action_type="redact",
        replacement="[PII]",
    )
    engine = PolicyEngine([policy])
    result = await engine.evaluate(
        step_id="test",
        output={"email": "user@test.com", "name": "John"},
        context={"step_id": "test", "run_id": "r1"},
    )
    assert isinstance(result.modified_output, dict)
    assert result.modified_output["name"] == "John"
    assert "[PII]" in result.modified_output["email"]


@pytest.mark.asyncio
async def test_redaction_on_string():
    """Redaction on string output returns a string."""
    policy = _make_policy(
        patterns=[{"type": "email"}],
        action_type="redact",
    )
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
async def test_block_action():
    """Block action sets should_block and redacts output."""
    policy = _make_policy(
        patterns=[{"type": "regex", "pattern": r"FAKEKEY_\w{20,}"}],
        action_type="block",
        message="Secret key detected",
    )
    engine = PolicyEngine([policy])
    result = await engine.evaluate(
        step_id="test",
        output={"key": "FAKEKEY_abcdefghijklmnopqrstuvwxyz1234"},
        context={"step_id": "test", "run_id": "r1"},
    )
    assert result.should_block is True
    assert result.block_reason == "Secret key detected"
    assert "FAKEKEY_" not in str(result.modified_output)


@pytest.mark.asyncio
async def test_inject_approval_action():
    """inject_approval action sets should_inject_approval."""
    policy = _make_policy(
        trigger_type="condition",
        expression="output.confidence < 0.5",
        action_type="inject_approval",
        approval_config={"message": "Low confidence: review needed", "timeout_hours": 24},
    )
    engine = PolicyEngine([policy])
    result = await engine.evaluate(
        step_id="test",
        output={"confidence": 0.3, "data": "stuff"},
        context={"step_id": "test", "run_id": "r1"},
    )
    assert result.should_inject_approval is True
    assert result.approval_config is not None
    assert "Low confidence" in result.approval_config.get("message", "")


@pytest.mark.asyncio
async def test_no_violation_when_no_match():
    """No violations when patterns don't match."""
    policy = _make_policy(
        patterns=[{"type": "email"}],
        action_type="redact",
    )
    engine = PolicyEngine([policy])
    result = await engine.evaluate(
        step_id="test",
        output={"data": "No PII here"},
        context={"step_id": "test", "run_id": "r1"},
    )
    assert len(result.violations) == 0
    assert result.modified_output == {"data": "No PII here"}


@pytest.mark.asyncio
async def test_multiple_policies():
    """Multiple policies are evaluated in order."""
    policies = [
        _make_policy(
            policy_id="email-redact",
            patterns=[{"type": "email"}],
            action_type="redact",
        ),
        _make_policy(
            policy_id="cost-check",
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
    assert result.violations[0].policy_id == "email-redact"
    assert result.violations[1].policy_id == "cost-check"


@pytest.mark.asyncio
async def test_condition_eval_error_does_not_crash():
    """Invalid expression doesn't crash the engine."""
    policy = _make_policy(
        trigger_type="condition",
        expression="nonexistent_var > 10",
        action_type="alert",
    )
    engine = PolicyEngine([policy])
    result = await engine.evaluate(
        step_id="test",
        output={"data": "ok"},
        context={"step_id": "test", "run_id": "r1"},
    )
    # Should not crash, just no violation
    assert len(result.violations) == 0


# --- resolve_step_policies ---


def test_resolve_step_policies_none():
    """None means all global policies apply."""
    globals_ = [
        _make_policy(policy_id="a"),
        _make_policy(policy_id="b"),
    ]
    assert resolve_step_policies(None, globals_) == globals_


def test_resolve_step_policies_empty():
    """Empty list means no policies apply."""
    globals_ = [_make_policy(policy_id="a")]
    assert resolve_step_policies([], globals_) == []


def test_resolve_step_policies_by_id():
    """String references resolve to global policies."""
    globals_ = [
        _make_policy(policy_id="a"),
        _make_policy(policy_id="b"),
        _make_policy(policy_id="c"),
    ]
    result = resolve_step_policies(["a", "c"], globals_)
    assert len(result) == 2
    assert result[0].id == "a"
    assert result[1].id == "c"


def test_resolve_step_policies_inline():
    """Inline PolicyDefinition objects pass through."""
    inline = _make_policy(policy_id="inline")
    globals_ = [_make_policy(policy_id="global")]
    result = resolve_step_policies([inline], globals_)
    assert len(result) == 1
    assert result[0].id == "inline"
