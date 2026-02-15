"""API endpoints for Sandcastle."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sandcastle.api.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    ApiResponse,
    DeadLetterItemResponse,
    DeadLetterResolveRequest,
    ErrorResponse,
    HealthResponse,
    PaginationMeta,
    RunListItem,
    RunStatusResponse,
    ScheduleCreateRequest,
    ScheduleResponse,
    ScheduleUpdateRequest,
    StatsResponse,
    StepStatusResponse,
    WorkflowInfoResponse,
    WorkflowRunRequest,
    WorkflowSaveRequest,
)
from sandcastle.config import settings
from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.sandbox import SandstormClient
from sandcastle.engine.storage import LocalStorage
from sandcastle.models.db import (
    ApiKey,
    DeadLetterItem,
    Run,
    RunStatus,
    RunStep,
    Schedule,
    async_session,
    get_session,
)
from sandcastle.api.auth import generate_api_key, hash_key
from sandcastle.queue.scheduler import add_schedule, remove_schedule
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


# --- Stats ---


@router.get("/stats")
async def get_stats() -> ApiResponse:
    """Get aggregated statistics for the overview dashboard."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with async_session() as session:
        # Total runs today
        total_today = await session.scalar(
            select(func.count(Run.id)).where(Run.created_at >= today_start)
        )

        # Success rate (completed / total non-queued runs today)
        completed_today = await session.scalar(
            select(func.count(Run.id)).where(
                Run.created_at >= today_start,
                Run.status == RunStatus.COMPLETED,
            )
        )
        finished_today = await session.scalar(
            select(func.count(Run.id)).where(
                Run.created_at >= today_start,
                Run.status.in_([RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PARTIAL]),
            )
        )
        success_rate = (completed_today / finished_today) if finished_today else 0.0

        # Total cost today
        total_cost = await session.scalar(
            select(func.coalesce(func.sum(Run.total_cost_usd), 0.0)).where(
                Run.created_at >= today_start
            )
        )

        # Average duration (completed runs today)
        avg_duration = await session.scalar(
            select(
                func.avg(
                    func.extract("epoch", Run.completed_at) - func.extract("epoch", Run.started_at)
                )
            ).where(
                Run.created_at >= today_start,
                Run.completed_at.isnot(None),
                Run.started_at.isnot(None),
            )
        )

        # Runs by day (last 30 days)
        thirty_days_ago = now - timedelta(days=30)
        runs_by_day_query = await session.execute(
            select(
                func.date_trunc("day", Run.created_at).label("day"),
                Run.status,
                func.count(Run.id).label("count"),
            )
            .where(Run.created_at >= thirty_days_ago)
            .group_by("day", Run.status)
            .order_by("day")
        )
        runs_by_day_raw = runs_by_day_query.all()

        # Aggregate by day
        day_map: dict[str, dict] = {}
        for row in runs_by_day_raw:
            day_str = row.day.strftime("%Y-%m-%d") if row.day else "unknown"
            if day_str not in day_map:
                day_map[day_str] = {"date": day_str, "completed": 0, "failed": 0, "total": 0}
            status_val = row.status.value if hasattr(row.status, "value") else row.status
            if status_val == "completed":
                day_map[day_str]["completed"] += row.count
            elif status_val == "failed":
                day_map[day_str]["failed"] += row.count
            day_map[day_str]["total"] += row.count

        runs_by_day = list(day_map.values())

        # Cost by workflow (last 7 days)
        seven_days_ago = now - timedelta(days=7)
        cost_query = await session.execute(
            select(
                Run.workflow_name,
                func.coalesce(func.sum(Run.total_cost_usd), 0.0).label("cost"),
            )
            .where(Run.created_at >= seven_days_ago)
            .group_by(Run.workflow_name)
            .order_by(func.sum(Run.total_cost_usd).desc())
        )
        cost_by_workflow = [
            {"workflow": row.workflow_name, "cost": float(row.cost)}
            for row in cost_query.all()
        ]

    return ApiResponse(
        data=StatsResponse(
            total_runs_today=total_today or 0,
            success_rate=round(success_rate, 4),
            total_cost_today=float(total_cost or 0),
            avg_duration_seconds=round(float(avg_duration or 0), 1),
            runs_by_day=runs_by_day,
            cost_by_workflow=cost_by_workflow,
        )
    )


# --- Workflows ---


@router.get("/workflows")
async def list_workflows() -> ApiResponse:
    """List available workflow YAML files from the workflows directory."""
    workflows_dir = Path(settings.workflows_dir)
    if not workflows_dir.exists():
        return ApiResponse(data=[])

    items = []
    for yaml_file in sorted(workflows_dir.glob("*.yaml")):
        try:
            content = yaml_file.read_text()
            workflow = parse_yaml_string(content)
            items.append(
                WorkflowInfoResponse(
                    name=workflow.name,
                    description=workflow.description,
                    steps_count=len(workflow.steps),
                    file_name=yaml_file.name,
                )
            )
        except Exception as e:
            logger.warning(f"Could not parse workflow file {yaml_file.name}: {e}")

    return ApiResponse(data=items)


@router.post("/workflows")
async def save_workflow(request: WorkflowSaveRequest) -> ApiResponse:
    """Save a workflow YAML file to the workflows directory."""
    # Validate the YAML content
    try:
        workflow = parse_yaml_string(request.content)
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

    # Save to disk
    workflows_dir = Path(settings.workflows_dir)
    workflows_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.name)
    file_path = workflows_dir / f"{safe_name}.yaml"
    file_path.write_text(request.content)

    return ApiResponse(
        data=WorkflowInfoResponse(
            name=workflow.name,
            description=workflow.description,
            steps_count=len(workflow.steps),
            file_name=file_path.name,
        )
    )


# --- Workflow Execution ---


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


# --- Runs ---


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


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    """Stream live progress of a run via SSE."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run ID format")

    async def event_generator():
        """Poll the database and emit SSE events as status changes."""
        last_status = None
        last_step_count = 0

        for _ in range(600):  # Max 10 minutes of polling (1s intervals)
            async with async_session() as session:
                stmt = (
                    select(Run).options(selectinload(Run.steps)).where(Run.id == run_uuid)
                )
                result = await session.execute(stmt)
                run = result.scalar_one_or_none()

            if not run:
                yield _sse_event("error", {"message": f"Run '{run_id}' not found"})
                return

            current_status = run.status.value if hasattr(run.status, "value") else run.status

            # Emit status change events
            if current_status != last_status:
                yield _sse_event("status", {
                    "run_id": str(run.id),
                    "status": current_status,
                    "total_cost_usd": run.total_cost_usd,
                })
                last_status = current_status

            # Emit step update events
            if len(run.steps) > last_step_count:
                for step in run.steps[last_step_count:]:
                    step_status = (
                        step.status.value if hasattr(step.status, "value") else step.status
                    )
                    yield _sse_event("step", {
                        "step_id": step.step_id,
                        "parallel_index": step.parallel_index,
                        "status": step_status,
                        "cost_usd": step.cost_usd,
                        "duration_seconds": step.duration_seconds,
                    })
                last_step_count = len(run.steps)

            # Terminal states - emit final result and stop
            if current_status in ("completed", "failed", "partial"):
                yield _sse_event("result", {
                    "run_id": str(run.id),
                    "status": current_status,
                    "outputs": run.output_data,
                    "total_cost_usd": run.total_cost_usd,
                    "error": run.error,
                })
                return

            await asyncio.sleep(1.0)

        yield _sse_event("error", {"message": "Stream timed out"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event: str, data: dict) -> str:
    """Format a server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/runs")
async def list_runs(
    status: str | None = Query(None, description="Filter by status"),
    workflow: str | None = Query(None, description="Filter by workflow name"),
    since: datetime | None = Query(None, description="Filter runs created after this datetime"),
    until: datetime | None = Query(None, description="Filter runs created before this datetime"),
    tenant_id: str | None = Query(None, description="Filter by tenant ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List workflow runs with filters and pagination."""
    async with async_session() as session:
        # Base query for filtering
        base_filter = select(Run)
        count_filter = select(func.count(Run.id))

        if status:
            base_filter = base_filter.where(Run.status == status)
            count_filter = count_filter.where(Run.status == status)
        if workflow:
            base_filter = base_filter.where(Run.workflow_name == workflow)
            count_filter = count_filter.where(Run.workflow_name == workflow)
        if since:
            base_filter = base_filter.where(Run.created_at >= since)
            count_filter = count_filter.where(Run.created_at >= since)
        if until:
            base_filter = base_filter.where(Run.created_at <= until)
            count_filter = count_filter.where(Run.created_at <= until)
        if tenant_id:
            base_filter = base_filter.where(Run.tenant_id == tenant_id)
            count_filter = count_filter.where(Run.tenant_id == tenant_id)

        # Get total count
        total = await session.scalar(count_filter)

        # Get paginated results
        stmt = base_filter.order_by(Run.created_at.desc()).offset(offset).limit(limit)
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

    return ApiResponse(
        data=items,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


# --- Schedules ---


@router.post("/schedules")
async def create_schedule(request: ScheduleCreateRequest) -> ApiResponse:
    """Create a scheduled workflow execution."""
    schedule_id = str(uuid.uuid4())

    try:
        async with async_session() as session:
            db_schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name=request.workflow_name,
                cron_expression=request.cron_expression,
                input_data=request.input_data,
                notify=request.notify,
                enabled=request.enabled,
            )
            session.add(db_schedule)
            await session.commit()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="DB_ERROR", message=f"Could not create schedule: {e}")
            ).model_dump(),
        )

    # Register with APScheduler if enabled
    if request.enabled:
        try:
            add_schedule(
                schedule_id=schedule_id,
                cron_expression=request.cron_expression,
                workflow_name=request.workflow_name,
                workflow_yaml="",  # Will need to be loaded at execution time
                input_data=request.input_data,
            )
        except Exception as e:
            logger.warning(f"Could not register schedule with APScheduler: {e}")

    return ApiResponse(
        data=ScheduleResponse(
            id=schedule_id,
            workflow_name=request.workflow_name,
            cron_expression=request.cron_expression,
            input_data=request.input_data,
            enabled=request.enabled,
        )
    )


@router.get("/schedules")
async def list_schedules(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List all workflow schedules."""
    async with async_session() as session:
        total = await session.scalar(select(func.count(Schedule.id)))

        stmt = select(Schedule).order_by(Schedule.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        schedules = result.scalars().all()

    items = [
        ScheduleResponse(
            id=str(s.id),
            workflow_name=s.workflow_name,
            cron_expression=s.cron_expression,
            input_data=s.input_data or {},
            enabled=s.enabled,
            last_run_id=str(s.last_run_id) if s.last_run_id else None,
            created_at=s.created_at,
        )
        for s in schedules
    ]

    return ApiResponse(
        data=items,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.patch("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, request: ScheduleUpdateRequest) -> ApiResponse:
    """Enable or disable a schedule."""
    try:
        schedule_uuid = uuid.UUID(schedule_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid schedule ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        schedule = await session.get(Schedule, schedule_uuid)
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Schedule '{schedule_id}' not found",
                    )
                ).model_dump(),
            )
        schedule.enabled = request.enabled
        await session.commit()

    # Update APScheduler
    if request.enabled:
        try:
            add_schedule(
                schedule_id=schedule_id,
                cron_expression=schedule.cron_expression,
                workflow_name=schedule.workflow_name,
                workflow_yaml="",
                input_data=schedule.input_data,
            )
        except Exception as e:
            logger.warning(f"Could not register schedule with APScheduler: {e}")
    else:
        remove_schedule(schedule_id)

    return ApiResponse(
        data=ScheduleResponse(
            id=str(schedule.id),
            workflow_name=schedule.workflow_name,
            cron_expression=schedule.cron_expression,
            input_data=schedule.input_data or {},
            enabled=schedule.enabled,
            last_run_id=str(schedule.last_run_id) if schedule.last_run_id else None,
            created_at=schedule.created_at,
        )
    )


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str) -> ApiResponse:
    """Delete a workflow schedule."""
    try:
        schedule_uuid = uuid.UUID(schedule_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid schedule ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        schedule = await session.get(Schedule, schedule_uuid)
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Schedule '{schedule_id}' not found",
                    )
                ).model_dump(),
            )
        await session.delete(schedule)
        await session.commit()

    # Remove from APScheduler
    remove_schedule(schedule_id)

    return ApiResponse(data={"deleted": True, "id": schedule_id})


# --- Dead Letter Queue ---


@router.get("/dead-letter")
async def list_dead_letter(
    resolved: bool = Query(False, description="Include resolved items"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List dead letter queue items."""
    async with async_session() as session:
        base = select(DeadLetterItem)
        count_base = select(func.count(DeadLetterItem.id))

        if not resolved:
            base = base.where(DeadLetterItem.resolved_at.is_(None))
            count_base = count_base.where(DeadLetterItem.resolved_at.is_(None))

        total = await session.scalar(count_base)

        stmt = base.order_by(DeadLetterItem.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        DeadLetterItemResponse(
            id=str(item.id),
            run_id=str(item.run_id),
            step_id=item.step_id,
            error=item.error,
            input_data=item.input_data,
            attempts=item.attempts,
            created_at=item.created_at,
            resolved_at=item.resolved_at,
            resolved_by=item.resolved_by,
        )
        for item in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.post("/dead-letter/{item_id}/retry")
async def retry_dead_letter(item_id: str) -> ApiResponse:
    """Retry a failed step from the dead letter queue."""
    try:
        item_uuid = uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid DLQ item ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        item = await session.get(DeadLetterItem, item_uuid)
        if not item:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="DLQ item not found")
                ).model_dump(),
            )

        if item.resolved_at:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(code="ALREADY_RESOLVED", message="Item already resolved")
                ).model_dump(),
            )

        # Mark as resolved by retry
        item.resolved_at = datetime.now(timezone.utc)
        item.resolved_by = "retry"
        await session.commit()

    # TODO: Re-enqueue the step for execution with the original input_data
    # For now, mark as resolved - full re-execution requires workflow context

    return ApiResponse(
        data=DeadLetterItemResponse(
            id=str(item.id),
            run_id=str(item.run_id),
            step_id=item.step_id,
            error=item.error,
            input_data=item.input_data,
            attempts=item.attempts,
            created_at=item.created_at,
            resolved_at=item.resolved_at,
            resolved_by=item.resolved_by,
        )
    )


@router.post("/dead-letter/{item_id}/resolve")
async def resolve_dead_letter(item_id: str, request: DeadLetterResolveRequest | None = None) -> ApiResponse:
    """Manually resolve a dead letter queue item."""
    try:
        item_uuid = uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid DLQ item ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        item = await session.get(DeadLetterItem, item_uuid)
        if not item:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="DLQ item not found")
                ).model_dump(),
            )

        if item.resolved_at:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(code="ALREADY_RESOLVED", message="Item already resolved")
                ).model_dump(),
            )

        item.resolved_at = datetime.now(timezone.utc)
        item.resolved_by = "manual"
        await session.commit()

    return ApiResponse(
        data=DeadLetterItemResponse(
            id=str(item.id),
            run_id=str(item.run_id),
            step_id=item.step_id,
            error=item.error,
            input_data=item.input_data,
            attempts=item.attempts,
            created_at=item.created_at,
            resolved_at=item.resolved_at,
            resolved_by=item.resolved_by,
        )
    )


# --- API Keys ---


@router.post("/api-keys")
async def create_api_key(request: ApiKeyCreateRequest) -> ApiResponse:
    """Create a new API key. Returns the plaintext key ONCE."""
    plaintext_key = generate_api_key()
    key_hash_value = hash_key(plaintext_key)

    try:
        async with async_session() as session:
            db_key = ApiKey(
                key_hash=key_hash_value,
                tenant_id=request.tenant_id,
                name=request.name,
            )
            session.add(db_key)
            await session.commit()
            await session.refresh(db_key)

            return ApiResponse(
                data=ApiKeyCreatedResponse(
                    id=str(db_key.id),
                    tenant_id=db_key.tenant_id,
                    name=db_key.name,
                    key=plaintext_key,
                )
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="DB_ERROR", message=f"Could not create API key: {e}")
            ).model_dump(),
        )


@router.get("/api-keys")
async def list_api_keys(
    tenant_id: str | None = Query(None, description="Filter by tenant ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List API keys (without plaintext)."""
    async with async_session() as session:
        base = select(ApiKey).where(ApiKey.is_active == True)
        count_base = select(func.count(ApiKey.id)).where(ApiKey.is_active == True)

        if tenant_id:
            base = base.where(ApiKey.tenant_id == tenant_id)
            count_base = count_base.where(ApiKey.tenant_id == tenant_id)

        total = await session.scalar(count_base)

        stmt = base.order_by(ApiKey.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        keys = result.scalars().all()

    data = [
        ApiKeyResponse(
            id=str(k.id),
            tenant_id=k.tenant_id,
            name=k.name,
            is_active=k.is_active,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.delete("/api-keys/{key_id}")
async def deactivate_api_key(key_id: str) -> ApiResponse:
    """Deactivate an API key (soft delete)."""
    try:
        key_uuid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid key ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        db_key = await session.get(ApiKey, key_uuid)
        if not db_key:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="API key not found")
                ).model_dump(),
            )

        db_key.is_active = False
        await session.commit()

    return ApiResponse(data={"deactivated": True, "id": key_id})
