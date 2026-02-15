"""API endpoints for Sandcastle."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from sandcastle.api.auth import generate_api_key, get_tenant_id, hash_key
from sandcastle.api.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    ApiResponse,
    ApprovalRespondRequest,
    ApprovalResponse,
    AutoPilotStatsResponse,
    DeadLetterItemResponse,
    DeadLetterResolveRequest,
    ErrorResponse,
    ExperimentResponse,
    ForkRequest,
    HealthResponse,
    PaginationMeta,
    ReplayRequest,
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
    ApprovalRequest,
    ApprovalStatus,
    AutoPilotExperiment,
    AutoPilotSample,
    DeadLetterItem,
    ExperimentStatus,
    Run,
    RunCheckpoint,
    RunStatus,
    Schedule,
    async_session,
)
from sandcastle.queue.scheduler import add_schedule, remove_schedule
from sandcastle.queue.worker import enqueue_workflow

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Helpers ---


def _load_workflow_yaml(workflow_name: str) -> str:
    """Load workflow YAML content from the workflows directory by name."""
    workflows_dir = Path(settings.workflows_dir)
    # Try exact match first, then with .yaml extension
    for candidate in [
        workflows_dir / f"{workflow_name}.yaml",
        workflows_dir / workflow_name,
    ]:
        if candidate.exists() and candidate.is_file():
            return candidate.read_text()
    raise FileNotFoundError(f"Workflow '{workflow_name}' not found in {workflows_dir}")


def _resolve_workflow_request(request: WorkflowRunRequest) -> str:
    """Resolve a WorkflowRunRequest to YAML content.

    Supports both raw YAML via `workflow` field and file reference via `workflow_name`.
    """
    if request.workflow:
        return request.workflow
    if request.workflow_name:
        return _load_workflow_yaml(request.workflow_name)
    raise ValueError("Either 'workflow' or 'workflow_name' must be provided")


def _apply_tenant_filter(stmt, tenant_id: str | None, column):
    """Apply tenant_id filter to a query when auth is enabled."""
    if settings.auth_required and tenant_id is not None:
        return stmt.where(column == tenant_id)
    return stmt


async def _resolve_budget(
    request_budget: float | None, tenant_id: str | None
) -> float | None:
    """Resolve max_cost_usd with precedence: request > tenant > env.

    Returns None if no budget is set (unlimited).
    """
    # 1. Request-level budget takes priority
    if request_budget is not None and request_budget > 0:
        return request_budget
    # 2. Tenant API key budget
    if tenant_id and settings.auth_required:
        try:
            async with async_session() as session:
                stmt = select(ApiKey.max_cost_per_run_usd).where(
                    ApiKey.tenant_id == tenant_id,
                    ApiKey.is_active.is_(True),
                ).limit(1)
                result = await session.scalar(stmt)
                if result and result > 0:
                    return result
        except Exception:
            pass
    # 3. Env-level default
    if settings.default_max_cost_usd > 0:
        return settings.default_max_cost_usd
    return None


# --- Health ---


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
async def get_stats(request: Request) -> ApiResponse:
    """Get aggregated statistics for the overview dashboard."""
    tenant_id = get_tenant_id(request)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with async_session() as session:
        # Base filters
        base = select(func.count(Run.id)).where(Run.created_at >= today_start)
        base = _apply_tenant_filter(base, tenant_id, Run.tenant_id)

        total_today = await session.scalar(base)

        completed_q = select(func.count(Run.id)).where(
            Run.created_at >= today_start,
            Run.status == RunStatus.COMPLETED,
        )
        completed_q = _apply_tenant_filter(completed_q, tenant_id, Run.tenant_id)
        completed_today = await session.scalar(completed_q)

        finished_q = select(func.count(Run.id)).where(
            Run.created_at >= today_start,
            Run.status.in_([RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PARTIAL]),
        )
        finished_q = _apply_tenant_filter(finished_q, tenant_id, Run.tenant_id)
        finished_today = await session.scalar(finished_q)
        success_rate = (completed_today / finished_today) if finished_today else 0.0

        cost_q = select(func.coalesce(func.sum(Run.total_cost_usd), 0.0)).where(
            Run.created_at >= today_start
        )
        cost_q = _apply_tenant_filter(cost_q, tenant_id, Run.tenant_id)
        total_cost = await session.scalar(cost_q)

        dur_q = select(
            func.avg(
                func.extract("epoch", Run.completed_at) - func.extract("epoch", Run.started_at)
            )
        ).where(
            Run.created_at >= today_start,
            Run.completed_at.isnot(None),
            Run.started_at.isnot(None),
        )
        dur_q = _apply_tenant_filter(dur_q, tenant_id, Run.tenant_id)
        avg_duration = await session.scalar(dur_q)

        # Runs by day (last 30 days)
        thirty_days_ago = now - timedelta(days=30)
        rbd_q = (
            select(
                func.date_trunc("day", Run.created_at).label("day"),
                Run.status,
                func.count(Run.id).label("count"),
            )
            .where(Run.created_at >= thirty_days_ago)
            .group_by("day", Run.status)
            .order_by("day")
        )
        rbd_q = _apply_tenant_filter(rbd_q, tenant_id, Run.tenant_id)
        runs_by_day_raw = (await session.execute(rbd_q)).all()

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
        cost_wf_q = (
            select(
                Run.workflow_name,
                func.coalesce(func.sum(Run.total_cost_usd), 0.0).label("cost"),
            )
            .where(Run.created_at >= seven_days_ago)
            .group_by(Run.workflow_name)
            .order_by(func.sum(Run.total_cost_usd).desc())
        )
        cost_wf_q = _apply_tenant_filter(cost_wf_q, tenant_id, Run.tenant_id)
        cost_by_workflow = [
            {"workflow": row.workflow_name, "cost": float(row.cost)}
            for row in (await session.execute(cost_wf_q)).all()
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
async def run_workflow_sync(request: WorkflowRunRequest, req: Request) -> ApiResponse:
    """Run a workflow synchronously. Blocks until complete."""
    tenant_id = get_tenant_id(req)

    try:
        yaml_content = _resolve_workflow_request(request)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    try:
        workflow = parse_yaml_string(yaml_content)
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

    # Resolve budget
    budget = await _resolve_budget(request.max_cost_usd, tenant_id)

    # Idempotency check (scoped to tenant)
    run_id = str(uuid.uuid4())
    if request.idempotency_key:
        async with async_session() as session:
            idemp_stmt = select(Run.id).where(Run.idempotency_key == request.idempotency_key)
            idemp_stmt = _apply_tenant_filter(idemp_stmt, tenant_id, Run.tenant_id)
            existing = await session.scalar(idemp_stmt)
            if existing:
                return ApiResponse(
                    data={"run_id": str(existing), "status": "existing", "idempotent": True},
                )

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
                tenant_id=tenant_id,
                idempotency_key=request.idempotency_key,
                max_cost_usd=budget,
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
        max_cost_usd=budget,
    )

    # Map result status to RunStatus
    status_map = {
        "completed": RunStatus.COMPLETED,
        "failed": RunStatus.FAILED,
        "cancelled": RunStatus.CANCELLED,
        "budget_exceeded": RunStatus.BUDGET_EXCEEDED,
        "awaiting_approval": RunStatus.AWAITING_APPROVAL,
    }

    # Update DB record
    try:
        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(run_id))
            if db_run:
                db_run.status = status_map.get(result.status, RunStatus.FAILED)
                db_run.output_data = result.outputs
                db_run.total_cost_usd = result.total_cost_usd
                if result.status != "awaiting_approval":
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
            max_cost_usd=budget,
            started_at=result.started_at,
            completed_at=result.completed_at,
            error=result.error,
        )
    )


@router.post("/workflows/run")
async def run_workflow_async(request: WorkflowRunRequest, req: Request) -> ApiResponse:
    """Run a workflow asynchronously. Returns immediately with run_id."""
    tenant_id = get_tenant_id(req)

    try:
        yaml_content = _resolve_workflow_request(request)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    try:
        workflow = parse_yaml_string(yaml_content)
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

    # Resolve budget
    budget = await _resolve_budget(request.max_cost_usd, tenant_id)

    # Idempotency check (scoped to tenant)
    if request.idempotency_key:
        async with async_session() as session:
            idemp_stmt = select(Run.id).where(Run.idempotency_key == request.idempotency_key)
            idemp_stmt = _apply_tenant_filter(idemp_stmt, tenant_id, Run.tenant_id)
            existing = await session.scalar(idemp_stmt)
            if existing:
                return ApiResponse(
                    data={"run_id": str(existing), "status": "existing", "idempotent": True},
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
                tenant_id=tenant_id,
                idempotency_key=request.idempotency_key,
                max_cost_usd=budget,
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

    # Enqueue the job - clean up orphan run on failure
    try:
        await enqueue_workflow(yaml_content, request.input, run_id)
    except Exception as e:
        # Mark the run as failed so it doesn't stay stuck as "queued"
        try:
            async with async_session() as session:
                db_run = await session.get(Run, uuid.UUID(run_id))
                if db_run:
                    db_run.status = RunStatus.FAILED
                    db_run.error = f"Failed to enqueue: {e}"
                    db_run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
        except Exception:
            logger.error(f"Could not clean up orphan run {run_id}")

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
async def get_run(run_id: str, req: Request) -> ApiResponse:
    """Get the status and details of a specific run."""
    tenant_id = get_tenant_id(req)

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
        stmt = (
            select(Run)
            .options(selectinload(Run.steps), selectinload(Run.children))
            .where(Run.id == run_uuid)
        )
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
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
            max_cost_usd=run.max_cost_usd,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error=run.error,
            steps=steps,
            parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
            replay_from_step=run.replay_from_step,
            fork_changes=run.fork_changes,
            depth=run.depth,
            sub_workflow_of_step=run.sub_workflow_of_step,
            sub_runs=[
                {
                    "run_id": str(c.id),
                    "workflow_name": c.workflow_name,
                    "status": c.status.value if hasattr(c.status, "value") else c.status,
                    "sub_workflow_of_step": c.sub_workflow_of_step,
                }
                for c in run.children
            ] if run.children else None,
        )
    )


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    """Stream live progress of a run via SSE."""
    tenant_id = get_tenant_id(request)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run ID format")

    # Verify the run belongs to the tenant before starting the stream
    async with async_session() as session:
        check_stmt = select(Run.id).where(Run.id == run_uuid)
        check_stmt = _apply_tenant_filter(check_stmt, tenant_id, Run.tenant_id)
        if not await session.scalar(check_stmt):
            raise HTTPException(status_code=404, detail="Run not found")

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
            if current_status in (
                "completed", "failed", "partial", "cancelled",
                "budget_exceeded", "awaiting_approval",
            ):
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
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    workflow: str | None = Query(None, description="Filter by workflow name"),
    since: datetime | None = Query(None, description="Filter runs created after this datetime"),
    until: datetime | None = Query(None, description="Filter runs created before this datetime"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List workflow runs with filters and pagination."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base_filter = select(Run)
        count_filter = select(func.count(Run.id))

        # Always apply tenant filter when auth is enabled
        base_filter = _apply_tenant_filter(base_filter, tenant_id, Run.tenant_id)
        count_filter = _apply_tenant_filter(count_filter, tenant_id, Run.tenant_id)

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

        total = await session.scalar(count_filter)

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
            parent_run_id=str(r.parent_run_id) if r.parent_run_id else None,
        )
        for r in runs
    ]

    return ApiResponse(
        data=items,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


# --- Cancel ---


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, req: Request) -> ApiResponse:
    """Cancel a running workflow. Sets a Redis flag checked by the executor."""
    tenant_id = get_tenant_id(req)

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
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
            ).model_dump(),
        )

    run_status = run.status.value if hasattr(run.status, "value") else run.status
    if run_status not in ("queued", "running"):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_STATUS",
                    message=f"Cannot cancel run with status '{run_status}'",
                )
            ).model_dump(),
        )

    # Set Redis cancel flag
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        await r.set(f"cancel:{run_id}", "1", ex=3600)  # 1h TTL
        await r.aclose()
    except Exception as e:
        logger.error(f"Could not set cancel flag in Redis: {e}")

    # Update DB status
    async with async_session() as session:
        db_run = await session.get(Run, run_uuid)
        if db_run:
            db_run.status = RunStatus.CANCELLED
            db_run.completed_at = datetime.now(timezone.utc)
            db_run.error = "Cancelled by user"
            await session.commit()

    return ApiResponse(
        data={"cancelled": True, "run_id": run_id},
    )


# --- Replay / Fork (Time Machine) ---


@router.post("/runs/{run_id}/replay")
async def replay_run(run_id: str, request: ReplayRequest, req: Request) -> ApiResponse:
    """Replay a run from a specific step using saved checkpoints."""
    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    # Load the original run
    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        original_run = result.scalar_one_or_none()

    if not original_run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
            ).model_dump(),
        )

    # Load workflow YAML and validate from_step
    try:
        yaml_content = _load_workflow_yaml(original_run.workflow_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="WORKFLOW_NOT_FOUND",
                    message=f"Workflow '{original_run.workflow_name}' not found on disk",
                )
            ).model_dump(),
        )

    # Validate from_step exists in the workflow
    try:
        wf_def = parse_yaml_string(yaml_content)
        valid_step_ids = {s.id for s in wf_def.steps}
    except Exception:
        valid_step_ids = set()
    if valid_step_ids and request.from_step not in valid_step_ids:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_STEP",
                    message=f"Step '{request.from_step}' not found in workflow "
                    f"'{original_run.workflow_name}'",
                )
            ).model_dump(),
        )

    # Find the checkpoint before the requested step
    async with async_session() as session:
        checkpoint_stmt = (
            select(RunCheckpoint)
            .where(RunCheckpoint.run_id == run_uuid)
            .order_by(RunCheckpoint.stage_index.desc())
        )
        result = await session.execute(checkpoint_stmt)
        checkpoints = result.scalars().all()

    # Find the newest checkpoint where from_step is NOT yet in step_outputs.
    # If no such checkpoint exists (from_step is the first step), use empty
    # context so the entire workflow replays from the beginning.
    target_checkpoint = None
    for cp in checkpoints:
        snapshot = cp.context_snapshot
        if request.from_step not in snapshot.get("step_outputs", {}):
            target_checkpoint = cp
            break

    initial_context = target_checkpoint.context_snapshot if target_checkpoint else None
    skip_steps = set(initial_context["step_outputs"].keys()) if initial_context else set()
    # Safety: never skip the step we're replaying from
    skip_steps.discard(request.from_step)

    # Create new run
    new_run_id = str(uuid.uuid4())
    async with async_session() as session:
        new_run = Run(
            id=uuid.UUID(new_run_id),
            workflow_name=original_run.workflow_name,
            status=RunStatus.QUEUED,
            input_data=original_run.input_data,
            callback_url=original_run.callback_url,
            tenant_id=tenant_id,
            parent_run_id=run_uuid,
            replay_from_step=request.from_step,
            max_cost_usd=original_run.max_cost_usd,
        )
        session.add(new_run)
        await session.commit()

    # Enqueue with replay context
    try:
        await enqueue_workflow(
            yaml_content,
            original_run.input_data or {},
            new_run_id,
            max_cost_usd=original_run.max_cost_usd,
            initial_context=initial_context,
            skip_steps=list(skip_steps),
        )
    except Exception as e:
        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(new_run_id))
            if db_run:
                db_run.status = RunStatus.FAILED
                db_run.error = f"Failed to enqueue replay: {e}"
                db_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="QUEUE_ERROR", message=f"Could not enqueue replay: {e}")
            ).model_dump(),
        )

    return ApiResponse(
        data={
            "new_run_id": new_run_id,
            "parent_run_id": run_id,
            "replay_from_step": request.from_step,
            "status": "queued",
        },
    )


@router.post("/runs/{run_id}/fork")
async def fork_run(run_id: str, request: ForkRequest, req: Request) -> ApiResponse:
    """Fork a run from a specific step with overrides (prompt, model, etc.)."""
    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    # Load the original run
    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        original_run = result.scalar_one_or_none()

    if not original_run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
            ).model_dump(),
        )

    # Load workflow YAML and validate from_step
    try:
        yaml_content = _load_workflow_yaml(original_run.workflow_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="WORKFLOW_NOT_FOUND",
                    message=f"Workflow '{original_run.workflow_name}' not found on disk",
                )
            ).model_dump(),
        )

    # Validate from_step exists in the workflow
    try:
        wf_def = parse_yaml_string(yaml_content)
        valid_step_ids = {s.id for s in wf_def.steps}
    except Exception:
        valid_step_ids = set()
    if valid_step_ids and request.from_step not in valid_step_ids:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_STEP",
                    message=f"Step '{request.from_step}' not found in workflow "
                    f"'{original_run.workflow_name}'",
                )
            ).model_dump(),
        )

    # Find the checkpoint before the requested step
    async with async_session() as session:
        checkpoint_stmt = (
            select(RunCheckpoint)
            .where(RunCheckpoint.run_id == run_uuid)
            .order_by(RunCheckpoint.stage_index.desc())
        )
        result = await session.execute(checkpoint_stmt)
        checkpoints = result.scalars().all()

    # Find the newest checkpoint where from_step is NOT yet in step_outputs
    target_checkpoint = None
    for cp in checkpoints:
        snapshot = cp.context_snapshot
        if request.from_step not in snapshot.get("step_outputs", {}):
            target_checkpoint = cp
            break

    initial_context = target_checkpoint.context_snapshot if target_checkpoint else None
    skip_steps = set(initial_context["step_outputs"].keys()) if initial_context else set()
    # Safety: never skip the step we're forking from
    skip_steps.discard(request.from_step)

    # Create new run with fork metadata
    new_run_id = str(uuid.uuid4())
    async with async_session() as session:
        new_run = Run(
            id=uuid.UUID(new_run_id),
            workflow_name=original_run.workflow_name,
            status=RunStatus.QUEUED,
            input_data=original_run.input_data,
            callback_url=original_run.callback_url,
            tenant_id=tenant_id,
            parent_run_id=run_uuid,
            replay_from_step=request.from_step,
            fork_changes=request.changes,
            max_cost_usd=original_run.max_cost_usd,
        )
        session.add(new_run)
        await session.commit()

    # Step overrides for the fork target step
    step_overrides = {request.from_step: request.changes} if request.changes else None

    try:
        await enqueue_workflow(
            yaml_content,
            original_run.input_data or {},
            new_run_id,
            max_cost_usd=original_run.max_cost_usd,
            initial_context=initial_context,
            skip_steps=list(skip_steps),
            step_overrides=step_overrides,
        )
    except Exception as e:
        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(new_run_id))
            if db_run:
                db_run.status = RunStatus.FAILED
                db_run.error = f"Failed to enqueue fork: {e}"
                db_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="QUEUE_ERROR", message=f"Could not enqueue fork: {e}")
            ).model_dump(),
        )

    return ApiResponse(
        data={
            "new_run_id": new_run_id,
            "parent_run_id": run_id,
            "fork_from_step": request.from_step,
            "changes": request.changes,
            "status": "queued",
        },
    )


# --- Schedules ---


@router.post("/schedules")
async def create_schedule(request: ScheduleCreateRequest, req: Request) -> ApiResponse:
    """Create a scheduled workflow execution."""
    tenant_id = get_tenant_id(req)

    # Validate that the workflow exists
    try:
        _load_workflow_yaml(request.workflow_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_WORKFLOW",
                    message=f"Workflow '{request.workflow_name}' not found",
                )
            ).model_dump(),
        )

    # Validate cron expression before saving
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(request.cron_expression)
    except (ValueError, KeyError) as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_CRON",
                    message=f"Invalid cron expression: {e}",
                )
            ).model_dump(),
        )

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
                tenant_id=tenant_id,
            )
            session.add(db_schedule)
            await session.commit()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="DB_ERROR",
                    message=f"Could not create schedule: {e}",
                )
            ).model_dump(),
        )

    # Register with APScheduler if enabled
    if request.enabled:
        add_schedule(
            schedule_id=schedule_id,
            cron_expression=request.cron_expression,
            workflow_name=request.workflow_name,
            input_data=request.input_data,
        )

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
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List all workflow schedules."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        count_stmt = select(func.count(Schedule.id))
        count_stmt = _apply_tenant_filter(count_stmt, tenant_id, Schedule.tenant_id)
        total = await session.scalar(count_stmt)

        stmt = select(Schedule).order_by(Schedule.created_at.desc()).offset(offset).limit(limit)
        stmt = _apply_tenant_filter(stmt, tenant_id, Schedule.tenant_id)
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
async def update_schedule(
    schedule_id: str, request: ScheduleUpdateRequest, req: Request,
) -> ApiResponse:
    """Enable or disable a schedule."""
    tenant_id = get_tenant_id(req)

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
        stmt = select(Schedule).where(Schedule.id == schedule_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Schedule.tenant_id)
        result = await session.execute(stmt)
        schedule = result.scalar_one_or_none()
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
        add_schedule(
            schedule_id=schedule_id,
            cron_expression=schedule.cron_expression,
            workflow_name=schedule.workflow_name,
            input_data=schedule.input_data,
        )
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
async def delete_schedule(schedule_id: str, request: Request) -> ApiResponse:
    """Delete a workflow schedule."""
    tenant_id = get_tenant_id(request)

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
        stmt = select(Schedule).where(Schedule.id == schedule_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Schedule.tenant_id)
        result = await session.execute(stmt)
        schedule = result.scalar_one_or_none()
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

    remove_schedule(schedule_id)

    return ApiResponse(data={"deleted": True, "id": schedule_id})


# --- Dead Letter Queue ---


@router.get("/dead-letter")
async def list_dead_letter(
    request: Request,
    resolved: bool = Query(False, description="Include resolved items"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List dead letter queue items."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base = select(DeadLetterItem)
        count_base = select(func.count(DeadLetterItem.id))

        # Tenant isolation via join on parent Run
        if settings.auth_required and tenant_id is not None:
            join_cond = DeadLetterItem.run_id == Run.id
            base = base.join(Run, join_cond).where(
                Run.tenant_id == tenant_id
            )
            count_base = count_base.join(Run, join_cond).where(
                Run.tenant_id == tenant_id
            )

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
            parallel_index=item.parallel_index,
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
async def retry_dead_letter(item_id: str, request: Request) -> ApiResponse:
    """Retry a failed step by re-running its parent workflow."""
    tenant_id = get_tenant_id(request)

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
        # Load DLQ item with tenant check via parent Run
        stmt = select(DeadLetterItem).where(DeadLetterItem.id == item_uuid)
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.join(Run, DeadLetterItem.run_id == Run.id).where(Run.tenant_id == tenant_id)
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()
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

        # Load the original run to get workflow name and input
        original_run = await session.get(Run, item.run_id)
        if not original_run:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="RUN_NOT_FOUND",
                        message="Original run not found, cannot retry",
                    )
                ).model_dump(),
            )

        # Mark DLQ item as resolved by retry
        item.resolved_at = datetime.now(timezone.utc)
        item.resolved_by = "retry"
        item.attempts += 1
        await session.commit()

    # Re-enqueue the workflow
    try:
        yaml_content = _load_workflow_yaml(original_run.workflow_name)
        new_run_id = str(uuid.uuid4())

        async with async_session() as session:
            new_run = Run(
                id=uuid.UUID(new_run_id),
                workflow_name=original_run.workflow_name,
                status=RunStatus.QUEUED,
                input_data=original_run.input_data,
                callback_url=original_run.callback_url,
                tenant_id=original_run.tenant_id,
                parent_run_id=original_run.id,
            )
            session.add(new_run)
            await session.commit()

        await enqueue_workflow(yaml_content, original_run.input_data or {}, new_run_id)
        logger.info(f"DLQ retry: created new run {new_run_id} for item {item_id}")

    except Exception as e:
        logger.error(f"DLQ retry failed for item {item_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="RETRY_ERROR", message=f"Could not retry: {e}")
            ).model_dump(),
        )

    return ApiResponse(
        data={
            "retried": True,
            "dlq_item_id": item_id,
            "new_run_id": new_run_id,
        },
    )


@router.post("/dead-letter/{item_id}/resolve")
async def resolve_dead_letter(
    item_id: str, req: Request,
    request: DeadLetterResolveRequest | None = None,
) -> ApiResponse:
    """Manually resolve a dead letter queue item."""
    tenant_id = get_tenant_id(req)

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
        stmt = select(DeadLetterItem).where(DeadLetterItem.id == item_uuid)
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.join(Run, DeadLetterItem.run_id == Run.id).where(Run.tenant_id == tenant_id)
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()
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
            parallel_index=item.parallel_index,
            error=item.error,
            input_data=item.input_data,
            attempts=item.attempts,
            created_at=item.created_at,
            resolved_at=item.resolved_at,
            resolved_by=item.resolved_by,
        )
    )


# --- AutoPilot ---


@router.get("/autopilot/experiments")
async def list_experiments(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List AutoPilot experiments."""
    async with async_session() as session:
        base = select(AutoPilotExperiment)
        count_base = select(func.count(AutoPilotExperiment.id))

        if status:
            base = base.where(AutoPilotExperiment.status == status)
            count_base = count_base.where(AutoPilotExperiment.status == status)

        total = await session.scalar(count_base)
        stmt = base.order_by(AutoPilotExperiment.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        ExperimentResponse(
            id=str(e.id),
            workflow_name=e.workflow_name,
            step_id=e.step_id,
            status=e.status.value if hasattr(e.status, "value") else e.status,
            optimize_for=e.optimize_for,
            config=e.config,
            deployed_variant_id=e.deployed_variant_id,
            created_at=e.created_at,
            completed_at=e.completed_at,
        )
        for e in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/autopilot/experiments/{experiment_id}")
async def get_experiment(experiment_id: str) -> ApiResponse:
    """Get experiment details with samples and stats."""
    try:
        exp_uuid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid experiment ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = (
            select(AutoPilotExperiment)
            .options(selectinload(AutoPilotExperiment.samples))
            .where(AutoPilotExperiment.id == exp_uuid)
        )
        result = await session.execute(stmt)
        experiment = result.scalar_one_or_none()

    if not experiment:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Experiment not found")
            ).model_dump(),
        )

    samples = [
        {
            "id": str(s.id),
            "variant_id": s.variant_id,
            "quality_score": s.quality_score,
            "cost_usd": s.cost_usd,
            "duration_seconds": s.duration_seconds,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in experiment.samples
    ]

    return ApiResponse(
        data=ExperimentResponse(
            id=str(experiment.id),
            workflow_name=experiment.workflow_name,
            step_id=experiment.step_id,
            status=(
                experiment.status.value
                if hasattr(experiment.status, "value")
                else experiment.status
            ),
            optimize_for=experiment.optimize_for,
            config=experiment.config,
            deployed_variant_id=experiment.deployed_variant_id,
            created_at=experiment.created_at,
            completed_at=experiment.completed_at,
            samples=samples,
        )
    )


@router.post("/autopilot/experiments/{experiment_id}/deploy")
async def deploy_experiment(experiment_id: str) -> ApiResponse:
    """Manually deploy a specific variant from an experiment."""
    try:
        exp_uuid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid experiment ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        experiment = await session.get(AutoPilotExperiment, exp_uuid)
        if not experiment:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="Experiment not found")
                ).model_dump(),
            )

        # Find the best performing variant
        from sandcastle.engine.autopilot import maybe_complete_experiment
        from sandcastle.engine.dag import AutoPilotConfig

        config = AutoPilotConfig(
            optimize_for=experiment.optimize_for,
            min_samples=0,  # Force completion
            auto_deploy=True,
            quality_threshold=0.0,
        )
        winner = await maybe_complete_experiment(exp_uuid, config)

        if not winner:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NO_SAMPLES", message="No samples to deploy from"
                    )
                ).model_dump(),
            )

    return ApiResponse(
        data={
            "deployed": True,
            "experiment_id": experiment_id,
            "variant_id": winner["variant_id"],
            "avg_quality": winner.get("avg_quality"),
        },
    )


@router.post("/autopilot/experiments/{experiment_id}/reset")
async def reset_experiment(experiment_id: str) -> ApiResponse:
    """Reset an experiment by deleting all samples and restarting."""
    try:
        exp_uuid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid experiment ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        experiment = await session.get(AutoPilotExperiment, exp_uuid)
        if not experiment:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="Experiment not found")
                ).model_dump(),
            )

        # Delete all samples
        from sqlalchemy import delete

        await session.execute(
            delete(AutoPilotSample).where(AutoPilotSample.experiment_id == exp_uuid)
        )

        # Reset experiment
        experiment.status = ExperimentStatus.RUNNING
        experiment.deployed_variant_id = None
        experiment.completed_at = None
        await session.commit()

    return ApiResponse(data={"reset": True, "experiment_id": experiment_id})


@router.get("/autopilot/stats")
async def autopilot_stats() -> ApiResponse:
    """Get overall AutoPilot savings and experiment statistics."""
    async with async_session() as session:
        total = await session.scalar(select(func.count(AutoPilotExperiment.id)))
        active = await session.scalar(
            select(func.count(AutoPilotExperiment.id)).where(
                AutoPilotExperiment.status == ExperimentStatus.RUNNING
            )
        )
        completed = await session.scalar(
            select(func.count(AutoPilotExperiment.id)).where(
                AutoPilotExperiment.status == ExperimentStatus.COMPLETED
            )
        )
        total_samples = await session.scalar(select(func.count(AutoPilotSample.id)))

    return ApiResponse(
        data=AutoPilotStatsResponse(
            total_experiments=total or 0,
            active_experiments=active or 0,
            completed_experiments=completed or 0,
            total_samples=total_samples or 0,
        )
    )


# --- Approval Gates ---


@router.get("/approvals")
async def list_approvals(
    request: Request,
    status: str | None = Query(None, description="Filter by status (pending, approved, etc.)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List approval requests, scoped to tenant."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base = select(ApprovalRequest)
        count_base = select(func.count(ApprovalRequest.id))

        # Tenant isolation via join on parent Run
        if settings.auth_required and tenant_id is not None:
            join_cond = ApprovalRequest.run_id == Run.id
            base = base.join(Run, join_cond).where(Run.tenant_id == tenant_id)
            count_base = count_base.join(Run, join_cond).where(Run.tenant_id == tenant_id)

        if status:
            base = base.where(ApprovalRequest.status == status)
            count_base = count_base.where(ApprovalRequest.status == status)

        total = await session.scalar(count_base)

        stmt = base.order_by(ApprovalRequest.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        ApprovalResponse(
            id=str(a.id),
            run_id=str(a.run_id),
            step_id=a.step_id,
            status=a.status.value if hasattr(a.status, "value") else a.status,
            request_data=a.request_data,
            response_data=a.response_data,
            message=a.message,
            reviewer_id=a.reviewer_id,
            reviewer_comment=a.reviewer_comment,
            timeout_at=a.timeout_at,
            on_timeout=a.on_timeout,
            allow_edit=a.allow_edit,
            created_at=a.created_at,
            resolved_at=a.resolved_at,
        )
        for a in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, request: Request) -> ApiResponse:
    """Get details of a specific approval request."""
    tenant_id = get_tenant_id(request)

    try:
        approval_uuid = uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid approval ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(ApprovalRequest).where(ApprovalRequest.id == approval_uuid)
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.join(Run, ApprovalRequest.run_id == Run.id).where(
                Run.tenant_id == tenant_id
            )
        result = await session.execute(stmt)
        approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Approval request not found")
            ).model_dump(),
        )

    return ApiResponse(
        data=ApprovalResponse(
            id=str(approval.id),
            run_id=str(approval.run_id),
            step_id=approval.step_id,
            status=approval.status.value if hasattr(approval.status, "value") else approval.status,
            request_data=approval.request_data,
            response_data=approval.response_data,
            message=approval.message,
            reviewer_id=approval.reviewer_id,
            reviewer_comment=approval.reviewer_comment,
            timeout_at=approval.timeout_at,
            on_timeout=approval.on_timeout,
            allow_edit=approval.allow_edit,
            created_at=approval.created_at,
            resolved_at=approval.resolved_at,
        )
    )


async def _resolve_approval(
    approval_id: str,
    tenant_id: str | None,
    action: str,
    request_body: ApprovalRespondRequest | None = None,
) -> ApprovalRequest:
    """Shared logic for approve/reject/skip actions."""
    try:
        approval_uuid = uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid approval ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(ApprovalRequest).where(ApprovalRequest.id == approval_uuid)
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.join(Run, ApprovalRequest.run_id == Run.id).where(
                Run.tenant_id == tenant_id
            )
        result = await session.execute(stmt)
        approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Approval request not found")
            ).model_dump(),
        )

    ap_status = approval.status.value if hasattr(approval.status, "value") else approval.status
    if ap_status != "pending":
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="ALREADY_RESOLVED",
                    message=f"Approval already resolved with status '{ap_status}'",
                )
            ).model_dump(),
        )

    return approval


@router.post("/approvals/{approval_id}/approve")
async def approve_approval(
    approval_id: str,
    req: Request,
    request: ApprovalRespondRequest | None = None,
) -> ApiResponse:
    """Approve an approval gate and resume the workflow."""
    tenant_id = get_tenant_id(req)
    approval = await _resolve_approval(approval_id, tenant_id, "approve", request)

    now = datetime.now(timezone.utc)
    response_data = approval.request_data  # Default: use original data

    # Apply edited data if allowed and provided
    if request and request.edited_data and approval.allow_edit:
        response_data = request.edited_data

    async with async_session() as session:
        ap = await session.get(ApprovalRequest, approval.id)
        if ap:
            ap.status = ApprovalStatus.APPROVED
            ap.resolved_at = now
            ap.response_data = response_data
            if request and request.comment:
                ap.reviewer_comment = request.comment
            await session.commit()

    # Resume the workflow
    await _resume_after_approval(
        approval, output_data=response_data or {"approved": True}
    )

    return ApiResponse(
        data={"approved": True, "approval_id": approval_id, "run_id": str(approval.run_id)},
    )


@router.post("/approvals/{approval_id}/reject")
async def reject_approval(
    approval_id: str,
    req: Request,
    request: ApprovalRespondRequest | None = None,
) -> ApiResponse:
    """Reject an approval gate and fail the workflow."""
    tenant_id = get_tenant_id(req)
    approval = await _resolve_approval(approval_id, tenant_id, "reject", request)

    now = datetime.now(timezone.utc)

    async with async_session() as session:
        ap = await session.get(ApprovalRequest, approval.id)
        if ap:
            ap.status = ApprovalStatus.REJECTED
            ap.resolved_at = now
            if request and request.comment:
                ap.reviewer_comment = request.comment
            await session.commit()

        # Fail the run
        run = await session.get(Run, approval.run_id)
        if run:
            run.status = RunStatus.FAILED
            run.completed_at = now
            run.error = f"Approval rejected at step '{approval.step_id}'"
            await session.commit()

    return ApiResponse(
        data={"rejected": True, "approval_id": approval_id, "run_id": str(approval.run_id)},
    )


@router.post("/approvals/{approval_id}/skip")
async def skip_approval(
    approval_id: str,
    req: Request,
    request: ApprovalRespondRequest | None = None,
) -> ApiResponse:
    """Skip an approval gate and continue the workflow."""
    tenant_id = get_tenant_id(req)
    approval = await _resolve_approval(approval_id, tenant_id, "skip", request)

    now = datetime.now(timezone.utc)

    async with async_session() as session:
        ap = await session.get(ApprovalRequest, approval.id)
        if ap:
            ap.status = ApprovalStatus.SKIPPED
            ap.resolved_at = now
            if request and request.comment:
                ap.reviewer_comment = request.comment
            await session.commit()

    # Resume with null output for skipped step
    await _resume_after_approval(approval, output_data=None)

    return ApiResponse(
        data={"skipped": True, "approval_id": approval_id, "run_id": str(approval.run_id)},
    )


async def _resume_after_approval(
    approval: ApprovalRequest,
    output_data: dict | None,
) -> None:
    """Resume a workflow after an approval gate is resolved.

    Loads the checkpoint, sets the approval step output, and re-enqueues.
    """
    run_id = str(approval.run_id)
    step_id = approval.step_id

    # Load the run to get workflow info
    async with async_session() as session:
        run = await session.get(Run, approval.run_id)
        if not run:
            logger.error(f"Cannot resume: run {run_id} not found")
            return

        workflow_name = run.workflow_name
        input_data = run.input_data or {}
        max_cost_usd = run.max_cost_usd

    # Load workflow YAML
    try:
        yaml_content = _load_workflow_yaml(workflow_name)
    except FileNotFoundError:
        logger.error(f"Cannot resume: workflow '{workflow_name}' not found")
        return

    # Find the checkpoint (saved before the approval step)
    async with async_session() as session:
        checkpoint_stmt = (
            select(RunCheckpoint)
            .where(RunCheckpoint.run_id == approval.run_id)
            .order_by(RunCheckpoint.stage_index.desc())
        )
        result = await session.execute(checkpoint_stmt)
        checkpoints = result.scalars().all()

    # Use the latest checkpoint
    initial_context = checkpoints[0].context_snapshot if checkpoints else None

    # Set the approval step output in the context
    if initial_context:
        initial_context["step_outputs"][step_id] = output_data
    else:
        initial_context = {"step_outputs": {step_id: output_data}, "costs": []}

    # Steps already completed (including the approval step now)
    skip_steps = list(initial_context["step_outputs"].keys())

    # Enqueue continuation
    try:
        await enqueue_workflow(
            yaml_content,
            input_data,
            run_id,
            max_cost_usd=max_cost_usd,
            initial_context=initial_context,
            skip_steps=skip_steps,
        )
        logger.info(f"Resumed workflow {run_id} after approval of step '{step_id}'")
    except Exception as e:
        logger.error(f"Failed to resume workflow {run_id}: {e}")
        async with async_session() as session:
            run = await session.get(Run, approval.run_id)
            if run:
                run.status = RunStatus.FAILED
                run.error = f"Failed to resume after approval: {e}"
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()


# --- API Keys ---


@router.post("/api-keys")
async def create_api_key(request: ApiKeyCreateRequest, req: Request) -> ApiResponse:
    """Create a new API key. Returns the plaintext key ONCE."""
    auth_tenant = get_tenant_id(req)

    # When auth is enabled, enforce tenant scoping: can only create keys for own tenant
    if settings.auth_required and auth_tenant is not None and request.tenant_id != auth_tenant:
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="FORBIDDEN",
                    message="Cannot create API keys for a different tenant",
                )
            ).model_dump(),
        )

    plaintext_key = generate_api_key()
    key_hash_value = hash_key(plaintext_key)
    key_prefix = plaintext_key[:8]

    try:
        async with async_session() as session:
            db_key = ApiKey(
                key_hash=key_hash_value,
                key_prefix=key_prefix,
                tenant_id=request.tenant_id,
                name=request.name,
                max_cost_per_run_usd=request.max_cost_per_run_usd,
            )
            session.add(db_key)
            await session.commit()
            await session.refresh(db_key)

            return ApiResponse(
                data=ApiKeyCreatedResponse(
                    id=str(db_key.id),
                    key_prefix=key_prefix,
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
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List API keys (without plaintext). Scoped to tenant when auth is enabled."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base = select(ApiKey).where(ApiKey.is_active.is_(True))
        count_base = select(func.count(ApiKey.id)).where(ApiKey.is_active.is_(True))

        # Tenant isolation - only see own keys
        base = _apply_tenant_filter(base, tenant_id, ApiKey.tenant_id)
        count_base = _apply_tenant_filter(count_base, tenant_id, ApiKey.tenant_id)

        total = await session.scalar(count_base)

        stmt = base.order_by(ApiKey.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        keys = result.scalars().all()

    data = [
        ApiKeyResponse(
            id=str(k.id),
            key_prefix=k.key_prefix,
            tenant_id=k.tenant_id,
            name=k.name,
            is_active=k.is_active,
            max_cost_per_run_usd=k.max_cost_per_run_usd,
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
async def deactivate_api_key(key_id: str, request: Request) -> ApiResponse:
    """Deactivate an API key (soft delete)."""
    tenant_id = get_tenant_id(request)

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
        stmt = select(ApiKey).where(ApiKey.id == key_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, ApiKey.tenant_id)
        result = await session.execute(stmt)
        db_key = result.scalar_one_or_none()
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
