"""Policy Engine - evaluates declarative rules against step outputs and executes actions.

Supports triggers (output_contains patterns, condition expressions) and actions
(redact PII, inject approval gates, alert, block secrets).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from simpleeval import simple_eval

logger = logging.getLogger(__name__)


# --- Built-in regex patterns ---

BUILTIN_PATTERNS: dict[str, str] = {
    "email": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    "phone": r"[\+]?[(]?[0-9]{1,4}[)]?[-\s\./0-9]{7,15}",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b(?:\d[ \-]*?){13,19}\b",
}


# --- Dataclasses ---


@dataclass
class PolicyPattern:
    """A pattern to match in step output."""

    type: str  # "email", "phone", "ssn", "credit_card", "regex"
    pattern: str | None = None  # Custom regex (only for type="regex")


@dataclass
class PolicyTrigger:
    """When to evaluate a policy."""

    type: str  # "output_contains", "condition"
    patterns: list[PolicyPattern] | None = None  # For output_contains
    expression: str | None = None  # For condition (safe expression)


@dataclass
class PolicyAction:
    """What to do when a policy triggers."""

    type: str  # "redact", "inject_approval", "alert", "block", "log"
    replacement: str | None = None  # For redact
    apply_to: list[str] | None = None  # For redact: ["storage", "webhook", "output"]
    approval_config: dict | None = None  # For inject_approval
    message: str | None = None  # For alert/block
    notify: list[str] | None = None  # For alert: ["webhook", "log"]


@dataclass
class PolicyDefinition:
    """A single policy rule."""

    id: str
    trigger: PolicyTrigger
    action: PolicyAction
    description: str | None = None
    severity: str = "medium"  # "critical", "high", "medium", "low"


@dataclass
class PolicyViolation:
    """Record of a policy violation."""

    policy_id: str
    severity: str
    trigger_details: str
    action_taken: str
    output_modified: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PolicyEvalResult:
    """Result of evaluating all policies against a step output."""

    violations: list[PolicyViolation] = field(default_factory=list)
    modified_output: Any = None
    redacted_output: Any = None  # Version for storage/webhooks
    should_inject_approval: bool = False
    approval_config: dict | None = None
    should_block: bool = False
    block_reason: str | None = None


# --- PolicyEngine ---


class PolicyEngine:
    """Evaluates policies against step outputs and applies actions."""

    def __init__(self, policies: list[PolicyDefinition]):
        self.policies = policies
        self._compiled: dict[str, re.Pattern] = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compile all regex patterns for performance."""
        for policy in self.policies:
            if policy.trigger.patterns:
                for pattern in policy.trigger.patterns:
                    key = f"{policy.id}:{pattern.type}:{pattern.pattern or ''}"
                    if key not in self._compiled:
                        self._compiled[key] = _get_pattern_regex(pattern)

    async def evaluate(
        self,
        step_id: str,
        output: Any,
        context: dict[str, Any],
        step_cost_usd: float = 0.0,
    ) -> PolicyEvalResult:
        """Evaluate all applicable policies against step output.

        Args:
            step_id: Current step identifier.
            output: Step output (dict or str).
            context: Evaluation context with run_id, step_outputs, etc.
            step_cost_usd: Cost of this step execution.
        """
        violations: list[PolicyViolation] = []
        modified_output = copy.deepcopy(output)
        should_inject_approval = False
        approval_config = None
        should_block = False
        block_reason = None

        # Track which apply_to targets need redaction
        redact_targets: set[str] = set()

        for policy in self.policies:
            matched, details = self._check_trigger(
                policy, modified_output, context, step_cost_usd
            )

            if not matched:
                continue

            violation = PolicyViolation(
                policy_id=policy.id,
                severity=policy.severity,
                trigger_details=details,
                action_taken=policy.action.type,
            )

            if policy.action.type == "redact":
                modified_output = self._apply_redaction(
                    modified_output, policy.trigger.patterns, policy.action
                )
                violation.output_modified = True
                if policy.action.apply_to:
                    redact_targets.update(policy.action.apply_to)

            elif policy.action.type == "inject_approval":
                should_inject_approval = True
                approval_config = policy.action.approval_config
                # Resolve template variables in approval message
                if approval_config and "message" in approval_config:
                    approval_config = dict(approval_config)
                    approval_config["message"] = _resolve_policy_template(
                        approval_config["message"], output, context
                    )

            elif policy.action.type == "block":
                should_block = True
                block_reason = policy.action.message or "Policy violation: output blocked"
                # Redact blocked content so secrets don't persist
                if policy.trigger.patterns:
                    modified_output = self._apply_redaction(
                        modified_output,
                        policy.trigger.patterns,
                        PolicyAction(type="redact", replacement="[BLOCKED]"),
                    )
                    violation.output_modified = True

            elif policy.action.type == "alert":
                msg = policy.action.message or f"Policy '{policy.id}' triggered"
                msg = _resolve_policy_template(msg, output, context)
                logger.warning(f"Policy alert [{policy.severity}]: {msg}")

            elif policy.action.type == "log":
                logger.info(f"Policy log [{policy.id}]: {details}")

            violations.append(violation)

        # Build redacted output for storage/webhooks
        redacted_output = modified_output
        if redact_targets:
            # If any redact policy has apply_to targets, build a separately
            # redacted version from the original output
            redacted_output = copy.deepcopy(output)
            for p in self.policies:
                if p.action.type == "redact" and p.trigger.patterns:
                    redacted_output = self._apply_redaction(
                        redacted_output, p.trigger.patterns, p.action
                    )

        return PolicyEvalResult(
            violations=violations,
            modified_output=modified_output,
            redacted_output=redacted_output,
            should_inject_approval=should_inject_approval,
            approval_config=approval_config,
            should_block=should_block,
            block_reason=block_reason,
        )

    def _check_trigger(
        self,
        policy: PolicyDefinition,
        output: Any,
        context: dict[str, Any],
        step_cost_usd: float,
    ) -> tuple[bool, str]:
        """Check if a policy trigger condition is met."""
        trigger = policy.trigger

        if trigger.type == "output_contains":
            if not trigger.patterns:
                return False, ""
            output_str = json.dumps(output) if isinstance(output, dict) else str(output)
            for pattern in trigger.patterns:
                key = f"{policy.id}:{pattern.type}:{pattern.pattern or ''}"
                regex = self._compiled.get(key) or _get_pattern_regex(pattern)
                matches = regex.findall(output_str)
                if matches:
                    return True, f"Pattern '{pattern.type}' found: {len(matches)} match(es)"
            return False, ""

        elif trigger.type == "condition":
            if not trigger.expression:
                return False, ""
            try:
                result = _safe_eval(
                    trigger.expression,
                    {
                        "output": output,
                        "step_cost_usd": step_cost_usd,
                        "step_id": context.get("step_id", ""),
                        "run_id": context.get("run_id", ""),
                        "total_cost_usd": context.get("total_cost_usd", 0.0),
                    },
                )
                if result:
                    return True, f"Condition '{trigger.expression}' = {result}"
                return False, ""
            except Exception as e:
                logger.warning(f"Policy condition eval error: {e}")
                return False, ""

        return False, ""

    def _apply_redaction(
        self,
        output: Any,
        patterns: list[PolicyPattern] | None,
        action: PolicyAction,
    ) -> Any:
        """Replace all pattern matches with replacement string."""
        if not patterns:
            return output
        replacement = action.replacement or "[REDACTED]"
        output_str = json.dumps(output) if isinstance(output, dict) else str(output)
        for pattern in patterns:
            regex = _get_pattern_regex(pattern)
            output_str = regex.sub(replacement, output_str)
        if isinstance(output, dict):
            try:
                return json.loads(output_str)
            except json.JSONDecodeError:
                return output_str
        return output_str


# --- Helper functions ---


def _get_pattern_regex(pattern: PolicyPattern) -> re.Pattern:
    """Get compiled regex for a pattern type."""
    if pattern.type == "regex":
        if not pattern.pattern:
            raise ValueError("Regex pattern requires a 'pattern' field")
        return re.compile(pattern.pattern)
    elif pattern.type in BUILTIN_PATTERNS:
        return re.compile(BUILTIN_PATTERNS[pattern.type])
    raise ValueError(f"Unknown pattern type: {pattern.type}")


def _safe_eval(expression: str, variables: dict[str, Any]) -> Any:
    """Safely evaluate an expression using simpleeval.

    Supports comparisons, dot access, len(), basic math, and/or/not.
    Never uses Python eval/exec.
    """
    functions = {"len": len}
    return simple_eval(expression, names=variables, functions=functions)


def _resolve_policy_template(template: str, output: Any, context: dict[str, Any]) -> str:
    """Resolve {output.field} and {context.field} placeholders in policy messages."""

    def _replace(match: re.Match) -> str:
        var_path = match.group(1)
        parts = var_path.split(".")
        if parts[0] == "output":
            obj = output
            for part in parts[1:]:
                if isinstance(obj, dict):
                    obj = obj.get(part, match.group(0))
                else:
                    return match.group(0)
            return str(obj)
        elif parts[0] == "input":
            obj = context.get("input", {})
            for part in parts[1:]:
                if isinstance(obj, dict):
                    obj = obj.get(part, match.group(0))
                else:
                    return match.group(0)
            return str(obj)
        return match.group(0)

    return re.sub(r"\{([^}]+)\}", _replace, template)


def resolve_step_policies(
    step_policies: list | None,
    global_policies: list[PolicyDefinition],
) -> list[PolicyDefinition]:
    """Resolve which policies apply to a step.

    - step_policies=None -> all global policies apply
    - step_policies=[] -> no policies apply
    - step_policies=["id1", {inline}] -> referenced + inline policies
    """
    if step_policies is None:
        return global_policies

    if isinstance(step_policies, list) and len(step_policies) == 0:
        return []

    global_map = {p.id: p for p in global_policies}
    result: list[PolicyDefinition] = []

    for item in step_policies:
        if isinstance(item, str):
            # Reference to global policy by ID
            if item in global_map:
                result.append(global_map[item])
            else:
                logger.warning(f"Policy '{item}' not found in global policies")
        elif isinstance(item, PolicyDefinition):
            result.append(item)

    return result
