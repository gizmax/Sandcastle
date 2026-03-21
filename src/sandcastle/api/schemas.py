"""Pydantic request/response models for the API."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# Maximum serialized size (in bytes) for free-form JSON dict fields.
# Prevents oversized payloads that could exhaust memory or storage.
_MAX_JSON_DICT_BYTES = 1_048_576  # 1 MB

# Maximum nesting depth for JSON dict fields.
_MAX_JSON_NESTING_DEPTH = 20


def _check_json_dict_size(value: dict | None, field_name: str) -> dict | None:
    """Reject dicts whose JSON serialization exceeds _MAX_JSON_DICT_BYTES."""
    if value is None:
        return value
    raw = json.dumps(value, separators=(",", ":"))
    if len(raw.encode("utf-8")) > _MAX_JSON_DICT_BYTES:
        raise ValueError(
            f"{field_name} JSON payload exceeds maximum size of "
            f"{_MAX_JSON_DICT_BYTES} bytes"
        )
    return value


def _check_json_nesting_depth(obj: Any, current_depth: int = 0) -> None:
    """Raise ValueError if nesting exceeds _MAX_JSON_NESTING_DEPTH."""
    if current_depth > _MAX_JSON_NESTING_DEPTH:
        raise ValueError(
            f"JSON nesting depth exceeds maximum of {_MAX_JSON_NESTING_DEPTH}"
        )
    if isinstance(obj, dict):
        for v in obj.values():
            _check_json_nesting_depth(v, current_depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _check_json_nesting_depth(item, current_depth + 1)


def _validate_json_dict(value: dict | None, field_name: str) -> dict | None:
    """Combined size + nesting depth check for JSON dict fields."""
    if value is None:
        return value
    _check_json_dict_size(value, field_name)
    _check_json_nesting_depth(value)
    return value


# --- Requests ---


class WorkflowRunRequest(BaseModel):
    """Request to run a workflow.

    Provide either `workflow` (raw YAML) or `workflow_name` (file reference).
    """

    workflow: str | None = Field(None, description="Workflow YAML content", max_length=500000)
    workflow_name: str | None = Field(
        None, description="Name of a saved workflow file to run", max_length=200
    )
    input: dict[str, Any] = Field(default_factory=dict, description="Input data for the workflow")
    callback_url: str | None = Field(
        None, description="Webhook URL for completion notification", max_length=2000
    )
    idempotency_key: str | None = Field(
        None, description="Unique key to prevent duplicate runs", max_length=200
    )
    max_cost_usd: float | None = Field(None, description="Maximum cost limit for this run", ge=0)
    version: int | str | None = Field(None, description="Workflow version (int, 'latest', or None)")

    @field_validator("callback_url")
    @classmethod
    def callback_url_must_be_http(cls, v: str | None) -> str | None:
        """Reject non-HTTP(S) callback URLs at the schema level."""
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("callback_url must not be empty or whitespace-only")
            if not stripped.startswith(("http://", "https://")):
                raise ValueError("callback_url must use http:// or https:// scheme")
        return v

    @field_validator("version")
    @classmethod
    def version_must_be_int_or_latest(cls, v: int | str | None) -> int | str | None:
        """Ensure version is either a positive integer or the string 'latest'."""
        if v is None:
            return v
        if isinstance(v, int):
            if v < 1:
                raise ValueError("version must be a positive integer")
            return v
        if isinstance(v, str):
            if v != "latest":
                raise ValueError("version string must be 'latest'")
            return v
        raise ValueError("version must be an integer, 'latest', or null")

    @field_validator("input")
    @classmethod
    def validate_input_dict(cls, v: dict[str, Any]) -> dict[str, Any]:
        _validate_json_dict(v, "input")
        return v

    @model_validator(mode="after")
    def workflow_or_name_required(self) -> WorkflowRunRequest:
        """At least one of workflow or workflow_name must be provided."""
        if self.workflow is None and self.workflow_name is None:
            raise ValueError("Either 'workflow' or 'workflow_name' must be provided")
        return self


class ReplayRequest(BaseModel):
    """Request to replay a run from a specific step."""

    from_step: str = Field(..., description="Step ID to replay from", min_length=1, max_length=100)


class ForkRequest(BaseModel):
    """Request to fork a run from a specific step with overrides."""

    from_step: str = Field(..., description="Step ID to fork from", min_length=1, max_length=100)
    changes: dict[str, Any] = Field(
        default_factory=dict,
        description="Step overrides (e.g. prompt, model, max_turns)",
    )

    @field_validator("changes")
    @classmethod
    def validate_changes_dict(cls, v: dict[str, Any]) -> dict[str, Any]:
        _validate_json_dict(v, "changes")
        return v


class ScheduleCreateRequest(BaseModel):
    """Request to create a scheduled workflow."""

    workflow_name: str = Field(..., min_length=1, max_length=200)
    cron_expression: str = Field(..., min_length=1, max_length=100)
    input_data: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] | None = None
    enabled: bool = True

    @field_validator("input_data")
    @classmethod
    def validate_input_data_dict(cls, v: dict[str, Any]) -> dict[str, Any]:
        _validate_json_dict(v, "input_data")
        return v

    @field_validator("notify")
    @classmethod
    def validate_notify_dict(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        _validate_json_dict(v, "notify")
        return v

    @field_validator("cron_expression")
    @classmethod
    def cron_expression_must_have_five_fields(cls, v: str) -> str:
        """Basic cron format check: must have exactly 5 space-separated fields."""
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError(
                "cron_expression must have exactly 5 fields "
                "(minute hour day month weekday)"
            )
        return v


class ScheduleUpdateRequest(BaseModel):
    """Request to update a schedule."""

    enabled: bool | None = None
    cron_expression: str | None = Field(None, min_length=1, max_length=100)
    input_data: dict[str, Any] | None = None

    @field_validator("input_data")
    @classmethod
    def validate_input_data_dict(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        _validate_json_dict(v, "input_data")
        return v

    @field_validator("cron_expression")
    @classmethod
    def cron_expression_must_have_five_fields(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError(
                "cron_expression must have exactly 5 fields "
                "(minute hour day month weekday)"
            )
        return v


class WorkflowGenerateRequest(BaseModel):
    """Request to generate a workflow from natural language."""

    description: str = Field(
        ...,
        description="Natural language description of the workflow",
        min_length=1,
        max_length=10000,
    )
    refine_from: str | None = Field(
        None, description="Existing YAML to refine", max_length=500000
    )
    refine_instruction: str | None = Field(
        None,
        description="What to change in the existing YAML",
        max_length=10000,
    )


class GenerateChatMessage(BaseModel):
    """A single message in a chat-based generation conversation."""

    role: str = Field(
        ...,
        description="Message role: 'user' or 'assistant'",
        pattern="^(user|assistant)$",
    )
    content: str = Field(..., description="Message content", min_length=1, max_length=100000)


class GenerateChatRequest(BaseModel):
    """Request for chat-based workflow generation."""

    messages: list[GenerateChatMessage] = Field(
        ...,
        description="Conversation history",
        min_length=1,
        max_length=500,
    )
    existing_yaml: str | None = Field(
        None, description="Existing workflow YAML for edit mode", max_length=500000
    )


class ExplainErrorRequest(BaseModel):
    """Request to explain a step failure using AI."""

    step_id: str = Field(..., description="Failed step identifier", min_length=1)
    step_type: str = Field("standard", description="Step type (llm, http, code, etc.)")
    error: str = Field(..., description="Raw error message", min_length=1, max_length=10000)
    prompt: str = Field("", description="Step prompt (for context)", max_length=5000)
    model: str = Field("", description="Model used by the step")
    workflow_name: str = Field("", description="Parent workflow name")


class RunEstimateRequest(BaseModel):
    """Request to estimate the cost of a workflow run before execution."""

    yaml_content: str = Field(
        ..., description="Workflow YAML content", min_length=1, max_length=500000,
    )


class WorkflowSaveRequest(BaseModel):
    """Request to save a workflow YAML file."""

    name: str = Field(
        ...,
        description="Workflow file name (without .yaml extension)",
        min_length=1,
        max_length=200,
    )
    content: str = Field(..., description="Workflow YAML content", min_length=1, max_length=500000)
    description: str = Field("", description="Version description", max_length=5000)

    @field_validator("name")
    @classmethod
    def name_must_be_safe_filename(cls, v: str) -> str:
        """Reject path traversal characters in workflow names."""
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError("Workflow name must not contain '/', '\\', or '..'")
        if v.strip() != v:
            raise ValueError("Workflow name must not have leading/trailing whitespace")
        return v


class ApiKeyCreateRequest(BaseModel):
    """Request to create a new API key."""

    tenant_id: str | None = Field(
        None,
        description="Tenant scope. Null for admin keys.",
        max_length=200,
    )
    name: str = Field(..., description="Description for the key", min_length=1, max_length=200)
    max_cost_per_run_usd: float | None = Field(None, description="Default cost limit per run", ge=0)
    allowed_workflows: list[str] | None = Field(
        None,
        description="Workflow names this key may call via /api/v1/. Null = all workflows.",
        max_length=500,
    )

    @field_validator("name")
    @classmethod
    def name_must_not_be_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be whitespace-only")
        return v

    @field_validator("allowed_workflows")
    @classmethod
    def validate_allowed_workflows(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for wf in v:
            if not isinstance(wf, str) or not wf.strip():
                raise ValueError("Each allowed_workflow entry must be a non-empty string")
            if len(wf) > 200:
                raise ValueError(f"Workflow name too long (max 200 chars): {wf[:50]}...")
        return v


class ApiKeyRotateRequest(BaseModel):
    """Request to rotate an API key."""

    grace_period_hours: int | None = Field(
        None,
        description="Hours to keep old key active (default: server setting)",
        ge=0,
        le=8760,  # Max 1 year
    )


class ApiKeyRotateResponse(BaseModel):
    """Response after rotating an API key."""

    new_key: str = Field(..., description="New plaintext API key - shown only once")
    new_key_id: str
    old_key_id: str
    old_key_expires_at: datetime
    grace_period_hours: int = Field(..., ge=0)


class ApiKeyAllowlistRequest(BaseModel):
    """Update IP allowlist for an API key."""

    cidrs: list[str] = Field(
        ...,
        description="List of CIDR ranges (e.g. ['10.0.0.0/8', '::1/128']). Empty list clears.",
        max_length=1000,
    )

    @field_validator("cidrs")
    @classmethod
    def validate_cidr_entries(cls, v: list[str]) -> list[str]:
        """Validate individual CIDR string lengths."""
        for cidr in v:
            if len(cidr) > 50:
                raise ValueError(f"CIDR entry too long (max 50 chars): {cidr[:60]}...")
            if not cidr.strip():
                raise ValueError("CIDR entries must not be empty or whitespace-only")
        return v


class DeadLetterResolveRequest(BaseModel):
    """Request to manually resolve a dead letter item."""

    reason: str | None = Field(None, max_length=5000)


class WorkflowPromoteRequest(BaseModel):
    """Request to promote a workflow version (draft->staging->production)."""

    version: int | None = Field(
        None, description="Version to promote (default: latest)", ge=1
    )


class WorkflowRollbackRequest(BaseModel):
    """Request to rollback a workflow to a previous version."""

    target_version: int | None = Field(
        None, description="Target version (default: previous)", ge=1
    )


class ApprovalRespondRequest(BaseModel):
    """Request to approve/reject/skip an approval gate."""

    comment: str | None = Field(None, description="Reviewer comment", max_length=5000)
    edited_data: dict[str, Any] | None = Field(
        None, description="Edited request data (only if allow_edit is true)"
    )

    @field_validator("edited_data")
    @classmethod
    def validate_edited_data_dict(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        _validate_json_dict(v, "edited_data")
        return v


# --- Responses ---


class ErrorResponse(BaseModel):
    """Standard error response."""

    code: str
    message: str
    details: Any | None = None


class ApiResponse(BaseModel):
    """Standard API response wrapper."""

    data: Any | None = None
    error: ErrorResponse | None = None
    meta: PaginationMeta | None = None


class PaginationMeta(BaseModel):
    """Pagination metadata for list endpoints."""

    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1)
    offset: int = Field(..., ge=0)


class RunStatusResponse(BaseModel):
    """Workflow run status."""

    run_id: str
    workflow_name: str
    status: str
    input_data: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    total_cost_usd: float = Field(0.0, ge=0)
    max_cost_usd: float | None = Field(None, ge=0)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    steps: list[StepStatusResponse] | None = None
    parent_run_id: str | None = None
    replay_from_step: str | None = None
    fork_changes: dict[str, Any] | None = None
    depth: int = Field(0, ge=0)
    sub_workflow_of_step: str | None = None
    sub_runs: list[dict[str, Any]] | None = None
    risk_level: str = "minimal"


class StepStatusResponse(BaseModel):
    """Individual step status within a run."""

    step_id: str
    parallel_index: int | None = None
    status: str
    output: Any | None = None
    cost_usd: float = Field(0.0, ge=0)
    duration_seconds: float = Field(0.0, ge=0)
    attempt: int = Field(1, ge=1)
    error: str | None = None
    started_at: str | None = None
    pdf_artifact: bool = False


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    runtime: bool
    redis: bool | None = None  # None when Redis is not configured (local mode)
    database: bool


class LicenseInfoResponse(BaseModel):
    """License key status."""

    status: str  # "valid" | "expired" | "invalid" | "missing"
    tier: str  # "community" | "pro" | "enterprise"
    licensee: str = ""
    max_seats: int = Field(0, ge=0)
    expires: str = ""


class RuntimeInfoResponse(BaseModel):
    """Runtime mode information."""

    mode: str  # "local" or "production"
    database: str  # "sqlite" or "postgresql"
    queue: str  # "in-process" or "redis"
    storage: str  # "local" or "s3"
    sandbox_backend: str = "e2b"  # "e2b" | "docker" | "local" | "cloudflare"
    data_dir: str | None = None  # Only set in local mode
    version: str | None = None
    license: LicenseInfoResponse | None = None


class UpdateCheckResponse(BaseModel):
    """Software update check result."""

    current_version: str
    latest_version: str
    update_available: bool
    release_url: str
    install_command: str


class RunListItem(BaseModel):
    """Summary item for run list."""

    run_id: str
    workflow_name: str
    status: str
    total_cost_usd: float = Field(0.0, ge=0)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    parent_run_id: str | None = None


class StepDiff(BaseModel):
    """Comparison of a single step between two runs."""

    step_id: str
    parallel_index: int | None = None
    presence: str  # "both", "only_a", "only_b"
    config_a: dict[str, Any] | None = None
    config_b: dict[str, Any] | None = None
    config_changed: bool = False
    output_a: Any | None = None
    output_b: Any | None = None
    output_changed: bool = False
    cost_a: float = Field(0.0, ge=0)
    cost_b: float = Field(0.0, ge=0)
    cost_delta: float = 0.0
    duration_a: float = Field(0.0, ge=0)
    duration_b: float = Field(0.0, ge=0)
    duration_delta: float = 0.0
    status_a: str | None = None
    status_b: str | None = None
    error_a: str | None = None
    error_b: str | None = None


class RunCompareResponse(BaseModel):
    """Side-by-side comparison of two runs."""

    run_a: RunListItem
    run_b: RunListItem
    total_cost_a: float = Field(0.0, ge=0)
    total_cost_b: float = Field(0.0, ge=0)
    total_cost_delta: float = 0.0
    total_duration_a: float | None = None
    total_duration_b: float | None = None
    total_duration_delta: float | None = None
    same_workflow: bool = True
    steps: list[StepDiff] = []


class ScheduleResponse(BaseModel):
    """Schedule information."""

    id: str
    workflow_name: str
    cron_expression: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    last_run_id: str | None = None
    created_at: datetime | None = None


class StatsResponse(BaseModel):
    """Aggregated statistics for the overview dashboard."""

    total_runs_today: int = Field(0, ge=0)
    success_rate: float = Field(0.0, ge=0, le=100)
    total_cost_today: float = Field(0.0, ge=0)
    avg_duration_seconds: float = Field(0.0, ge=0)
    runs_by_day: list[dict[str, Any]] = Field(default_factory=list)
    cost_by_workflow: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowStepInfo(BaseModel):
    """Step info for DAG visualization and workflow editing."""

    id: str
    depends_on: list[str] = []
    model: str | None = None
    prompt: str = ""


class WorkflowInfoResponse(BaseModel):
    """Workflow file metadata."""

    name: str
    description: str
    steps_count: int = Field(..., ge=0)
    file_name: str
    steps: list[WorkflowStepInfo] = []
    input_schema: dict | None = None
    version: int | None = None
    version_status: str | None = None
    total_versions: int | None = None
    yaml_content: str | None = None


class WorkflowVersionResponse(BaseModel):
    """A single workflow version."""

    id: str
    workflow_name: str
    version: int = Field(..., ge=1)
    status: str
    description: str = ""
    steps_count: int = Field(0, ge=0)
    steps: list[WorkflowStepInfo] = []
    checksum: str = ""
    created_by: str | None = None
    promoted_by: str | None = None
    promoted_at: datetime | None = None
    created_at: datetime | None = None


class WorkflowVersionListResponse(BaseModel):
    """Workflow version history."""

    workflow_name: str
    production_version: int | None = None
    staging_version: int | None = None
    latest_draft_version: int | None = None
    versions: list[WorkflowVersionResponse] = []


class WorkflowVersionDiffResponse(BaseModel):
    """Diff between two workflow versions."""

    version_a: int = Field(..., ge=1)
    version_b: int = Field(..., ge=1)
    yaml_a: str
    yaml_b: str
    steps_added: list[str] = []
    steps_removed: list[str] = []
    steps_changed: list[str] = []


class ApiKeyResponse(BaseModel):
    """API key information (no plaintext)."""

    id: str
    key_prefix: str = ""
    tenant_id: str | None = None
    name: str
    is_active: bool
    max_cost_per_run_usd: float | None = None
    expires_at: datetime | None = None
    allowed_cidrs: list[str] | None = None
    allowed_workflows: list[str] | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class ApiKeyCreatedResponse(BaseModel):
    """Response after creating a new API key - includes plaintext ONCE."""

    id: str
    key_prefix: str
    tenant_id: str | None = None
    name: str
    key: str = Field(..., description="Plaintext API key - shown only once")
    allowed_workflows: list[str] | None = None


class DeadLetterItemResponse(BaseModel):
    """Dead letter queue item."""

    id: str
    run_id: str
    step_id: str
    parallel_index: int | None = None
    error: str | None = None
    input_data: dict[str, Any] | None = None
    attempts: int = Field(1, ge=1)
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class ExperimentResponse(BaseModel):
    """AutoPilot experiment details."""

    id: str
    workflow_name: str
    step_id: str
    status: str
    optimize_for: str = "quality"
    config: dict[str, Any] | None = None
    deployed_variant_id: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    samples: list[dict[str, Any]] | None = None
    rollout_stage: str | None = None


class SampleResponse(BaseModel):
    """AutoPilot experiment sample."""

    id: str
    experiment_id: str
    run_id: str | None = None
    variant_id: str
    variant_config: dict[str, Any] | None = None
    quality_score: float | None = None
    cost_usd: float = Field(0.0, ge=0)
    duration_seconds: float = Field(0.0, ge=0)
    created_at: datetime | None = None


class AutoPilotStatsResponse(BaseModel):
    """Overview statistics for AutoPilot experiments."""

    total_experiments: int = Field(0, ge=0)
    active_experiments: int = Field(0, ge=0)
    deploying_experiments: int = Field(0, ge=0)
    completed_experiments: int = Field(0, ge=0)
    total_samples: int = Field(0, ge=0)
    avg_quality_improvement: float = 0.0
    total_cost_savings_usd: float = 0.0


class ApprovalResponse(BaseModel):
    """Approval request details."""

    id: str
    run_id: str
    step_id: str
    status: str
    request_data: dict[str, Any] | None = None
    response_data: dict[str, Any] | None = None
    message: str = ""
    reviewer_id: str | None = None
    reviewer_comment: str | None = None
    timeout_at: datetime | None = None
    on_timeout: str = "abort"
    allow_edit: bool = False
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class PolicyViolationResponse(BaseModel):
    """A single policy violation record."""

    id: str
    run_id: str
    step_id: str
    policy_id: str
    severity: str = "medium"
    trigger_details: str | None = None
    action_taken: str
    output_modified: bool = False
    created_at: datetime | None = None


class PolicyViolationStatsResponse(BaseModel):
    """Aggregated policy violation statistics."""

    total_violations_30d: int = Field(0, ge=0)
    violations_by_severity: dict[str, int] = Field(default_factory=dict)
    violations_by_policy: dict[str, int] = Field(default_factory=dict)
    violations_by_day: list[dict[str, Any]] = Field(default_factory=list)


class RoutingDecisionResponse(BaseModel):
    """A single optimizer routing decision."""

    id: str
    run_id: str
    step_id: str
    selected_model: str
    selected_variant_id: str
    reason: str | None = None
    budget_pressure: float = Field(0.0, ge=0, le=1)
    confidence: float = Field(0.1, ge=0, le=1)
    alternatives: list[dict[str, Any]] | None = None
    slo: dict[str, Any] | None = None
    created_at: datetime | None = None


class OptimizerStatsResponse(BaseModel):
    """Overview statistics for the optimizer."""

    total_decisions_30d: int = Field(0, ge=0)
    model_distribution: dict[str, float] = Field(default_factory=dict)
    avg_confidence: float = Field(0.0, ge=0, le=1)
    estimated_savings_30d_usd: float = 0.0
    active_alerts: int = Field(0, ge=0)


class SettingsResponse(BaseModel):
    """Current server settings."""

    # Credentials (masked - only last 4 chars shown)
    anthropic_api_key: str = ""
    e2b_api_key: str = ""
    openai_api_key: str = ""
    minimax_api_key: str = ""
    openrouter_api_key: str = ""
    # Security
    auth_required: bool = False
    dashboard_origin: str = ""
    # Budget
    default_max_cost_usd: float = Field(0.0, ge=0)
    # Webhooks
    webhook_secret: str = ""
    # System
    log_level: str = "info"
    max_workflow_depth: int = Field(5, ge=1, le=20)
    # Storage (read-only info)
    storage_backend: str = "local"
    storage_bucket: str = ""
    storage_endpoint: str = ""
    data_dir: str = ""
    workflows_dir: str = ""
    # Runtime info (read-only)
    is_local_mode: bool = True
    database_url: str = ""
    redis_url: str = ""


class SettingsUpdateRequest(BaseModel):
    """Update server settings. Only include fields you want to change.

    Security-critical settings (auth_required, dashboard_origin, webhook_secret)
    can only be changed via environment variables, not via this API.
    """

    anthropic_api_key: str | None = Field(None, max_length=500)
    e2b_api_key: str | None = Field(None, max_length=500)
    openai_api_key: str | None = Field(None, max_length=500)
    minimax_api_key: str | None = Field(None, max_length=500)
    openrouter_api_key: str | None = Field(None, max_length=500)
    default_max_cost_usd: float | None = Field(None, ge=0)
    log_level: str | None = Field(None, pattern="^(debug|info|warning|error)$", max_length=50)
    max_workflow_depth: int | None = Field(None, ge=1, le=20)


# --- Tool Registry ---


class ToolFunctionResponse(BaseModel):
    """A single function within a tool."""

    name: str
    description: str
    parameters: dict = Field(
        default_factory=dict, description="JSON Schema for function parameters"
    )


class ToolConnectionResponse(BaseModel):
    """Named connection for a tool."""

    name: str
    tool_name: str
    credentials_configured: list[str] = []
    credentials_missing: list[str] = []
    created_at: datetime | None = None


class ToolResponse(BaseModel):
    """Tool definition with credential status."""

    name: str
    description: str
    category: str
    functions: list[ToolFunctionResponse] = []
    credential_env_vars: list[str] = []
    connector_file: str = ""
    icon: str = ""
    # Credential status (populated at runtime)
    configured: bool = False
    missing_credentials: list[str] = []
    connections: list[ToolConnectionResponse] = []


class ToolListResponse(BaseModel):
    """List of all available tools."""

    tools: list[ToolResponse] = []
    total: int = Field(0, ge=0)


class ToolCredentialUpdateRequest(BaseModel):
    """Update credentials for a specific tool."""

    credentials: dict[str, str] = Field(
        ...,
        description="Mapping of env var name to value, e.g. {'TOOL_SLACK_BOT_TOKEN': 'xoxb-...'}",
    )

    @field_validator("credentials")
    @classmethod
    def validate_credentials_entries(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 50:
            raise ValueError("Maximum 50 credential entries allowed")
        for key, val in v.items():
            if len(key) > 200:
                raise ValueError(f"Credential key too long (max 200 chars): {key[:60]}...")
            if len(val) > 10000:
                raise ValueError(f"Credential value too long (max 10000 chars) for key: {key}")
            if not key.strip():
                raise ValueError("Credential key must not be empty or whitespace-only")
        return v


class ToolConnectionCreateRequest(BaseModel):
    """Create a named connection for a tool."""

    name: str = Field(
        ..., description="Connection name (e.g. 'analytics', 'staging')",
        min_length=1, max_length=200,
    )
    credentials: dict[str, str] = Field(..., description="Mapping of env var name to value")

    @field_validator("credentials")
    @classmethod
    def validate_credentials_entries(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 50:
            raise ValueError("Maximum 50 credential entries allowed")
        if len(v) == 0:
            raise ValueError("At least one credential entry is required")
        for key, val in v.items():
            if len(key) > 200:
                raise ValueError(f"Credential key too long (max 200 chars): {key[:60]}...")
            if len(val) > 10000:
                raise ValueError(f"Credential value too long (max 10000 chars) for key: {key}")
        return v


class ToolConnectionUpdateRequest(BaseModel):
    """Update credentials for a named connection."""

    credentials: dict[str, str] = Field(..., description="Mapping of env var name to value")

    @field_validator("credentials")
    @classmethod
    def validate_credentials_entries(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 50:
            raise ValueError("Maximum 50 credential entries allowed")
        if len(v) == 0:
            raise ValueError("At least one credential entry is required")
        for key, val in v.items():
            if len(key) > 200:
                raise ValueError(f"Credential key too long (max 200 chars): {key[:60]}...")
            if len(val) > 10000:
                raise ValueError(f"Credential value too long (max 10000 chars) for key: {key}")
        return v


# --- Evaluations ---


class EvalSuiteRunRequest(BaseModel):
    """Request to run an eval suite."""

    suite_yaml: str = Field(
        ..., description="Eval suite YAML content", min_length=1, max_length=500000
    )
    concurrency: int = Field(1, description="Max concurrent test cases", ge=1, le=20)


class EvalAssertionResponse(BaseModel):
    """Result of a single assertion."""

    type: str
    passed: bool
    expected: Any | None = None
    actual: Any | None = None
    message: str = ""
    score: float | None = None


class EvalCaseResponse(BaseModel):
    """Result of a single eval case."""

    case_name: str
    passed: bool
    run_id: str | None = None
    cost_usd: float = Field(0.0, ge=0)
    duration_seconds: float = Field(0.0, ge=0)
    assertions: list[EvalAssertionResponse] = []
    output_summary: str | None = None
    error: str | None = None


class EvalRunResponse(BaseModel):
    """Eval run details."""

    id: str
    suite_name: str
    workflow_name: str
    status: str
    total_cases: int = Field(0, ge=0)
    passed_cases: int = Field(0, ge=0)
    failed_cases: int = Field(0, ge=0)
    pass_rate: float = Field(0.0, ge=0, le=100)
    total_cost_usd: float = Field(0.0, ge=0)
    total_duration_seconds: float = Field(0.0, ge=0)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    cases: list[EvalCaseResponse] | None = None


class EvalStatsResponse(BaseModel):
    """Aggregated eval statistics."""

    total_runs: int = Field(0, ge=0)
    avg_pass_rate: float = Field(0.0, ge=0, le=100)
    total_cost_usd: float = Field(0.0, ge=0)
    last_run_at: datetime | None = None
    pass_rate_trend: list[dict[str, Any]] = Field(default_factory=list)


# --- Memory ---


class MemoryAddRequest(BaseModel):
    """Request to add a memory."""

    scope: str = Field(
        "workflow",
        description="Memory scope: workflow | agent | global",
        pattern="^(workflow|agent|global)$",
    )
    scope_id: str = Field(
        ...,
        description="Scope ID (workflow name, agent name, or 'global')",
        min_length=1,
        max_length=500,
    )
    content: str = Field(..., description="Text to memorize", min_length=1, max_length=100000)
    metadata: dict | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata_dict(cls, v: dict | None) -> dict | None:
        _validate_json_dict(v, "metadata")
        return v


class MemorySearchRequest(BaseModel):
    """Request to search memories by semantic query."""

    scope_id: str = Field(..., min_length=1, max_length=500)
    query: str = Field(..., min_length=1, max_length=10000)
    limit: int = Field(10, ge=1, le=200)


class MemoryEntry(BaseModel):
    """A single memory entry."""

    id: str
    memory: str
    metadata: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryListResponse(BaseModel):
    """List of memories."""

    memories: list[MemoryEntry]
    total: int = Field(..., ge=0)


class AuditEventResponse(BaseModel):
    """A single audit trail event."""

    id: str
    event_type: str
    run_id: str | None = None
    actor_id: str
    actor_key_prefix: str | None = None
    source_ip: str | None = None
    payload: dict[str, Any] | None = None
    prev_hash: str
    entry_hash: str
    created_at: datetime | None = None


class AuditVerifyResponse(BaseModel):
    """Result of verifying an audit chain for a run."""

    run_id: str | None = None
    valid: bool
    chain_length: int = Field(..., ge=0)
    broken_at: dict[str, Any] | None = None


class EmergencyStopResponse(BaseModel):
    """Response after triggering a global emergency stop."""

    cancelled_count: int = Field(..., ge=0, description="Number of runs transitioned to CANCELLED")
    active: bool = Field(True, description="Whether the emergency stop flag is now active")


# --- Transparency / Compliance ---


class TransparencyAiModelEntry(BaseModel):
    """A single AI model usage entry in the transparency report."""

    step_id: str
    model: str
    cost_usd: float = Field(0.0, ge=0)


class TransparencyHumanOversightEntry(BaseModel):
    """A single human oversight (approval) entry in the transparency report."""

    step_id: str
    type: str = "approval"
    status: str
    reviewer: str | None = None


class TransparencyPolicyViolationEntry(BaseModel):
    """A single policy violation entry in the transparency report."""

    step_id: str
    policy: str
    severity: str
    action: str


class TransparencyReportResponse(BaseModel):
    """EU AI Act Article 13 transparency report for a workflow run."""

    run_id: str
    workflow_name: str
    risk_level: str = "minimal"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: str
    total_cost_usd: float = Field(0.0, ge=0)
    ai_models_used: list[TransparencyAiModelEntry] = Field(default_factory=list)
    human_oversight: list[TransparencyHumanOversightEntry] = Field(default_factory=list)
    policy_violations: list[TransparencyPolicyViolationEntry] = Field(default_factory=list)
    privacy_applied: bool = False
    total_steps: int = Field(0, ge=0)
    failed_steps: int = Field(0, ge=0)
    disclaimer: str = (
        "This report was generated for EU AI Act Article 13 transparency requirements."
    )


class AnnexIVSections(BaseModel):
    """Content sections for the Annex IV technical documentation."""

    intended_purpose: str = ""
    ai_models: list[str] = Field(default_factory=list)
    step_types: list[str] = Field(default_factory=list)
    human_oversight: str = ""
    risk_classification: str = ""
    testing_evidence: dict[str, Any] = Field(default_factory=dict)
    known_limitations: str = ""
    data_handling: str = ""
    audit_trail: str = ""


class AnnexIVResponse(BaseModel):
    """EU AI Act Annex IV technical documentation stub for a workflow."""

    workflow_name: str
    version: str | None = None
    risk_level: str = "minimal"
    generated_at: datetime
    sections: AnnexIVSections


class ComplianceStatusResponse(BaseModel):
    """Current compliance mode status and active features."""

    mode: str
    active: bool
    features: dict[str, bool] = Field(default_factory=dict)


# --- Workflow as API schemas ---


class WorkflowPublishResponse(BaseModel):
    """Response after publishing a workflow as a public API endpoint."""

    workflow_name: str
    version: int
    endpoint_url: str = Field(..., description="Public API endpoint URL")
    spec_url: str = Field(..., description="OpenAPI spec URL for this endpoint")
    example_curl: str = Field(..., description="Example curl command")
    example_sdk: str = Field(..., description="Example SDK snippet")
    is_public: bool = True


class WorkflowApiSpecResponse(BaseModel):
    """OpenAPI-compatible spec for a published workflow API endpoint."""

    workflow_name: str
    version: int
    endpoint_url: str
    method: str = "POST"
    auth_method: str = "X-API-Key header or Authorization: Bearer <key>"
    input_schema: dict[str, Any] | None = None
    output_description: str = "Workflow outputs keyed by step ID"
    async_header: str = "Set Prefer: respond-async to get run_id instead of waiting for result"


class WorkflowApiUsageResponse(BaseModel):
    """Usage statistics for a published workflow API endpoint."""

    workflow_name: str
    period_days: int = 30
    total_runs: int = Field(0, ge=0)
    successful_runs: int = Field(0, ge=0)
    failed_runs: int = Field(0, ge=0)
    total_cost_usd: float = Field(0.0, ge=0)
    avg_duration_seconds: float | None = None
    runs_by_day: list[dict[str, Any]] = Field(default_factory=list)


# --- Hub Marketplace ---


class HubSubmitRequest(BaseModel):
    """Request to submit a workflow template to the community hub."""

    yaml_content: str = Field(
        ..., description="Workflow YAML content", min_length=1, max_length=500000
    )
    description: str = Field(
        ..., description="Human-readable description", min_length=1, max_length=2000
    )
    category: str = Field("general_ai", description="Template category", max_length=100)
    tags: list[str] = Field(default_factory=list, description="Searchable tags")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        if len(v) > 20:
            raise ValueError("Maximum 20 tags allowed")
        for tag in v:
            if len(tag) > 100:
                raise ValueError(f"Tag too long (max 100 chars): {tag[:40]}...")
        return v


class HubSubmissionResponse(BaseModel):
    """Response for a hub submission record."""

    id: str
    slug: str
    name: str
    description: str | None = None
    category: str
    tags: list[str] = Field(default_factory=list)
    author: str
    status: str
    step_count: int = Field(0, ge=0)
    models_used: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    downloads: int = Field(0, ge=0)
    rating: float | None = None
    rating_count: int = Field(0, ge=0)
    created_at: datetime | None = None


class HubRateRequest(BaseModel):
    """Request to rate a hub template (1-5 stars)."""

    rating: int = Field(..., description="Star rating 1-5", ge=1, le=5)


# --- Workflow Evolution schemas ---


class EvolutionStartRequest(BaseModel):
    """Request to start a workflow evolution experiment."""

    workflow_name: str = Field(..., min_length=1, max_length=200)
    eval_suite_yaml: str = Field(..., min_length=1, max_length=500000)
    max_iterations: int = Field(20, ge=1, le=100)
    optimize_for: str = Field("balanced", pattern="^(quality|cost|latency|balanced)$")
    budget_limit_usd: float | None = Field(None, ge=0)


class EvolutionIterationResponse(BaseModel):
    """Response for a single evolution iteration record."""

    id: str
    iteration_number: int
    mutation_type: str
    mutation_description: str
    mutation_diff: list[dict[str, Any]] | None = None
    score: float | None = None
    quality: float | None = None
    cost_usd: float | None = None
    duration_seconds: float | None = None
    eval_pass_rate: float | None = None
    status: str
    created_at: datetime | None = None


class EvolutionStatusResponse(BaseModel):
    """Response for evolution status + history."""

    evolution_id: str
    workflow_name: str
    status: str
    optimize_for: str
    baseline_score: float | None = None
    baseline_quality: float | None = None
    baseline_cost: float | None = None
    best_score: float | None = None
    best_quality: float | None = None
    best_cost: float | None = None
    max_iterations: int
    current_iteration: int
    total_keeps: int
    total_discards: int
    budget_limit_usd: float | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    iterations: list[EvolutionIterationResponse] = Field(default_factory=list)


class EvolutionAcceptRequest(BaseModel):
    """Request to accept and promote the best evolution variant."""

    notes: str | None = Field(None, max_length=500)


class EvolutionStatsResponse(BaseModel):
    """Aggregated evolution statistics."""

    total_evolutions: int = 0
    active_evolutions: int = 0
    completed_evolutions: int = 0
    total_improvements: int = 0
    avg_improvement: float | None = None
    top_workflows: list[dict[str, Any]] = Field(default_factory=list)


# Fix forward reference for ApiResponse.meta
ApiResponse.model_rebuild()
