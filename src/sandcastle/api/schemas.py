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


# --- Responses ---


class ErrorResponse(BaseModel):
    """Standard error response."""

    code: str
    message: str


class ApiResponse(BaseModel):
    """Standard API response wrapper."""

    data: Any | None = None
    error: ErrorResponse | None = None


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
