"""API endpoints for Sandcastle."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException

from sandcastle.api.schemas import (
    ApiResponse,
    ErrorResponse,
    HealthResponse,
    RunStatusResponse,
    WorkflowRunRequest,
)
from sandcastle.config import settings
from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.sandbox import SandstormClient
from sandcastle.engine.storage import LocalStorage

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check() -> ApiResponse:
    """Check health of Sandcastle and its dependencies."""
    sandbox = SandstormClient(
        base_url=settings.sandstorm_url,
        anthropic_api_key=settings.anthropic_api_key,
        e2b_api_key=settings.e2b_api_key,
    )
    sandstorm_ok = await sandbox.health()
    await sandbox.close()

    return ApiResponse(
        data=HealthResponse(
            status="ok",
            sandstorm=sandstorm_ok,
            redis=False,  # Phase 2
            database=False,  # Phase 2
        )
    )


@router.post("/workflows/run/sync")
async def run_workflow_sync(request: WorkflowRunRequest) -> ApiResponse:
    """Run a workflow synchronously and return the full result.

    Blocks until the workflow completes.
    """
    try:
        workflow = parse_yaml_string(request.workflow)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    errors = validate(workflow)
    if errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="VALIDATION_ERROR",
                    message="; ".join(errors),
                )
            ).model_dump(),
        )

    try:
        plan = build_plan(workflow)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="PLAN_ERROR", message=str(e))
            ).model_dump(),
        )

    run_id = str(uuid.uuid4())
    storage = LocalStorage()

    result = await execute_workflow(
        workflow=workflow,
        plan=plan,
        input_data=request.input,
        run_id=run_id,
        storage=storage,
    )

    return ApiResponse(
        data=RunStatusResponse(
            run_id=result.run_id,
            workflow_name=workflow.name,
            status=result.status,
            input_data=request.input,
            outputs=result.outputs,
            total_cost_usd=result.total_cost_usd,
            started_at=result.started_at,
            completed_at=result.completed_at,
            error=result.error,
        )
    )
