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


# Valid severity levels for policy definitions
VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
# "webhooks" is accepted alongside "webhook": the privacy router's own default
# config uses the plural, so rejecting it turned a shipped default into a
# configuration error - which, before this was made fail-closed, silently
# disabled every policy on the step.
VALID_REDACTION_TARGETS = frozenset(
    {"output", "outputs", "storage", "webhook", "webhooks", "logs"}
)


class PolicyConfigError(ValueError):
    """A policy definition is malformed and no engine can be built from it."""


# --- Built-in regex patterns ---

BUILTIN_PATTERNS: dict[str, str] = {
    "email": r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
    "phone": r"(?<![0-9])[\+]?[(]?[0-9]{1,4}[)]?[-\s./0-9]{7,15}(?![0-9])",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b(?:\d[ -]*?){13,19}\b",
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
    target_outputs: dict[str, Any] = field(default_factory=dict)
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
        self._validate_policies()
        self._compile_patterns()

    def _validate_policies(self) -> None:
        """Validate policy definitions for correctness."""
        for policy in self.policies:
            if policy.severity not in VALID_SEVERITIES:
                raise PolicyConfigError(
                    f"Invalid severity '{policy.severity}' for policy '{policy.id}'. "
                    f"Must be one of: {', '.join(sorted(VALID_SEVERITIES))}"
                )
            if policy.action.type == "redact" and policy.action.apply_to:
                invalid_targets = (
                    set(policy.action.apply_to) - VALID_REDACTION_TARGETS
                )
                if invalid_targets:
                    raise PolicyConfigError(
                        f"Invalid redaction target(s) for policy '{policy.id}': "
                        f"{', '.join(sorted(invalid_targets))}"
                    )

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
        target_outputs: dict[str, Any] = {}

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
                targets = set(
                    policy.action.apply_to
                    or ("output", "storage", "webhook", "logs")
                )
                if "outputs" in targets:
                    targets.remove("outputs")
                    targets.add("output")
                for target in targets:
                    target_output = target_outputs.get(
                        target, copy.deepcopy(output)
                    )
                    target_outputs[target] = self._apply_redaction(
                        target_output,
                        policy.trigger.patterns,
                        policy.action,
                        policy_id=policy.id,
                    )
                if "output" in targets:
                    modified_output = target_outputs["output"]
                violation.output_modified = True

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
                        policy_id=policy.id,
                    )
                    for target in ("output", "storage", "webhook", "logs"):
                        target_outputs[target] = self._apply_redaction(
                            target_outputs.get(target, copy.deepcopy(output)),
                            policy.trigger.patterns,
                            PolicyAction(
                                type="redact", replacement="[BLOCKED]"
                            ),
                            policy_id=policy.id,
                        )
                    violation.output_modified = True

            elif policy.action.type == "alert":
                msg = policy.action.message or f"Policy '{policy.id}' triggered"
                msg = _resolve_policy_template(msg, output, context)
                logger.warning(f"Policy alert [{policy.severity}]: {msg}")

            elif policy.action.type == "log":
                logger.info(f"Policy log [{policy.id}]: {details}")

            violations.append(violation)

        redacted_output = target_outputs.get(
            "storage",
            target_outputs.get("webhook", modified_output),
        )

        return PolicyEvalResult(
            violations=violations,
            modified_output=modified_output,
            redacted_output=redacted_output,
            target_outputs=target_outputs,
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
        policy_id: str = "",
    ) -> Any:
        """Replace all pattern matches with replacement string."""
        if not patterns:
            return output
        replacement = action.replacement or "[REDACTED]"
        output_str = json.dumps(output) if isinstance(output, dict) else str(output)
        for pattern in patterns:
            # Use the pre-compiled regex from cache when available
            key = f"{policy_id}:{pattern.type}:{pattern.pattern or ''}"
            regex = self._compiled.get(key) or _get_pattern_regex(pattern)
            output_str = regex.sub(replacement, output_str)
        if isinstance(output, dict):
            try:
                return json.loads(output_str)
            except json.JSONDecodeError:
                return output_str
        return output_str


# --- Helper functions ---


# Maximum length for user-supplied regex patterns to limit complexity
_MAX_REGEX_LENGTH = 500

# Maximum length for condition expressions to prevent abuse
_MAX_EXPRESSION_LENGTH = 1000


def _has_redos_risk(pattern: str) -> bool:
    """Heuristic check for catastrophic backtracking (ReDoS) patterns.

    Detects common nested-quantifier constructs such as ``(a+)+``,
    ``(.*)*``, ``(a+)*``, ``([a-z]+)+`` etc. that can cause exponential runtime.
    Also detects alternation-based amplification like ``(a|a)+``.
    """
    # Match a group with an inner quantifier followed by an outer quantifier
    # e.g. (X+)+, (X+)*, (X*)+, (X*){n}, (.+)+, ([a-z]+)+, etc.
    if re.search(r"\([^)]*[+*][^)]*\)[+*{]", pattern):
        return True
    # Detect nested groups with quantifiers: ((a+)b)+
    if re.search(r"\([^)]*\([^)]*[+*]", pattern):
        if re.search(r"\)[^)]*\)[+*{]", pattern):
            return True
    return False


def _get_pattern_regex(pattern: PolicyPattern) -> re.Pattern:
    """Get compiled regex for a pattern type.

    User-supplied patterns (type="regex") are validated for length and
    checked for obvious catastrophic-backtracking constructs before
    compilation.
    """
    if pattern.type == "regex":
        if not pattern.pattern:
            raise ValueError("Regex pattern requires a 'pattern' field")
        if len(pattern.pattern) > _MAX_REGEX_LENGTH:
            raise ValueError(
                f"Regex pattern too long ({len(pattern.pattern)} chars, "
                f"max {_MAX_REGEX_LENGTH})"
            )
        if _has_redos_risk(pattern.pattern):
            raise ValueError(
                "Regex pattern rejected: potential catastrophic backtracking "
                f"(ReDoS) detected in '{pattern.pattern[:80]}...'"
            )
        return re.compile(pattern.pattern)
    elif pattern.type in BUILTIN_PATTERNS:
        return re.compile(BUILTIN_PATTERNS[pattern.type])
    raise ValueError(f"Unknown pattern type: {pattern.type}")


def _safe_eval(expression: str, variables: dict[str, Any]) -> Any:
    """Safely evaluate an expression using simpleeval.

    Supports comparisons, dot access, len(), basic math, and/or/not.
    Never uses Python eval/exec.
    """
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError(
            f"Expression too long ({len(expression)} chars, "
            f"max {_MAX_EXPRESSION_LENGTH})"
        )
    functions = {"len": len}
    return simple_eval(expression, names=variables, functions=functions)


def _resolve_policy_template(
    template: str, output: Any, context: dict[str, Any],
    _max_depth: int = 10,
    _max_resolved_len: int = 500,
) -> str:
    """Resolve {output.field} and {context.field} placeholders in policy messages.

    Args:
        _max_depth: Maximum traversal depth for nested dict access.
        _max_resolved_len: Maximum length for each resolved value string.
    """

    def _replace(match: re.Match) -> str:
        var_path = match.group(1)
        parts = var_path.split(".")
        if len(parts) > _max_depth + 1:
            return match.group(0)
        if parts[0] == "output":
            obj = output
            for part in parts[1:]:
                if isinstance(obj, dict):
                    obj = obj.get(part, match.group(0))
                else:
                    return match.group(0)
            resolved = str(obj)
            return resolved[:_max_resolved_len] if len(resolved) > _max_resolved_len else resolved
        elif parts[0] == "input":
            obj = context.get("input", {})
            for part in parts[1:]:
                if isinstance(obj, dict):
                    obj = obj.get(part, match.group(0))
                else:
                    return match.group(0)
            resolved = str(obj)
            return resolved[:_max_resolved_len] if len(resolved) > _max_resolved_len else resolved
        return match.group(0)

    return re.sub(r"\{([^}]+)\}", _replace, template)


# Common credential patterns for tool connectors
_CREDENTIAL_PATTERNS: dict[str, list[str]] = {
    # Communication
    "slack": [r"xoxb-[0-9A-Za-z\-]+", r"xoxp-[0-9A-Za-z\-]+"],
    "discord": [r"[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}"],
    "twilio": [r"SK[0-9a-f]{32}"],
    "sendgrid": [r"SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}"],
    "resend": [r"re_[A-Za-z0-9]{20,}"],
    "whatsapp": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    "intercom": [r"dG9r[A-Za-z0-9=]+"],
    # Project management
    "github": [r"ghp_[A-Za-z0-9]{20,40}", r"gho_[A-Za-z0-9]{20,40}"],
    "jira": [r"[A-Za-z0-9+/]{24,}={0,2}"],
    "linear": [r"lin_api_[A-Za-z0-9]+"],
    # CRM
    "hubspot": [r"pat-[a-z]{2}\d+-[a-f0-9\-]+"],
    "salesforce": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    "zendesk": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    # Data
    "supabase": [r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"],
    "pinecone": [r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"],
    "airtable": [r"pat[A-Za-z0-9]{14}\.[a-f0-9]{64}"],
    "snowflake": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    "redis": [r"redis://[^@\s]+@"],
    # Payments / ERP
    "stripe": [r"sk_live_[A-Za-z0-9]+", r"sk_test_[A-Za-z0-9]+"],
    "shopify": [r"shpat_[A-Za-z0-9]+", r"shppa_[A-Za-z0-9]+"],
    "plaid": [r"access-sandbox-[a-f0-9\-]+", r"access-production-[a-f0-9\-]+"],
    "quickbooks": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    "docusign": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    # AI
    "openai": [r"sk-[A-Za-z0-9]{48}", r"sk-proj-[A-Za-z0-9\-_]+"],
    "anthropic": [r"sk-ant-[A-Za-z0-9\-_]+"],
    "elevenlabs": [r"[a-f0-9]{32}"],
    "tavily": [r"tvly-[A-Za-z0-9]+"],
    # DevOps / Cloud
    "aws": [r"AKIA[0-9A-Z]{16}"],
    "vercel": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    "datadog": [r"[a-f0-9]{32}"],
    "pagerduty": [r"[A-Za-z0-9_\-]{20}"],
    "cloudflare-workers": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    # Design / Scheduling
    "figma": [r"figd_[A-Za-z0-9_\-]+"],
    "calendly": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    # Services
    "firecrawl": [r"fc-[A-Za-z0-9]+"],
    "servicenow": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
    "sap": [r"Bearer\s+[A-Za-z0-9_\-\.]+"],
}


def create_tool_credential_policy(
    tools: list[str],
) -> PolicyDefinition | None:
    """Create auto-redaction policy for tool credential patterns.

    Returns None if no tools are provided.
    """
    if not tools:
        return None

    patterns: list[PolicyPattern] = []
    for tool in tools:
        tool_key = tool.split(":")[0].lower()
        if tool_key in _CREDENTIAL_PATTERNS:
            for regex in _CREDENTIAL_PATTERNS[tool_key]:
                patterns.append(
                    PolicyPattern(type="regex", pattern=regex)
                )
        # Always add generic Bearer token pattern
        patterns.append(
            PolicyPattern(
                type="regex", pattern=r"Bearer\s+[A-Za-z0-9_\-\.]+"
            )
        )

    # Deduplicate
    seen: set[str] = set()
    unique: list[PolicyPattern] = []
    for p in patterns:
        key = f"{p.type}:{p.pattern}"
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return PolicyDefinition(
        id="auto-credential-redact",
        trigger=PolicyTrigger(
            type="output_contains", patterns=unique
        ),
        action=PolicyAction(
            type="redact",
            replacement="[CREDENTIAL_REDACTED]",
            apply_to=["storage", "webhook", "output"],
        ),
        description="Auto-redact tool credentials from outputs",
        severity="high",
    )


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
        elif all(
            hasattr(item, attr)
            for attr in ("id", "trigger", "action")
        ):
            trigger = item.trigger
            action = item.action
            result.append(
                PolicyDefinition(
                    id=item.id,
                    trigger=PolicyTrigger(
                        type=trigger.type,
                        patterns=(
                            [
                                PolicyPattern(
                                    type=pattern.type,
                                    pattern=pattern.pattern,
                                )
                                for pattern in trigger.patterns
                            ]
                            if trigger.patterns
                            else None
                        ),
                        expression=trigger.expression,
                    ),
                    action=PolicyAction(
                        type=action.type,
                        replacement=action.replacement,
                        apply_to=action.apply_to,
                        approval_config=action.approval_config,
                        message=action.message,
                        notify=action.notify,
                    ),
                    description=getattr(item, "description", None),
                    severity=getattr(item, "severity", "medium"),
                )
            )

    return result
