"""Pydantic request/response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# --- Requests ---


class WorkflowRunRequest(BaseModel):
    """Request to run a workflow."""

    workflow: str = Field(..., description="Workflow YAML content or file path")
    input: dict[str, Any] = Field(default_factory=dict, description="Input data for the workflow")
    callback_url: str | None = Field(None, description="Webhook URL for completion notification")
    tenant_id: str | None = Field(None, description="Tenant identifier for multi-tenancy")


class ScheduleCreateRequest(BaseModel):
    """Request to create a scheduled workflow."""

    workflow_name: str
    cron_expression: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] | None = None
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    """Request to update a schedule (enable/disable toggle)."""

    enabled: bool


class WorkflowSaveRequest(BaseModel):
    """Request to save a workflow YAML file."""

    name: str = Field(..., description="Workflow file name (without .yaml extension)")
    content: str = Field(..., description="Workflow YAML content")


class ApiKeyCreateRequest(BaseModel):
    """Request to create a new API key."""

    tenant_id: str
    name: str = Field(..., description="Description for the key")


class DeadLetterResolveRequest(BaseModel):
    """Request to manually resolve a dead letter item."""

    reason: str | None = None


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
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    steps: list[StepStatusResponse] | None = None


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


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    sandstorm: bool
    redis: bool
    database: bool


class RunListItem(BaseModel):
    """Summary item for run list."""

    run_id: str
    workflow_name: str
    status: str
    total_cost_usd: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None


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


class WorkflowInfoResponse(BaseModel):
    """Workflow file metadata."""

    name: str
    description: str
    steps_count: int
    file_name: str


class ApiKeyResponse(BaseModel):
    """API key information (no plaintext)."""

    id: str
    tenant_id: str
    name: str
    is_active: bool
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class ApiKeyCreatedResponse(BaseModel):
    """Response after creating a new API key - includes plaintext ONCE."""

    id: str
    tenant_id: str
    name: str
    key: str = Field(..., description="Plaintext API key - shown only once")


class DeadLetterItemResponse(BaseModel):
    """Dead letter queue item."""

    id: str
    run_id: str
    step_id: str
    error: str | None = None
    input_data: dict[str, Any] | None = None
    attempts: int = 1
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None


# Fix forward reference for ApiResponse.meta
ApiResponse.model_rebuild()
