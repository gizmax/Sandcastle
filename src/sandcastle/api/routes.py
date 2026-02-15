"""API endpoints for Sandcastle."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sandcastle.api.schemas import (
    ApiResponse,
    ErrorResponse,
    HealthResponse,
    RunListItem,
    RunStatusResponse,
    StepStatusResponse,
    WorkflowRunRequest,
)
from sandcastle.config import settings
from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.sandbox import SandstormClient
from sandcastle.engine.storage import LocalStorage
from sandcastle.models.db import Run, RunStatus, RunStep, async_session, get_session
from sandcastle.queue.worker import enqueue_workflow

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

    # Check database
    db_ok = False
    try:
        async with async_session() as session:
            await session.execute(select(1))
            db_ok = True
    except Exception:
        pass

    # Check Redis
    redis_ok = False
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        redis_ok = True
        await r.aclose()
    except Exception:
        pass

    return ApiResponse(
        data=HealthResponse(
            status="ok" if all([sandstorm_ok, db_ok, redis_ok]) else "degraded",
            sandstorm=sandstorm_ok,
            redis=redis_ok,
            database=db_ok,
        )
    )


@router.post("/workflows/run/sync")
async def run_workflow_sync(request: WorkflowRunRequest) -> ApiResponse:
    """Run a workflow synchronously. Blocks until complete."""
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
                error=ErrorResponse(code="VALIDATION_ERROR", message="; ".join(errors))
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

    # Create DB record
    try:
        async with async_session() as session:
            db_run = Run(
                id=uuid.UUID(run_id),
                workflow_name=workflow.name,
                status=RunStatus.RUNNING,
                input_data=request.input,
                callback_url=request.callback_url,
                tenant_id=request.tenant_id,
                started_at=datetime.now(timezone.utc),
            )
            session.add(db_run)
            await session.commit()
    except Exception:
        logger.warning("Could not save run to database (DB may not be available)")

    result = await execute_workflow(
        workflow=workflow,
        plan=plan,
        input_data=request.input,
        run_id=run_id,
        storage=storage,
    )

    # Update DB record
    try:
        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(run_id))
            if db_run:
                db_run.status = (
                    RunStatus.COMPLETED if result.status == "completed" else RunStatus.FAILED
                )
                db_run.output_data = result.outputs
                db_run.total_cost_usd = result.total_cost_usd
                db_run.completed_at = result.completed_at
                db_run.error = result.error
                await session.commit()
    except Exception:
        logger.warning("Could not update run in database")

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


@router.post("/workflows/run")
async def run_workflow_async(request: WorkflowRunRequest) -> ApiResponse:
    """Run a workflow asynchronously. Returns immediately with run_id."""
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
                error=ErrorResponse(code="VALIDATION_ERROR", message="; ".join(errors))
            ).model_dump(),
        )

    run_id = str(uuid.uuid4())

    # Create DB record with QUEUED status
    try:
        async with async_session() as session:
            db_run = Run(
                id=uuid.UUID(run_id),
                workflow_name=workflow.name,
                status=RunStatus.QUEUED,
                input_data=request.input,
                callback_url=request.callback_url,
                tenant_id=request.tenant_id,
            )
            session.add(db_run)
            await session.commit()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="DB_ERROR", message=f"Could not create run: {e}")
            ).model_dump(),
        )

    # Enqueue the job
    try:
        await enqueue_workflow(request.workflow, request.input, run_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="QUEUE_ERROR", message=f"Could not enqueue job: {e}")
            ).model_dump(),
        )

    return ApiResponse(
        data={"run_id": run_id, "status": "queued"},
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> ApiResponse:
    """Get the status and details of a specific run."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(Run).options(selectinload(Run.steps)).where(Run.id == run_uuid)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
            ).model_dump(),
        )

    steps = [
        StepStatusResponse(
            step_id=s.step_id,
            parallel_index=s.parallel_index,
            status=s.status.value if hasattr(s.status, "value") else s.status,
            output=s.output_data,
            cost_usd=s.cost_usd,
            duration_seconds=s.duration_seconds,
            attempt=s.attempt,
            error=s.error,
        )
        for s in run.steps
    ]

    return ApiResponse(
        data=RunStatusResponse(
            run_id=str(run.id),
            workflow_name=run.workflow_name,
            status=run.status.value if hasattr(run.status, "value") else run.status,
            input_data=run.input_data,
            outputs=run.output_data,
            total_cost_usd=run.total_cost_usd,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error=run.error,
            steps=steps,
        )
    )


@router.get("/runs")
async def list_runs(
    status: str | None = Query(None, description="Filter by status"),
    workflow: str | None = Query(None, description="Filter by workflow name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List workflow runs with optional filters and pagination."""
    async with async_session() as session:
        stmt = select(Run).order_by(Run.created_at.desc())

        if status:
            stmt = stmt.where(Run.status == status)
        if workflow:
            stmt = stmt.where(Run.workflow_name == workflow)

        stmt = stmt.offset(offset).limit(limit)
        result = await session.execute(stmt)
        runs = result.scalars().all()

    items = [
        RunListItem(
            run_id=str(r.id),
            workflow_name=r.workflow_name,
            status=r.status.value if hasattr(r.status, "value") else r.status,
            total_cost_usd=r.total_cost_usd,
            started_at=r.started_at,
            completed_at=r.completed_at,
        )
        for r in runs
    ]

    return ApiResponse(data=items)
