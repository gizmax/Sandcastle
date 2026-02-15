"""DAG parser and dependency resolver for workflow definitions."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RetryConfig:
    """Retry configuration for a step."""

    max_attempts: int = 3
    backoff: str = "exponential"  # "exponential" | "fixed"
    on_failure: str = "abort"  # "skip" | "abort" | "fallback"


@dataclass
class FallbackConfig:
    """Fallback configuration for a step."""

    prompt: str = ""
    model: str = "haiku"


@dataclass
class CompletionConfig:
    """Workflow completion configuration."""

    webhook: str | None = None
    storage_path: str | None = None


@dataclass
class FailureConfig:
    """Workflow failure configuration."""

    dead_letter: bool = False
    webhook: str | None = None


@dataclass
class ApprovalConfig:
    """Configuration for an approval gate step."""

    message: str = ""
    show_data: str | None = None  # Variable path to resolve and show reviewer
    timeout_hours: float | None = None
    on_timeout: str = "abort"  # "abort" | "skip"
    allow_edit: bool = False


@dataclass
class VariantConfig:
    """Configuration for an AutoPilot experiment variant."""

    id: str = ""
    model: str | None = None
    prompt: str | None = None
    max_turns: int | None = None


@dataclass
class EvaluationConfig:
    """How to evaluate AutoPilot experiment results."""

    method: str = "llm_judge"  # "llm_judge" | "schema_completeness" | "custom"
    criteria: str = ""


@dataclass
class AutoPilotConfig:
    """Configuration for self-optimizing step experiments."""

    enabled: bool = False
    optimize_for: str = "quality"  # "quality" | "cost" | "latency" | "pareto"
    variants: list[VariantConfig] = field(default_factory=list)
    evaluation: EvaluationConfig | None = None
    sample_rate: float = 1.0  # 0.0-1.0 fraction of runs to sample
    min_samples: int = 10
    auto_deploy: bool = True
    quality_threshold: float = 0.7  # Minimum quality score to consider


@dataclass
class SubWorkflowConfig:
    """Configuration for a sub-workflow (workflow-as-step)."""

    workflow: str = ""  # Workflow file name
    input_mapping: dict[str, str] = field(default_factory=dict)
    output_mapping: dict[str, str] = field(default_factory=dict)
    parallel_over: str | None = None  # Fan-out over this variable
    max_concurrent: int = 5
    timeout: int = 600


@dataclass
class StepDefinition:
    """Definition of a single workflow step."""

    id: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    model: str = "sonnet"
    max_turns: int = 10
    timeout: int = 300
    parallel_over: str | None = None
    output_schema: dict | None = None
    retry: RetryConfig | None = None
    fallback: FallbackConfig | None = None
    type: str = "standard"  # "standard" | "approval" | "sub_workflow"
    approval_config: ApprovalConfig | None = None
    autopilot: AutoPilotConfig | None = None
    sub_workflow: SubWorkflowConfig | None = None


@dataclass
class WorkflowDefinition:
    """Full workflow definition parsed from YAML."""

    name: str
    description: str
    sandstorm_url: str
    default_model: str
    default_max_turns: int
    default_timeout: int
    steps: list[StepDefinition]
    on_complete: CompletionConfig | None = None
    on_failure: FailureConfig | None = None
    schedule: str | None = None

    def get_step(self, step_id: str) -> StepDefinition:
        """Get a step by its ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        raise ValueError(f"Step '{step_id}' not found in workflow '{self.name}'")


@dataclass
class ExecutionPlan:
    """Topologically sorted execution stages."""

    stages: list[list[str]]  # e.g. [["scrape"], ["enrich"], ["score"]]


def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} patterns with environment variable values."""

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return re.sub(r"\$\{(\w+)\}", _replace, value)


def _parse_retry(data: dict | None) -> RetryConfig | None:
    """Parse retry configuration from YAML data."""
    if data is None:
        return None
    return RetryConfig(
        max_attempts=data.get("max_attempts", 3),
        backoff=data.get("backoff", "exponential"),
        on_failure=data.get("on_failure", "abort"),
    )


def _parse_fallback(data: dict | None) -> FallbackConfig | None:
    """Parse fallback configuration from YAML data."""
    if data is None:
        return None
    return FallbackConfig(
        prompt=data.get("prompt", ""),
        model=data.get("model", "haiku"),
    )


def _parse_approval_config(data: dict | None) -> ApprovalConfig | None:
    """Parse approval configuration from YAML data."""
    if data is None:
        return None
    return ApprovalConfig(
        message=data.get("message", ""),
        show_data=data.get("show_data"),
        timeout_hours=data.get("timeout_hours"),
        on_timeout=data.get("on_timeout", "abort"),
        allow_edit=data.get("allow_edit", False),
    )


def _parse_autopilot_config(data: dict | None) -> AutoPilotConfig | None:
    """Parse autopilot configuration from YAML data."""
    if data is None:
        return None
    variants = []
    for v in data.get("variants", []):
        variants.append(VariantConfig(
            id=v.get("id", ""),
            model=v.get("model"),
            prompt=v.get("prompt"),
            max_turns=v.get("max_turns"),
        ))

    eval_data = data.get("evaluation")
    evaluation = None
    if eval_data:
        evaluation = EvaluationConfig(
            method=eval_data.get("method", "llm_judge"),
            criteria=eval_data.get("criteria", ""),
        )

    return AutoPilotConfig(
        enabled=data.get("enabled", False),
        optimize_for=data.get("optimize_for", "quality"),
        variants=variants,
        evaluation=evaluation,
        sample_rate=data.get("sample_rate", 1.0),
        min_samples=data.get("min_samples", 10),
        auto_deploy=data.get("auto_deploy", True),
        quality_threshold=data.get("quality_threshold", 0.7),
    )


def _parse_sub_workflow_config(data: dict | None) -> SubWorkflowConfig | None:
    """Parse sub-workflow configuration from YAML data."""
    if data is None:
        return None
    return SubWorkflowConfig(
        workflow=data.get("workflow", ""),
        input_mapping=data.get("input_mapping", {}),
        output_mapping=data.get("output_mapping", {}),
        parallel_over=data.get("parallel_over"),
        max_concurrent=data.get("max_concurrent", 5),
        timeout=data.get("timeout", 600),
    )


def _parse_step(data: dict, defaults: dict) -> StepDefinition:
    """Parse a single step definition from YAML data."""
    step_type = data.get("type", "standard")
    # Approval steps don't require a prompt - use message as fallback
    prompt = data.get("prompt", "")
    if step_type == "approval" and not prompt:
        ac = data.get("approval_config", {})
        prompt = ac.get("message", "Approval required")
    # Sub-workflow steps don't require a prompt
    if step_type == "sub_workflow" and not prompt:
        sw = data.get("sub_workflow", {})
        prompt = f"Sub-workflow: {sw.get('workflow', 'unknown')}"

    return StepDefinition(
        id=data["id"],
        prompt=prompt,
        depends_on=data.get("depends_on", []),
        model=data.get("model", defaults.get("model", "sonnet")),
        max_turns=data.get("max_turns", defaults.get("max_turns", 10)),
        timeout=data.get("timeout", defaults.get("timeout", 300)),
        parallel_over=data.get("parallel_over"),
        output_schema=data.get("output_schema"),
        retry=_parse_retry(data.get("retry")),
        fallback=_parse_fallback(data.get("fallback")),
        type=step_type,
        approval_config=_parse_approval_config(data.get("approval_config")),
        autopilot=_parse_autopilot_config(data.get("autopilot")),
        sub_workflow=_parse_sub_workflow_config(data.get("sub_workflow")),
    )


def parse(yaml_path: str) -> WorkflowDefinition:
    """Parse a workflow YAML file into a WorkflowDefinition."""
    path = Path(yaml_path)
    with path.open() as f:
        data = yaml.safe_load(f)

    sandstorm_url = _resolve_env_vars(data.get("sandstorm_url", "http://localhost:8000"))
    default_model = data.get("default_model", "sonnet")
    default_max_turns = data.get("default_max_turns", 10)
    default_timeout = data.get("default_timeout", 300)

    defaults = {
        "model": default_model,
        "max_turns": default_max_turns,
        "timeout": default_timeout,
    }

    steps = [_parse_step(s, defaults) for s in data.get("steps", [])]

    on_complete = None
    if "on_complete" in data:
        oc = data["on_complete"]
        on_complete = CompletionConfig(
            webhook=_resolve_env_vars(oc["webhook"]) if oc.get("webhook") else None,
            storage_path=oc.get("storage_path"),
        )

    on_failure = None
    if "on_failure" in data:
        of = data["on_failure"]
        on_failure = FailureConfig(
            dead_letter=of.get("dead_letter", False),
            webhook=_resolve_env_vars(of["webhook"]) if of.get("webhook") else None,
        )

    schedule = data.get("schedule")

    return WorkflowDefinition(
        name=data["name"],
        description=data.get("description", ""),
        sandstorm_url=sandstorm_url,
        default_model=default_model,
        default_max_turns=default_max_turns,
        default_timeout=default_timeout,
        steps=steps,
        on_complete=on_complete,
        on_failure=on_failure,
        schedule=schedule,
    )


def parse_yaml_string(yaml_content: str) -> WorkflowDefinition:
    """Parse a workflow from a YAML string (for API submissions)."""
    data = yaml.safe_load(yaml_content)

    sandstorm_url = _resolve_env_vars(data.get("sandstorm_url", "http://localhost:8000"))
    default_model = data.get("default_model", "sonnet")
    default_max_turns = data.get("default_max_turns", 10)
    default_timeout = data.get("default_timeout", 300)

    defaults = {
        "model": default_model,
        "max_turns": default_max_turns,
        "timeout": default_timeout,
    }

    steps = [_parse_step(s, defaults) for s in data.get("steps", [])]

    on_complete = None
    if "on_complete" in data:
        oc = data["on_complete"]
        on_complete = CompletionConfig(
            webhook=_resolve_env_vars(oc["webhook"]) if oc.get("webhook") else None,
            storage_path=oc.get("storage_path"),
        )

    on_failure = None
    if "on_failure" in data:
        of = data["on_failure"]
        on_failure = FailureConfig(
            dead_letter=of.get("dead_letter", False),
            webhook=_resolve_env_vars(of["webhook"]) if of.get("webhook") else None,
        )

    return WorkflowDefinition(
        name=data["name"],
        description=data.get("description", ""),
        sandstorm_url=sandstorm_url,
        default_model=default_model,
        default_max_turns=default_max_turns,
        default_timeout=default_timeout,
        steps=steps,
        on_complete=on_complete,
        on_failure=on_failure,
        schedule=data.get("schedule"),
    )


def validate(workflow: WorkflowDefinition) -> list[str]:
    """Validate a workflow definition. Returns list of error messages (empty = valid)."""
    errors: list[str] = []

    if not workflow.name:
        errors.append("Workflow name is required")

    if not workflow.steps:
        errors.append("Workflow must have at least one step")

    step_ids = {s.id for s in workflow.steps}

    # Check for duplicate step IDs
    seen: set[str] = set()
    for step in workflow.steps:
        if step.id in seen:
            errors.append(f"Duplicate step ID: '{step.id}'")
        seen.add(step.id)

    # Check depends_on references
    for step in workflow.steps:
        for dep in step.depends_on:
            if dep not in step_ids:
                errors.append(f"Step '{step.id}' depends on unknown step '{dep}'")

    # Check approval steps have required config
    for step in workflow.steps:
        if step.type == "approval":
            if not step.approval_config or not step.approval_config.message:
                errors.append(
                    f"Approval step '{step.id}' must have approval_config with a message"
                )
        if step.type == "sub_workflow":
            if not step.sub_workflow or not step.sub_workflow.workflow:
                errors.append(
                    f"Sub-workflow step '{step.id}' must have sub_workflow.workflow"
                )

    # Check for cycles
    cycle_errors = _detect_cycles(workflow.steps)
    errors.extend(cycle_errors)

    return errors


def _detect_cycles(steps: list[StepDefinition]) -> list[str]:
    """Detect cycles in the step dependency graph."""
    adj: dict[str, list[str]] = {s.id: list(s.depends_on) for s in steps}
    visited: set[str] = set()
    in_stack: set[str] = set()
    errors: list[str] = []

    def dfs(node: str) -> bool:
        visited.add(node)
        in_stack.add(node)
        for neighbor in adj.get(node, []):
            if neighbor in in_stack:
                errors.append(f"Cycle detected involving step '{node}' -> '{neighbor}'")
                return True
            if neighbor not in visited:
                if dfs(neighbor):
                    return True
        in_stack.discard(node)
        return False

    for step in steps:
        if step.id not in visited:
            dfs(step.id)

    return errors


def build_plan(workflow: WorkflowDefinition) -> ExecutionPlan:
    """Build an execution plan using topological sort.

    Groups steps into stages where all steps in a stage can run in parallel.
    """
    step_map = {s.id: s for s in workflow.steps}
    in_degree: dict[str, int] = {s.id: 0 for s in workflow.steps}
    dependents: dict[str, list[str]] = {s.id: [] for s in workflow.steps}

    for step in workflow.steps:
        for dep in step.depends_on:
            in_degree[step.id] += 1
            dependents[dep].append(step.id)

    stages: list[list[str]] = []
    ready = [sid for sid, deg in in_degree.items() if deg == 0]

    while ready:
        # Sort for deterministic ordering
        stage = sorted(ready)
        stages.append(stage)

        next_ready: list[str] = []
        for sid in stage:
            for dependent in dependents[sid]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_ready.append(dependent)
        ready = next_ready

    # Check if all steps were scheduled (if not, there's a cycle)
    scheduled = {sid for stage in stages for sid in stage}
    if scheduled != set(step_map.keys()):
        unscheduled = set(step_map.keys()) - scheduled
        raise ValueError(f"Cannot build plan: unschedulable steps (cycle?): {unscheduled}")

    return ExecutionPlan(stages=stages)
