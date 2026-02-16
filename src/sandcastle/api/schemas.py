"""Pydantic request/response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# --- Requests ---


class WorkflowRunRequest(BaseModel):
    """Request to run a workflow.

    Provide either `workflow` (raw YAML) or `workflow_name` (file reference).
    """

    workflow: str | None = Field(None, description="Workflow YAML content")
    workflow_name: str | None = Field(None, description="Name of a saved workflow file to run")
    input: dict[str, Any] = Field(default_factory=dict, description="Input data for the workflow")
    callback_url: str | None = Field(None, description="Webhook URL for completion notification")
    idempotency_key: str | None = Field(None, description="Unique key to prevent duplicate runs")
    max_cost_usd: float | None = Field(None, description="Maximum cost limit for this run")


class ReplayRequest(BaseModel):
    """Request to replay a run from a specific step."""

    from_step: str = Field(..., description="Step ID to replay from")


class ForkRequest(BaseModel):
    """Request to fork a run from a specific step with overrides."""

    from_step: str = Field(..., description="Step ID to fork from")
    changes: dict[str, Any] = Field(
        default_factory=dict,
        description="Step overrides (e.g. prompt, model, max_turns)",
    )


class ScheduleCreateRequest(BaseModel):
    """Request to create a scheduled workflow."""

    workflow_name: str
    cron_expression: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] | None = None
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    """Request to update a schedule."""

    enabled: bool | None = None
    cron_expression: str | None = None
    input_data: dict[str, Any] | None = None


class WorkflowSaveRequest(BaseModel):
    """Request to save a workflow YAML file."""

    name: str = Field(..., description="Workflow file name (without .yaml extension)")
    content: str = Field(..., description="Workflow YAML content")


class ApiKeyCreateRequest(BaseModel):
    """Request to create a new API key."""

    tenant_id: str | None = Field(None, description="Tenant scope. Null for admin keys.")
    name: str = Field(..., description="Description for the key")
    max_cost_per_run_usd: float | None = Field(None, description="Default cost limit per run")


class DeadLetterResolveRequest(BaseModel):
    """Request to manually resolve a dead letter item."""

    reason: str | None = None


class ApprovalRespondRequest(BaseModel):
    """Request to approve/reject/skip an approval gate."""

    comment: str | None = Field(None, description="Reviewer comment")
    edited_data: dict[str, Any] | None = Field(
        None, description="Edited request data (only if allow_edit is true)"
    )


# --- Responses ---


class ErrorResponse(BaseModel):
    """Standard error response."""

    code: str
    message: str


class ApiResponse(BaseModel):
    """Standard API response wrapper."""

    data: Any | None = None
    error: ErrorResponse | None = None
    meta: PaginationMeta | None = None


class PaginationMeta(BaseModel):
    """Pagination metadata for list endpoints."""

    total: int
    limit: int
    offset: int


class RunStatusResponse(BaseModel):
    """Workflow run status."""

    run_id: str
    workflow_name: str
    status: str
    input_data: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    total_cost_usd: float = 0.0
    max_cost_usd: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    steps: list[StepStatusResponse] | None = None
    parent_run_id: str | None = None
    replay_from_step: str | None = None
    fork_changes: dict[str, Any] | None = None
    depth: int = 0
    sub_workflow_of_step: str | None = None
    sub_runs: list[dict[str, Any]] | None = None


class StepStatusResponse(BaseModel):
    """Individual step status within a run."""

    step_id: str
    parallel_index: int | None = None
    status: str
    output: Any | None = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    attempt: int = 1
    error: str | None = None
    started_at: str | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    sandstorm: bool
    redis: bool | None = None  # None when Redis is not configured (local mode)
    database: bool


class RuntimeInfoResponse(BaseModel):
    """Runtime mode information."""

    mode: str  # "local" or "production"
    database: str  # "sqlite" or "postgresql"
    queue: str  # "in-process" or "redis"
    storage: str  # "local" or "s3"
    data_dir: str | None = None  # Only set in local mode


class RunListItem(BaseModel):
    """Summary item for run list."""

    run_id: str
    workflow_name: str
    status: str
    total_cost_usd: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    parent_run_id: str | None = None


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

    total_runs_today: int = 0
    success_rate: float = 0.0
    total_cost_today: float = 0.0
    avg_duration_seconds: float = 0.0
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
    steps_count: int
    file_name: str
    steps: list[WorkflowStepInfo] = []


class ApiKeyResponse(BaseModel):
    """API key information (no plaintext)."""

    id: str
    key_prefix: str = ""
    tenant_id: str
    name: str
    is_active: bool
    max_cost_per_run_usd: float | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class ApiKeyCreatedResponse(BaseModel):
    """Response after creating a new API key - includes plaintext ONCE."""

    id: str
    key_prefix: str
    tenant_id: str
    name: str
    key: str = Field(..., description="Plaintext API key - shown only once")


class DeadLetterItemResponse(BaseModel):
    """Dead letter queue item."""

    id: str
    run_id: str
    step_id: str
    parallel_index: int | None = None
    error: str | None = None
    input_data: dict[str, Any] | None = None
    attempts: int = 1
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


class SampleResponse(BaseModel):
    """AutoPilot experiment sample."""

    id: str
    experiment_id: str
    run_id: str | None = None
    variant_id: str
    variant_config: dict[str, Any] | None = None
    quality_score: float | None = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    created_at: datetime | None = None


class AutoPilotStatsResponse(BaseModel):
    """Overview statistics for AutoPilot experiments."""

    total_experiments: int = 0
    active_experiments: int = 0
    completed_experiments: int = 0
    total_samples: int = 0
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

    total_violations_30d: int = 0
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
    budget_pressure: float = 0.0
    confidence: float = 0.1
    alternatives: list[dict[str, Any]] | None = None
    slo: dict[str, Any] | None = None
    created_at: datetime | None = None


class OptimizerStatsResponse(BaseModel):
    """Overview statistics for the optimizer."""

    total_decisions_30d: int = 0
    model_distribution: dict[str, float] = Field(default_factory=dict)
    avg_confidence: float = 0.0
    estimated_savings_30d_usd: float = 0.0


class SettingsResponse(BaseModel):
    """Current server settings."""

    # Connections
    sandstorm_url: str = ""
    # Credentials (masked - only last 4 chars shown)
    anthropic_api_key: str = ""
    e2b_api_key: str = ""
    # Security
    auth_required: bool = False
    dashboard_origin: str = ""
    # Budget
    default_max_cost_usd: float = 0.0
    # Webhooks
    webhook_secret: str = ""
    # System
    log_level: str = "info"
    max_workflow_depth: int = 5
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
    """Update server settings. Only include fields you want to change."""

    sandstorm_url: str | None = None
    anthropic_api_key: str | None = None
    e2b_api_key: str | None = None
    auth_required: bool | None = None
    dashboard_origin: str | None = None
    default_max_cost_usd: float | None = None
    webhook_secret: str | None = None
    log_level: str | None = None
    max_workflow_depth: int | None = None


# Fix forward reference for ApiResponse.meta
ApiResponse.model_rebuild()
