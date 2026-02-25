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
class CsvOutputConfig:
    """Configuration for CSV file export of step output."""

    directory: str = "./output"
    mode: str = "new_file"  # "append" | "new_file"
    filename: str = ""  # Custom filename (without extension)


@dataclass
class PdfReportConfig:
    """Configuration for PDF report generation from step output."""

    directory: str = "./output"
    language: str = "en"
    filename: str = ""  # Custom filename (without extension)


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
class LlmConfig:
    """Configuration for a lightweight LLM step (no sandbox)."""

    system_prompt: str = ""


@dataclass
class HttpConfig:
    """Configuration for an HTTP request step."""

    url: str = ""
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | dict | None = None
    auth: str | None = None  # "bearer:{token}" or env var ref


@dataclass
class CodeConfig:
    """Configuration for inline code execution."""

    code: str = ""
    language: str = "python"


@dataclass
class ConditionConfig:
    """Configuration for if/else branching."""

    expression: str = ""
    then_steps: list[str] = field(default_factory=list)
    else_steps: list[str] = field(default_factory=list)


@dataclass
class ClassifyConfig:
    """Configuration for LLM-based routing."""

    categories: list[str] = field(default_factory=list)
    input: str = ""
    model: str = "haiku"
    branches: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class LoopConfig:
    """Configuration for for-each iteration."""

    over: str = ""
    step_ids: list[str] = field(default_factory=list)
    max_iterations: int = 100
    until: str | None = None


@dataclass
class RaceConfig:
    """Configuration for racing parallel branches - first valid result wins."""

    branches: list[list[str]] = field(default_factory=list)  # list of step ID lists
    validator: str | None = None  # optional validation expression


@dataclass
class SensorConfig:
    """Configuration for polling an external endpoint until a condition is met."""

    url: str = ""
    check_interval: int = 30  # seconds
    timeout: int = 1800  # seconds
    condition: str = ""  # expression to evaluate on response
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class GateConfig:
    """Configuration for multi-strategy approval gates."""

    strategies: list[dict] = field(default_factory=list)  # [{type: "llm_eval"|"human"|"timeout", config: {...}}]


@dataclass
class TransformConfig:
    """Configuration for a template-based data transformation step."""

    template: str = ""  # Jinja2-style template string


@dataclass
class NotifyConfig:
    """Configuration for a notification step."""

    service: str = ""  # Tool connector name (slack, teams, gmail, etc.)
    channel: str = ""  # Target channel/recipient
    message: str = ""  # Message template with {steps.X.output} vars


@dataclass
class DelegateConfig:
    """Configuration for delegating to another workflow."""

    workflow: str = ""  # Workflow name to run
    task_description: str = ""  # NL description with template vars
    timeout: int = 3600  # Max wait time in seconds


@dataclass
class BrowserConfig:
    """Configuration for browser automation steps."""

    mode: str = "playwright"  # "playwright" or "computer_use"
    start_url: str = ""
    viewport_width: int = 1280
    viewport_height: int = 720
    timeout_seconds: int = 120
    wait_after_action: float = 1.0  # seconds to wait between actions
    screenshot_on_error: bool = True
    headless: bool = True
    credentials_env: str | None = None  # env var name containing credentials JSON


@dataclass
class SLOConfig:
    """Service Level Objective for optimizer-driven model selection."""

    quality_min: float = 0.6
    cost_max_usd: float = 0.20
    latency_max_seconds: int = 120
    optimize_for: str = "balanced"  # "cost" | "quality" | "latency" | "balanced"


@dataclass
class ModelPoolOption:
    """A model variant available for optimizer selection."""

    id: str
    model: str
    max_turns: int = 10


@dataclass
class PolicyPattern:
    """A pattern to match in step output."""

    type: str  # "email", "phone", "ssn", "credit_card", "regex"
    pattern: str | None = None


@dataclass
class PolicyTrigger:
    """When to evaluate a policy."""

    type: str  # "output_contains", "condition"
    patterns: list[PolicyPattern] | None = None
    expression: str | None = None


@dataclass
class PolicyAction:
    """What to do when a policy triggers."""

    type: str  # "redact", "inject_approval", "alert", "block", "log"
    replacement: str | None = None
    apply_to: list[str] | None = None
    approval_config: dict | None = None
    message: str | None = None
    notify: list[str] | None = None


@dataclass
class PolicyDefinition:
    """A declarative policy rule."""

    id: str
    trigger: PolicyTrigger
    action: PolicyAction
    description: str | None = None
    severity: str = "medium"


@dataclass
class StepMemoryConfig:
    """Memory read/write configuration for a single step."""

    read: bool = True
    write: bool = False


@dataclass
class MemoryConfig:
    """Workflow-level memory configuration."""

    agent: str = ""
    scope: str = "workflow"       # workflow | agent | global
    auto_inject: bool = True
    max_inject: int = 10


VALID_STEP_TYPES = frozenset({
    "standard", "approval", "sub_workflow",
    "llm", "http", "code", "condition", "classify", "loop",
    "race", "sensor", "gate",
    "transform", "notify", "delegate", "browser",
})

# Types that don't need a prompt
NON_PROMPT_TYPES = frozenset({"http", "code", "condition", "loop", "race", "sensor", "transform", "notify"})

# Types that don't use an LLM model (skip model validation)
NON_LLM_TYPES = frozenset({"http", "code", "condition", "loop", "race", "sensor", "transform", "notify"})


@dataclass
class StepDefinition:
    """Definition of a single workflow step."""

    id: str
    prompt: str = ""
    depends_on: list[str] = field(default_factory=list)
    model: str = "sonnet"
    max_turns: int = 10
    timeout: int = 300
    parallel_over: str | None = None
    output_schema: dict | None = None
    retry: RetryConfig | None = None
    fallback: FallbackConfig | None = None
    type: str = "standard"  # "standard" | "approval" | "sub_workflow" | "llm" | "http" | "code" | "condition" | "classify" | "loop" | "race" | "sensor" | "gate" | "transform" | "notify" | "delegate"
    approval_config: ApprovalConfig | None = None
    autopilot: AutoPilotConfig | None = None
    sub_workflow: SubWorkflowConfig | None = None
    csv_output: CsvOutputConfig | None = None
    pdf_report: PdfReportConfig | None = None
    policies: list[str | PolicyDefinition] | None = None  # Policy refs or inline defs
    slo: SLOConfig | None = None
    model_pool: list[ModelPoolOption] | None = None
    tools: list[str] | None = None  # Tool connectors for this step (e.g. ["slack", "jira"])
    memory: StepMemoryConfig | None = None
    llm_config: LlmConfig | None = None
    http_config: HttpConfig | None = None
    code_config: CodeConfig | None = None
    condition_config: ConditionConfig | None = None
    classify_config: ClassifyConfig | None = None
    loop_config: LoopConfig | None = None
    race_config: RaceConfig | None = None
    sensor_config: SensorConfig | None = None
    gate_config: GateConfig | None = None
    transform_config: TransformConfig | None = None
    notify_config: NotifyConfig | None = None
    delegate_config: DelegateConfig | None = None
    browser_config: BrowserConfig | None = None


@dataclass
class WorkflowDefinition:
    """Full workflow definition parsed from YAML."""

    name: str
    description: str
    default_model: str
    default_max_turns: int
    default_timeout: int
    steps: list[StepDefinition]
    input_schema: dict | None = None
    on_complete: CompletionConfig | None = None
    on_failure: FailureConfig | None = None
    schedule: str | None = None
    policies: list[PolicyDefinition] = field(default_factory=list)
    default_tools: list[str] = field(default_factory=list)  # Workflow-level default tools
    memory: MemoryConfig | None = None

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
    """Replace ${ENV_VAR} patterns with environment variable values.

    Returns empty string if any variable is unresolved, so callers
    can fall back to config defaults via `resolved or fallback`.
    """

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, "")

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


def _parse_policy_pattern(data: dict) -> PolicyPattern:
    """Parse a single policy pattern from YAML data."""
    return PolicyPattern(
        type=data.get("type", "regex"),
        pattern=data.get("pattern"),
    )


def _parse_policy_trigger(data: dict) -> PolicyTrigger:
    """Parse a policy trigger from YAML data."""
    patterns = None
    if "patterns" in data:
        patterns = [_parse_policy_pattern(p) for p in data["patterns"]]
    return PolicyTrigger(
        type=data.get("type", "condition"),
        patterns=patterns,
        expression=data.get("expression"),
    )


def _parse_policy_action(data: dict) -> PolicyAction:
    """Parse a policy action from YAML data."""
    return PolicyAction(
        type=data.get("type", "log"),
        replacement=data.get("replacement"),
        apply_to=data.get("apply_to"),
        approval_config=data.get("approval_config"),
        message=data.get("message"),
        notify=data.get("notify"),
    )


def _parse_policy(data: dict) -> PolicyDefinition:
    """Parse a single policy definition from YAML data."""
    return PolicyDefinition(
        id=data["id"],
        trigger=_parse_policy_trigger(data.get("trigger", {})),
        action=_parse_policy_action(data.get("action", {})),
        description=data.get("description"),
        severity=data.get("severity", "medium"),
    )


def _parse_step_policies(
    data: list | None,
) -> list[str | PolicyDefinition] | None:
    """Parse step-level policies (can be references or inline definitions)."""
    if data is None:
        return None
    result: list[str | PolicyDefinition] = []
    for item in data:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and "id" in item:
            result.append(_parse_policy(item))
        elif isinstance(item, dict):
            # Inline policy without explicit id
            item.setdefault("id", f"inline-{len(result)}")
            result.append(_parse_policy(item))
    return result


def _parse_slo_config(data: dict | None) -> SLOConfig | None:
    """Parse SLO configuration from YAML data."""
    if data is None:
        return None
    return SLOConfig(
        quality_min=data.get("quality_min", 0.6),
        cost_max_usd=data.get("cost_max_usd", 0.20),
        latency_max_seconds=data.get("latency_max_seconds", 120),
        optimize_for=data.get("optimize_for", "balanced"),
    )


def _parse_csv_output(data: dict | None) -> CsvOutputConfig | None:
    """Parse CSV output configuration from YAML data."""
    if data is None:
        return None
    return CsvOutputConfig(
        directory=data.get("directory", "./output"),
        mode=data.get("mode", "new_file"),
        filename=data.get("filename", ""),
    )


def _parse_pdf_report(data: dict | None) -> PdfReportConfig | None:
    """Parse PDF report configuration from YAML data."""
    if data is None:
        return None
    return PdfReportConfig(
        directory=data.get("directory", "./output"),
        language=data.get("language", "en"),
        filename=data.get("filename", ""),
    )


def _parse_model_pool(data) -> list[ModelPoolOption] | None:
    """Parse model pool from YAML data.

    Accepts a list of dicts or the string "auto" for default pool.
    """
    if data is None:
        return None
    if data == "auto":
        return [
            ModelPoolOption(id="fast-cheap", model="haiku", max_turns=5),
            ModelPoolOption(id="balanced", model="sonnet", max_turns=10),
            ModelPoolOption(id="thorough", model="opus", max_turns=20),
            ModelPoolOption(id="minimax-m2.5", model="minimax/m2.5", max_turns=15),
            ModelPoolOption(id="openai-codex-mini", model="openai/codex-mini", max_turns=10),
        ]
    if isinstance(data, list):
        return [
            ModelPoolOption(
                id=item.get("id", f"option-{i}"),
                model=item.get("model", "sonnet"),
                max_turns=item.get("max_turns", 10),
            )
            for i, item in enumerate(data)
        ]
    return None


def _parse_step_memory(data) -> StepMemoryConfig | None:
    """Parse step-level memory configuration from YAML data."""
    if data is None:
        return None
    if isinstance(data, bool):
        return StepMemoryConfig(read=data, write=data)
    return StepMemoryConfig(
        read=data.get("read", True),
        write=data.get("write", False),
    )


def _parse_memory_config(data) -> MemoryConfig | None:
    """Parse workflow-level memory configuration from YAML data."""
    if data is None:
        return None
    return MemoryConfig(
        agent=data.get("agent", ""),
        scope=data.get("scope", "workflow"),
        auto_inject=data.get("auto_inject", True),
        max_inject=data.get("max_inject", 10),
    )


def _parse_llm_config(data: dict | None) -> LlmConfig | None:
    """Parse LLM step configuration from YAML data."""
    if data is None:
        return None
    return LlmConfig(system_prompt=data.get("system_prompt", ""))


def _parse_http_config(data: dict | None) -> HttpConfig | None:
    """Parse HTTP step configuration from YAML data."""
    if data is None:
        return None
    return HttpConfig(
        url=data.get("url", ""),
        method=data.get("method", "GET"),
        headers=data.get("headers", {}),
        body=data.get("body"),
        auth=data.get("auth"),
    )


def _parse_code_config(data: dict | None) -> CodeConfig | None:
    """Parse code step configuration from YAML data."""
    if data is None:
        return None
    return CodeConfig(
        code=data.get("code", ""),
        language=data.get("language", "python"),
    )


def _parse_condition_config(data: dict | None) -> ConditionConfig | None:
    """Parse condition step configuration from YAML data."""
    if data is None:
        return None
    return ConditionConfig(
        expression=data.get("expression", ""),
        then_steps=data.get("then", []),
        else_steps=data.get("else", []),
    )


def _parse_classify_config(data: dict | None) -> ClassifyConfig | None:
    """Parse classify step configuration from YAML data."""
    if data is None:
        return None
    return ClassifyConfig(
        categories=data.get("categories", []),
        input=data.get("input", ""),
        model=data.get("model", "haiku"),
        branches=data.get("branches", {}),
    )


def _parse_loop_config(data: dict | None) -> LoopConfig | None:
    """Parse loop step configuration from YAML data."""
    if data is None:
        return None
    return LoopConfig(
        over=data.get("over", ""),
        step_ids=data.get("step_ids", []),
        max_iterations=data.get("max_iterations", 100),
        until=data.get("until"),
    )


def _parse_race_config(data: dict | None) -> RaceConfig | None:
    """Parse race step configuration from YAML data."""
    if data is None:
        return None
    return RaceConfig(
        branches=data.get("branches", []),
        validator=data.get("validator"),
    )


def _parse_sensor_config(data: dict | None) -> SensorConfig | None:
    """Parse sensor step configuration from YAML data."""
    if data is None:
        return None
    return SensorConfig(
        url=data.get("url", ""),
        check_interval=data.get("check_interval", 30),
        timeout=data.get("timeout", 1800),
        condition=data.get("condition", ""),
        method=data.get("method", "GET"),
        headers=data.get("headers", {}),
    )


def _parse_gate_config(data: dict | None) -> GateConfig | None:
    """Parse gate step configuration from YAML data."""
    if data is None:
        return None
    return GateConfig(
        strategies=data.get("strategies", []),
    )


def _parse_transform_config(data: dict | None) -> TransformConfig | None:
    """Parse transform step configuration from YAML data."""
    if data is None:
        return None
    return TransformConfig(
        template=data.get("template", ""),
    )


def _parse_notify_config(data: dict | None) -> NotifyConfig | None:
    """Parse notify step configuration from YAML data."""
    if data is None:
        return None
    return NotifyConfig(
        service=data.get("service", ""),
        channel=data.get("channel", ""),
        message=data.get("message", ""),
    )


def _parse_delegate_config(data: dict | None) -> DelegateConfig | None:
    """Parse delegate step configuration from YAML data."""
    if data is None:
        return None
    return DelegateConfig(
        workflow=data.get("workflow", ""),
        task_description=data.get("task_description", ""),
        timeout=data.get("timeout", 3600),
    )


def _parse_browser_config(data: dict | None) -> BrowserConfig | None:
    """Parse browser step configuration from YAML data."""
    if data is None:
        return None
    return BrowserConfig(
        mode=data.get("mode", "playwright"),
        start_url=data.get("start_url", ""),
        viewport_width=data.get("viewport_width", 1280),
        viewport_height=data.get("viewport_height", 720),
        timeout_seconds=data.get("timeout_seconds", 120),
        wait_after_action=data.get("wait_after_action", 1.0),
        screenshot_on_error=data.get("screenshot_on_error", True),
        headless=data.get("headless", True),
        credentials_env=data.get("credentials_env"),
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
    # Non-prompt types get a placeholder prompt
    if step_type in NON_PROMPT_TYPES and not prompt:
        prompt = f"{step_type} step"

    # Parse SLO config
    slo = _parse_slo_config(data.get("slo"))
    model_pool = _parse_model_pool(data.get("model_pool"))

    # If step has SLO but no model_pool, default to auto
    if slo and not model_pool:
        model_pool = _parse_model_pool("auto")

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
        csv_output=_parse_csv_output(data.get("csv_output")),
        pdf_report=_parse_pdf_report(data.get("pdf_report")),
        policies=_parse_step_policies(data.get("policies")),
        slo=slo,
        model_pool=model_pool,
        tools=data.get("tools"),
        memory=_parse_step_memory(data.get("memory")),
        llm_config=_parse_llm_config(data.get("llm_config")),
        http_config=_parse_http_config(data.get("http_config")),
        code_config=_parse_code_config(data.get("code_config")),
        condition_config=_parse_condition_config(data.get("condition_config")),
        classify_config=_parse_classify_config(data.get("classify_config")),
        loop_config=_parse_loop_config(data.get("loop_config")),
        race_config=_parse_race_config(data.get("race_config")),
        sensor_config=_parse_sensor_config(data.get("sensor_config")),
        gate_config=_parse_gate_config(data.get("gate_config")),
        transform_config=_parse_transform_config(data.get("transform_config")),
        notify_config=_parse_notify_config(data.get("notify_config")),
        delegate_config=_parse_delegate_config(data.get("delegate_config")),
        browser_config=_parse_browser_config(data.get("browser_config")),
    )


def _parse_raw(data: dict) -> WorkflowDefinition:
    """Parse a raw YAML dict into a WorkflowDefinition."""
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
            webhook=(
                _resolve_env_vars(oc["webhook"]) if oc.get("webhook") else None
            ),
            storage_path=oc.get("storage_path"),
        )

    on_failure = None
    if "on_failure" in data:
        of = data["on_failure"]
        on_failure = FailureConfig(
            dead_letter=of.get("dead_letter", False),
            webhook=(
                _resolve_env_vars(of["webhook"]) if of.get("webhook") else None
            ),
        )

    global_policies = [_parse_policy(p) for p in data.get("policies", [])]

    return WorkflowDefinition(
        name=data["name"],
        description=data.get("description", ""),
        default_model=default_model,
        default_max_turns=default_max_turns,
        default_timeout=default_timeout,
        steps=steps,
        input_schema=data.get("input_schema"),
        on_complete=on_complete,
        on_failure=on_failure,
        schedule=data.get("schedule"),
        policies=global_policies,
        default_tools=data.get("default_tools", []),
        memory=_parse_memory_config(data.get("memory")),
    )


def parse(yaml_path: str) -> WorkflowDefinition:
    """Parse a workflow YAML file into a WorkflowDefinition."""
    path = Path(yaml_path)
    with path.open() as f:
        data = yaml.safe_load(f)
    return _parse_raw(data)


def parse_yaml_string(yaml_content: str) -> WorkflowDefinition:
    """Parse a workflow from a YAML string (for API submissions)."""
    data = yaml.safe_load(yaml_content)
    return _parse_raw(data)


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

    # Reject unknown step types
    for step in workflow.steps:
        if step.type not in VALID_STEP_TYPES:
            errors.append(
                f"Step '{step.id}' has unknown type '{step.type}'. "
                f"Valid types: {', '.join(sorted(VALID_STEP_TYPES))}"
            )

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

    # Validate hybrid step types
    for step in workflow.steps:
        if step.type == "http":
            if not step.http_config or not step.http_config.url:
                errors.append(
                    f"HTTP step '{step.id}' must have http_config with a url"
                )
        elif step.type == "code":
            if not step.code_config or not step.code_config.code:
                errors.append(
                    f"Code step '{step.id}' must have code_config with code"
                )
        elif step.type == "condition":
            if not step.condition_config or not step.condition_config.expression:
                errors.append(
                    f"Condition step '{step.id}' must have condition_config with an expression"
                )
            if step.condition_config:
                for sid in step.condition_config.then_steps:
                    if sid not in step_ids:
                        errors.append(
                            f"Condition step '{step.id}' then references unknown step '{sid}'"
                        )
                for sid in step.condition_config.else_steps:
                    if sid not in step_ids:
                        errors.append(
                            f"Condition step '{step.id}' else references unknown step '{sid}'"
                        )
        elif step.type == "classify":
            if not step.classify_config or not step.classify_config.categories:
                errors.append(
                    f"Classify step '{step.id}' must have classify_config with categories"
                )
            if step.classify_config:
                for cat, branch_steps in step.classify_config.branches.items():
                    for sid in branch_steps:
                        if sid not in step_ids:
                            errors.append(
                                f"Classify step '{step.id}' branch '{cat}' references unknown step '{sid}'"
                            )
        elif step.type == "loop":
            if not step.loop_config or not step.loop_config.over:
                errors.append(
                    f"Loop step '{step.id}' must have loop_config with over"
                )
        elif step.type == "race":
            if not step.race_config or not step.race_config.branches:
                errors.append(
                    f"Race step '{step.id}' must have race_config with branches"
                )
            if step.race_config:
                for branch in step.race_config.branches:
                    for sid in branch:
                        if sid not in step_ids:
                            errors.append(
                                f"Race step '{step.id}' references unknown step '{sid}'"
                            )
        elif step.type == "sensor":
            if not step.sensor_config or not step.sensor_config.url:
                errors.append(
                    f"Sensor step '{step.id}' must have sensor_config with a url"
                )
            if step.sensor_config and not step.sensor_config.condition:
                errors.append(
                    f"Sensor step '{step.id}' must have sensor_config with a condition"
                )
        elif step.type == "gate":
            if not step.gate_config or not step.gate_config.strategies:
                errors.append(
                    f"Gate step '{step.id}' must have gate_config with strategies"
                )
        elif step.type == "transform":
            if not step.transform_config or not step.transform_config.template:
                errors.append(
                    f"Transform step '{step.id}' must have transform_config with a template"
                )
        elif step.type == "notify":
            if not step.notify_config or not step.notify_config.service:
                errors.append(
                    f"Notify step '{step.id}' must have notify_config with a service"
                )
            if not step.notify_config or not step.notify_config.message:
                errors.append(
                    f"Notify step '{step.id}' must have notify_config with a message"
                )
        elif step.type == "delegate":
            if not step.delegate_config or not step.delegate_config.workflow:
                errors.append(
                    f"Delegate step '{step.id}' must have delegate_config with a workflow"
                )
        elif step.type == "browser":
            if not step.browser_config:
                errors.append(
                    f"Browser step '{step.id}' must have browser_config"
                )
            elif step.browser_config.mode not in ("playwright", "computer_use"):
                errors.append(
                    f"Browser step '{step.id}' has invalid mode "
                    f"'{step.browser_config.mode}'. "
                    "Must be 'playwright' or 'computer_use'"
                )

    # Check model names against provider registry
    from sandcastle.engine.providers import KNOWN_MODELS

    all_models = {workflow.default_model}
    for step in workflow.steps:
        # Skip model validation for step types that don't use LLMs
        if step.type not in NON_LLM_TYPES:
            all_models.add(step.model)
        if step.fallback:
            all_models.add(step.fallback.model)
        if step.autopilot:
            for variant in step.autopilot.variants:
                if variant.model:
                    all_models.add(variant.model)
        # Validate classify config model
        if step.classify_config and step.classify_config.model:
            all_models.add(step.classify_config.model)
    for model_name in all_models:
        if model_name not in KNOWN_MODELS:
            errors.append(
                f"Unknown model '{model_name}'. "
                f"Available: {', '.join(sorted(KNOWN_MODELS))}"
            )

    # Check SLO configuration
    for step in workflow.steps:
        if step.slo and step.slo.optimize_for not in (
            "cost", "quality", "latency", "balanced"
        ):
            errors.append(
                f"Step '{step.id}' has invalid SLO optimize_for: '{step.slo.optimize_for}'"
            )

    # Check tool names against tool registry (supports tool:connection syntax)
    from sandcastle.engine.tools.registry import KNOWN_TOOLS, parse_tool_ref

    all_tools: set[str] = set(workflow.default_tools)
    for step in workflow.steps:
        if step.tools:
            all_tools.update(step.tools)
    for tool_ref in all_tools:
        base_name, _ = parse_tool_ref(tool_ref)
        if base_name not in KNOWN_TOOLS:
            errors.append(
                f"Unknown tool '{base_name}'. "
                f"Available: {', '.join(sorted(KNOWN_TOOLS))}"
            )

    # Check memory configuration
    if workflow.memory:
        if workflow.memory.scope not in ("workflow", "agent", "global"):
            errors.append(
                f"Invalid memory scope '{workflow.memory.scope}'. "
                "Must be 'workflow', 'agent', or 'global'"
            )
        if workflow.memory.scope == "agent" and not workflow.memory.agent:
            errors.append(
                "Memory scope 'agent' requires 'agent' name to be set"
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
