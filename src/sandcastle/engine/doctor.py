"""Workflow Doctor - preflight check for workflow YAML.

Analyzes workflows for security risks, misconfigurations, missing credentials,
unrealistic settings, and best-practice violations BEFORE execution.
Returns structured diagnostics with severity levels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sandcastle.config import settings
from sandcastle.engine.dag import (
    NON_LLM_TYPES,
    VALID_STEP_TYPES,
    WorkflowDefinition,
    validate,
)
from sandcastle.engine.providers import PROVIDER_REGISTRY


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """A single doctor finding."""

    category: str  # e.g. "security", "config", "cost"
    severity: str  # "blocking", "warning", "info"
    step_id: str | None  # None = workflow-level
    message: str
    suggested_fix: str = ""


@dataclass
class DoctorReport:
    """Full doctor report for a workflow."""

    workflow_name: str
    risk: str = "LOW"  # LOW / MEDIUM / HIGH / CRITICAL
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "blocking"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def info(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "info"]

    @property
    def ok(self) -> bool:
        return len(self.blocking) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "risk": self.risk,
            "ok": self.ok,
            "summary": {
                "blocking": len(self.blocking),
                "warnings": len(self.warnings),
                "info": len(self.info),
                "total": len(self.findings),
            },
            "findings": [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "step_id": f.step_id,
                    "message": f.message,
                    "suggested_fix": f.suggested_fix,
                }
                for f in self.findings
            ],
        }


# ---------------------------------------------------------------------------
# Check categories
# ---------------------------------------------------------------------------

def _check_validation_errors(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 1: Run the existing validate() and promote errors to blocking."""
    errors = validate(wf)
    for err in errors:
        report.findings.append(Finding(
            category="validation",
            severity="blocking",
            step_id=None,
            message=err,
        ))


def _check_unsafe_context_source(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 2: context_source: custom runs shell commands on the host."""
    for step in wf.steps:
        if step.context_source == "custom":
            report.findings.append(Finding(
                category="security",
                severity="blocking",
                step_id=step.id,
                message="Uses context_source: custom which executes shell commands on the host. Only allowed in admin-trusted workflows.",
                suggested_fix="Move shell logic into a sandboxed code step, or ensure this workflow is only run from internal/admin endpoints.",
            ))


def _check_code_steps_outside_sandbox(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 3: code steps run exec() in-process."""
    for step in wf.steps:
        if step.type == "code":
            report.findings.append(Finding(
                category="security",
                severity="warning",
                step_id=step.id,
                message="Code step executes Python in-process via exec(). Blocked patterns protect against common escapes, but in-process execution has inherent risks.",
                suggested_fix="Consider running code logic in a sandbox backend (e2b, docker, cloudflare) instead of in-process exec().",
            ))


def _check_missing_credentials(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 4: Check if required API keys/connections are configured."""
    # Collect all models used
    models_used: set[str] = set()
    for step in wf.steps:
        if step.type not in NON_LLM_TYPES:
            models_used.add(step.model or wf.default_model or "sonnet")

    # Check each model has its API key
    for model_name in models_used:
        info = PROVIDER_REGISTRY.get(model_name)
        if not info:
            continue
        if not info.api_key_env:
            continue  # Local model, no key needed
        key_value = getattr(settings, info.api_key_env.lower(), "") or ""
        if not key_value:
            report.findings.append(Finding(
                category="credentials",
                severity="blocking",
                step_id=None,
                message=f"Model '{model_name}' requires {info.api_key_env} but it is not set.",
                suggested_fix=f"Set {info.api_key_env} in .env or via the /settings API.",
            ))

    # Check tool connections
    for step in wf.steps:
        if step.tools:
            for tool_spec in step.tools:
                if ":" in tool_spec:
                    tool_name, conn_name = tool_spec.split(":", 1)
                    report.findings.append(Finding(
                        category="credentials",
                        severity="info",
                        step_id=step.id,
                        message=f"Step uses tool '{tool_name}' with connection '{conn_name}'. Verify the connection is configured.",
                        suggested_fix=f"Check /tools/{tool_name}/connections to ensure '{conn_name}' exists.",
                    ))


def _check_unknown_models(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 5: References to models not in the provider registry."""
    for step in wf.steps:
        if step.type in NON_LLM_TYPES:
            continue
        model = step.model or wf.default_model or "sonnet"
        if model not in PROVIDER_REGISTRY:
            report.findings.append(Finding(
                category="config",
                severity="blocking",
                step_id=step.id,
                message=f"Model '{model}' is not in the provider registry.",
                suggested_fix=f"Use one of: {', '.join(sorted(PROVIDER_REGISTRY.keys()))}",
            ))


def _check_unrealistic_timeouts(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 6: Timeouts that are too short or too long."""
    for step in wf.steps:
        if step.timeout < 10:
            report.findings.append(Finding(
                category="config",
                severity="warning",
                step_id=step.id,
                message=f"Timeout is {step.timeout}s - very short. Most LLM steps need at least 30s.",
                suggested_fix="Set timeout to at least 30 for LLM steps, or 10 for code/transform steps.",
            ))
        elif step.timeout > 3600:
            report.findings.append(Finding(
                category="config",
                severity="warning",
                step_id=step.id,
                message=f"Timeout is {step.timeout}s ({step.timeout // 3600}h) - extremely long. May indicate misconfiguration.",
                suggested_fix="Consider breaking long-running work into multiple steps with shorter timeouts.",
            ))


def _check_missing_budget(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 7: No cost limit set."""
    default_budget = settings.default_max_cost_usd
    if not default_budget or default_budget <= 0:
        # Check if workflow has no protection against runaway costs
        has_expensive_model = False
        for step in wf.steps:
            if step.type in NON_LLM_TYPES:
                continue
            model = step.model or wf.default_model or "sonnet"
            info = PROVIDER_REGISTRY.get(model)
            if info and info.output_price_per_m > 5.0:
                has_expensive_model = True
                break
        if has_expensive_model:
            report.findings.append(Finding(
                category="cost",
                severity="warning",
                step_id=None,
                message="No max_cost_usd budget set and workflow uses expensive models. Runaway costs are possible.",
                suggested_fix="Set max_cost_usd in the run request or configure default_max_cost_usd in server settings.",
            ))


def _check_prompt_injection_risk(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 8: Steps that fetch external content and feed it to LLM."""
    for step in wf.steps:
        if step.context_source == "web":
            report.findings.append(Finding(
                category="security",
                severity="warning",
                step_id=step.id,
                message="Step fetches web content as context. External content may contain prompt injection attempts.",
                suggested_fix="Add output_max_tokens to limit injected context size. Review context_query to ensure it targets trusted sources.",
            ))
        if step.type == "browser":
            report.findings.append(Finding(
                category="security",
                severity="warning",
                step_id=step.id,
                message="Browser step navigates to external URLs. Page content may contain prompt injection payloads.",
                suggested_fix="Restrict browsed URLs to trusted domains. Set timeout and monitor for unexpected behavior.",
            ))


def _check_callback_url_ssrf(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 9: on_complete/on_failure callbacks with SSRF risk."""
    if wf.on_complete and wf.on_complete.webhook:
        url = wf.on_complete.webhook
        if _is_internal_url(url):
            report.findings.append(Finding(
                category="security",
                severity="blocking",
                step_id=None,
                message=f"on_complete webhook '{url}' points to an internal/private address (SSRF risk).",
                suggested_fix="Use only public HTTPS URLs for webhook callbacks.",
            ))
    if wf.on_failure and wf.on_failure.webhook:
        url = wf.on_failure.webhook
        if _is_internal_url(url):
            report.findings.append(Finding(
                category="security",
                severity="blocking",
                step_id=None,
                message=f"on_failure webhook '{url}' points to an internal/private address (SSRF risk).",
                suggested_fix="Use only public HTTPS URLs for webhook callbacks.",
            ))
    # Check HTTP steps for SSRF
    for step in wf.steps:
        if step.type == "http" and step.http_config and step.http_config.url:
            url = step.http_config.url
            # Skip template variables - they're dynamic
            if "{" in url:
                continue
            if _is_internal_url(url):
                report.findings.append(Finding(
                    category="security",
                    severity="warning",
                    step_id=step.id,
                    message=f"HTTP step targets internal URL '{url}'. This may be intentional for internal APIs, but review for SSRF risk.",
                    suggested_fix="Ensure the target URL is expected. Use the SSRF allowlist if available.",
                ))


def _check_secrets_in_yaml(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 10: Hardcoded secrets/tokens in workflow YAML."""
    # Patterns that look like API keys or tokens
    _SECRET_PATTERNS = [
        (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "OpenAI API key"),
        (re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"), "Anthropic API key"),
        (re.compile(r"xoxb-[0-9]{10,}-[a-zA-Z0-9]{20,}"), "Slack bot token"),
        (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "GitHub personal access token"),
        (re.compile(r"glpat-[a-zA-Z0-9\-_]{20,}"), "GitLab personal access token"),
        (re.compile(r"Bearer\s+[a-zA-Z0-9\-._~+/]{30,}"), "Bearer token"),
        (re.compile(r"[\"']?password[\"']?\s*[:=]\s*[\"'][^\"']{8,}[\"']", re.IGNORECASE), "Hardcoded password"),
    ]

    for step in wf.steps:
        texts_to_check = [step.prompt]
        if step.http_config:
            if step.http_config.url:
                texts_to_check.append(step.http_config.url)
            if step.http_config.headers:
                texts_to_check.extend(str(v) for v in step.http_config.headers.values())
            if step.http_config.body:
                texts_to_check.append(str(step.http_config.body))
        if step.code_config and step.code_config.code:
            texts_to_check.append(step.code_config.code)

        for text in texts_to_check:
            if not text:
                continue
            for pattern, secret_type in _SECRET_PATTERNS:
                if pattern.search(text):
                    report.findings.append(Finding(
                        category="security",
                        severity="blocking",
                        step_id=step.id,
                        message=f"Possible {secret_type} found in step configuration. Never embed secrets in YAML.",
                        suggested_fix="Use environment variables or tool connections for credentials. Reference via {{env.VAR_NAME}} syntax.",
                    ))
                    break  # One finding per step is enough


def _check_data_residency(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 11: EU data residency violations."""
    residency = settings.data_residency
    if not residency:
        return

    for step in wf.steps:
        if step.type in NON_LLM_TYPES:
            continue
        model = step.model or wf.default_model or "sonnet"
        info = PROVIDER_REGISTRY.get(model)
        if not info:
            continue
        if residency == "eu" and info.region not in ("eu", "local"):
            report.findings.append(Finding(
                category="compliance",
                severity="blocking",
                step_id=step.id,
                message=f"Model '{model}' (region: {info.region}) violates data_residency=eu setting.",
                suggested_fix=f"Use an EU model (e.g. mistral/large, mistral/small) or local model.",
            ))
        elif residency == "local" and info.region != "local":
            report.findings.append(Finding(
                category="compliance",
                severity="blocking",
                step_id=step.id,
                message=f"Model '{model}' (region: {info.region}) violates data_residency=local setting.",
                suggested_fix="Use a local model (ollama or omlx/*).",
            ))


def _check_dead_steps(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 12: Steps that will never receive correct input."""
    step_ids = {s.id for s in wf.steps}

    for step in wf.steps:
        # Check if step depends on nonexistent steps (already caught by validate,
        # but we add a more descriptive message)
        for dep in step.depends_on:
            if dep not in step_ids:
                continue  # Already reported by validate

        # Check template references to step outputs
        template_refs = set()
        for text in [step.prompt, step.context_query]:
            if not text:
                continue
            # Find {steps.X.output} patterns
            for m in re.finditer(r"\{steps\.([a-zA-Z0-9_\-]+)\.", text):
                template_refs.add(m.group(1))

        for ref in template_refs:
            if ref not in step_ids:
                continue  # Already reported
            # Check if the referenced step is actually a dependency
            if ref not in step.depends_on:
                # Not a direct dependency - might still work via transitive deps
                # but it's a smell
                report.findings.append(Finding(
                    category="config",
                    severity="warning",
                    step_id=step.id,
                    message=f"References output of step '{ref}' but doesn't declare it in depends_on. Output may not be available at runtime.",
                    suggested_fix=f"Add '{ref}' to depends_on list for step '{step.id}'.",
                ))


def _check_cost_estimation(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 13: Estimate workflow cost and warn about expensive runs."""
    total_min_cost = 0.0
    for step in wf.steps:
        if step.type in NON_LLM_TYPES:
            continue
        model = step.model or wf.default_model or "sonnet"
        info = PROVIDER_REGISTRY.get(model)
        if not info:
            continue
        # Rough estimate: ~2K input tokens, ~1K output tokens per turn
        input_tokens = 2000 * step.max_turns
        output_tokens = 1000 * step.max_turns
        step_cost = (input_tokens / 1_000_000 * info.input_price_per_m +
                     output_tokens / 1_000_000 * info.output_price_per_m)
        total_min_cost += step_cost

    if total_min_cost > 10.0:
        report.findings.append(Finding(
            category="cost",
            severity="warning",
            step_id=None,
            message=f"Estimated minimum cost is ${total_min_cost:.2f} per run. This may be expensive at scale.",
            suggested_fix="Consider using cheaper models (haiku, mistral/small) for non-critical steps, or reduce max_turns.",
        ))
    elif total_min_cost > 1.0:
        report.findings.append(Finding(
            category="cost",
            severity="info",
            step_id=None,
            message=f"Estimated minimum cost per run: ${total_min_cost:.2f}.",
        ))


def _check_sensor_config(wf: WorkflowDefinition, report: DoctorReport) -> None:
    """Category 14: Sensor steps with unrealistic polling config."""
    for step in wf.steps:
        if step.type != "sensor" or not step.sensor_config:
            continue
        cfg = step.sensor_config
        if hasattr(cfg, "check_interval") and cfg.check_interval < 5:
            report.findings.append(Finding(
                category="config",
                severity="warning",
                step_id=step.id,
                message=f"Sensor check_interval is {cfg.check_interval}s - very aggressive polling may overload the target.",
                suggested_fix="Set check_interval to at least 10s for external APIs, 5s for internal services.",
            ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INTERNAL_PATTERNS = [
    re.compile(r"^https?://localhost[:/]", re.IGNORECASE),
    re.compile(r"^https?://127\.0\.0\.\d+[:/]", re.IGNORECASE),
    re.compile(r"^https?://\[::1\][:/]", re.IGNORECASE),
    re.compile(r"^https?://10\.\d+\.\d+\.\d+[:/]", re.IGNORECASE),
    re.compile(r"^https?://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+[:/]", re.IGNORECASE),
    re.compile(r"^https?://192\.168\.\d+\.\d+[:/]", re.IGNORECASE),
    re.compile(r"^https?://169\.254\.\d+\.\d+[:/]", re.IGNORECASE),  # Link-local / metadata
    re.compile(r"^https?://metadata\.google\.internal", re.IGNORECASE),
]


def _is_internal_url(url: str) -> bool:
    """Check if a URL targets internal/private networks."""
    return any(p.search(url) for p in _INTERNAL_PATTERNS)


def _compute_risk(report: DoctorReport) -> str:
    """Compute overall risk level from findings."""
    security_blocking = sum(
        1 for f in report.findings
        if f.category == "security" and f.severity == "blocking"
    )
    total_blocking = len(report.blocking)
    total_warnings = len(report.warnings)

    if security_blocking > 0:
        return "CRITICAL"
    if total_blocking > 0:
        return "HIGH"
    if total_warnings >= 3:
        return "MEDIUM"
    if total_warnings > 0:
        return "LOW"
    return "LOW"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_ALL_CHECKS = [
    _check_validation_errors,
    _check_unsafe_context_source,
    _check_code_steps_outside_sandbox,
    _check_missing_credentials,
    _check_unknown_models,
    _check_unrealistic_timeouts,
    _check_missing_budget,
    _check_prompt_injection_risk,
    _check_callback_url_ssrf,
    _check_secrets_in_yaml,
    _check_data_residency,
    _check_dead_steps,
    _check_cost_estimation,
    _check_sensor_config,
]


def diagnose(workflow: WorkflowDefinition) -> DoctorReport:
    """Run all doctor checks on a workflow and return the report.

    This is the main entry point. Call it with a parsed WorkflowDefinition
    to get a full preflight diagnostic report.
    """
    report = DoctorReport(workflow_name=workflow.name)

    for check in _ALL_CHECKS:
        check(workflow, report)

    report.risk = _compute_risk(report)
    return report


def diagnose_all(workflows_dir: str | None = None) -> list[DoctorReport]:
    """Diagnose all workflow YAML files in a directory.

    Returns a list of DoctorReport, one per workflow file.
    Useful for batch checking / CI integration.
    """
    from pathlib import Path

    wf_dir = Path(workflows_dir or settings.workflows_dir)
    if not wf_dir.exists():
        return []

    reports = []
    for yaml_file in sorted([*wf_dir.glob("*.yaml"), *wf_dir.glob("*.yml")]):
        try:
            report = diagnose_yaml(yaml_file.read_text())
            reports.append(report)
        except Exception as e:
            report = DoctorReport(workflow_name=yaml_file.stem)
            report.findings.append(Finding(
                category="validation",
                severity="blocking",
                step_id=None,
                message=f"Failed to parse {yaml_file.name}: {e}",
            ))
            report.risk = "HIGH"
            reports.append(report)
    return reports


def diagnose_yaml(yaml_content: str) -> DoctorReport:
    """Parse YAML and run diagnostics. Returns report with parse errors if invalid."""
    from sandcastle.engine.dag import parse_yaml_string

    try:
        workflow = parse_yaml_string(yaml_content)
    except Exception as e:
        report = DoctorReport(workflow_name="<parse-error>")
        report.findings.append(Finding(
            category="validation",
            severity="blocking",
            step_id=None,
            message=f"Failed to parse workflow YAML: {e}",
        ))
        report.risk = "HIGH"
        return report

    return diagnose(workflow)
