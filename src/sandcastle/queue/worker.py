"""Queue worker - arq (Redis) or in-process (asyncio) for local mode."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone

from arq.worker import func

from sandcastle.config import settings

logger = logging.getLogger(__name__)

# Track in-process background tasks to prevent GC and surface exceptions
_background_tasks: set[asyncio.Task] = set()

# Shared Redis pool for enqueue operations (avoids creating a new pool per call)
_enqueue_redis_pool = None
# Use threading.Lock for module-level init guard (safe at import time, before event loop)
_enqueue_redis_thread_lock = threading.Lock()
# Per-coroutine asyncio lock (created lazily inside the event loop)
_enqueue_redis_lock: asyncio.Lock | None = None


def _get_enqueue_lock() -> asyncio.Lock:
    """Return the asyncio lock for Redis pool creation, creating it lazily.

    Uses the threading lock to prevent multiple asyncio locks from being
    created if called from different coroutines near-simultaneously before
    the first one finishes assignment.
    """
    global _enqueue_redis_lock
    if _enqueue_redis_lock is None:
        with _enqueue_redis_thread_lock:
            if _enqueue_redis_lock is None:
                _enqueue_redis_lock = asyncio.Lock()
    return _enqueue_redis_lock


async def cleanup_enqueue_pool() -> None:
    """Close the shared Redis connection pool if it exists.

    Called during worker shutdown to prevent leaked Redis connections.
    Safe to call multiple times or when no pool has been created.
    """
    global _enqueue_redis_pool
    if _enqueue_redis_pool is not None:
        try:
            await _enqueue_redis_pool.close()
            logger.info("Closed shared Redis enqueue connection pool")
        except Exception as e:
            logger.warning("Error closing Redis enqueue pool: %s", e)
        finally:
            _enqueue_redis_pool = None


def _task_done_callback(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget background tasks."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background workflow task failed with unhandled exception: %s", exc,
            exc_info=exc,
        )


def _parse_redis_url(url: str):
    """Parse a Redis URL into arq RedisSettings."""
    from urllib.parse import urlparse

    from arq.connections import RedisSettings

    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or 0),
        password=parsed.password,
    )


def uses_redis_queue() -> bool:
    """Return whether workflow jobs are dispatched through the Redis queue."""
    return bool(settings.redis_url)


async def run_workflow_job(
    ctx: dict,
    workflow_yaml: str,
    input_data: dict,
    run_id: str,
    max_cost_usd: float | None = None,
    initial_context: dict | None = None,
    skip_steps: list[str] | None = None,
    step_overrides: dict | None = None,
    admin_trusted: bool = False,
) -> dict:
    """Arq job: execute a workflow asynchronously.

    Updates the database with progress and results. Dispatches webhooks.
    Supports budget limits, replay (initial_context + skip_steps), and fork (step_overrides).
    """
    from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
    from sandcastle.engine.executor import execute_workflow
    from sandcastle.engine.storage import create_storage
    from sandcastle.models.db import Run, RunStatus, async_session
    from sandcastle.webhooks.dispatcher import dispatch_webhook

    logger.info(f"Worker picked up run {run_id}")

    import uuid as _uuid
    run_uuid = _uuid.UUID(run_id) if isinstance(run_id, str) else run_id

    callback_url = None

    async with async_session() as session:
        run = await session.get(Run, run_uuid)
        if not run:
            logger.error(f"Run {run_id} not found in database, cannot execute")
            return {"run_id": run_id, "status": "failed", "error": "Run record not found"}
        # Guard against duplicate delivery: only transition from QUEUED
        if run.status != RunStatus.QUEUED:
            logger.warning(
                f"Run {run_id} in state {run.status.value} (expected QUEUED), "
                "skipping duplicate execution"
            )
            return {"run_id": run_id, "status": run.status.value}
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        callback_url = run.callback_url
        # Use budget from DB if not passed explicitly
        if max_cost_usd is None and run.max_cost_usd:
            max_cost_usd = run.max_cost_usd
        await session.commit()

    try:
        workflow = parse_yaml_string(workflow_yaml)
        errors = validate(workflow)
        if errors:
            raise ValueError(f"Workflow validation failed: {'; '.join(errors)}")

        plan = build_plan(workflow)
        storage = create_storage()

        result = await execute_workflow(
            workflow=workflow,
            plan=plan,
            input_data=input_data,
            run_id=run_id,
            storage=storage,
            max_cost_usd=max_cost_usd,
            initial_context=initial_context,
            skip_steps=set(skip_steps) if skip_steps else None,
            step_overrides=step_overrides,
            admin_trusted=admin_trusted,
            tenant_id=run.tenant_id,
        )

        # Map result status to RunStatus
        status_map = {
            "completed": RunStatus.COMPLETED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
            "budget_exceeded": RunStatus.BUDGET_EXCEEDED,
            "awaiting_approval": RunStatus.AWAITING_APPROVAL,
        }

        # Update DB with result (retry up to 3 times on failure)
        db_persist_ok = False
        for db_attempt in range(1, 4):
            try:
                async with async_session() as session:
                    run = await session.get(Run, run_uuid)
                    if run:
                        run.status = status_map.get(result.status, RunStatus.FAILED)
                        storage_outputs = getattr(result, "storage_outputs", None)
                        persisted_outputs = (
                            storage_outputs
                            if isinstance(storage_outputs, dict)
                            else result.outputs
                        )
                        output_with_report = (
                            dict(persisted_outputs)
                            if persisted_outputs
                            else {}
                        )
                        if getattr(result, "token_report", None):
                            output_with_report["_token_report"] = result.token_report
                        from sandcastle.engine.json_utils import json_safe

                        run.output_data = json_safe(output_with_report)
                        run.total_cost_usd = result.total_cost_usd
                        # Don't set completed_at for paused workflows
                        if result.status != "awaiting_approval":
                            run.completed_at = datetime.now(timezone.utc)
                        run.error = result.error
                        await session.commit()
                db_persist_ok = True
                break
            except Exception as db_err:
                logger.warning(
                    f"DB persist attempt {db_attempt}/3 failed for run {run_id}: {db_err}"
                )
                if db_attempt < 3:
                    await asyncio.sleep(1)
        if not db_persist_ok:
            logger.error(
                f"Failed to persist result for run {run_id} after 3 attempts. "
                f"Workflow completed but DB update failed. "
                f"Status={result.status}, outputs={result.outputs}"
            )

        # Dispatch webhook - on_complete or on_failure depending on status
        webhook_urls = []
        if callback_url:
            webhook_urls.append(callback_url)

        if result.status == "completed":
            if not callback_url and workflow.on_complete and workflow.on_complete.webhook:
                webhook_urls.append(workflow.on_complete.webhook)
        elif result.status == "failed":
            # Only fire on_failure webhook for actual failures, not for
            # cancelled/budget_exceeded/awaiting_approval statuses
            if workflow.on_failure and workflow.on_failure.webhook:
                webhook_urls.append(workflow.on_failure.webhook)

        # Determine the webhook event type based on actual status
        _event_map = {
            "completed": "workflow.completed",
            "failed": "workflow.failed",
            "cancelled": "workflow.cancelled",
            "budget_exceeded": "workflow.budget_exceeded",
            "awaiting_approval": "workflow.awaiting_approval",
        }
        event_type = _event_map.get(result.status, "workflow.failed")

        webhook_urls = list(dict.fromkeys(webhook_urls))
        for webhook_url in webhook_urls:
            duration = 0.0
            if result.started_at and result.completed_at:
                duration = (result.completed_at - result.started_at).total_seconds()
            result_webhook_outputs = getattr(result, "webhook_outputs", None)

            await dispatch_webhook(
                url=webhook_url,
                event=event_type,
                run_id=run_id,
                workflow=workflow.name,
                status=result.status,
                outputs=(
                    result_webhook_outputs
                    if isinstance(result_webhook_outputs, dict)
                    else result.outputs
                ),
                costs=result.total_cost_usd,
                duration_seconds=duration,
                error=result.error,
            )

        logger.info(f"Run {run_id} completed with status {result.status}")
        return {"run_id": run_id, "status": result.status}

    except Exception as e:
        logger.error(f"Run {run_id} failed: {e}")
        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            if run:
                run.status = RunStatus.FAILED
                run.completed_at = datetime.now(timezone.utc)
                run.error = str(e)[:4096]
                await session.commit()

        # Dispatch failure webhook - try callback_url, then on_failure.webhook
        failure_urls = []
        if callback_url:
            failure_urls.append(callback_url)
        try:
            wf = parse_yaml_string(workflow_yaml)
            if wf.on_failure and wf.on_failure.webhook:
                failure_urls.append(wf.on_failure.webhook)
        except Exception:
            pass

        failure_urls = list(dict.fromkeys(failure_urls))
        for url in failure_urls:
            try:
                await dispatch_webhook(
                    url=url,
                    event="workflow.failed",
                    run_id=run_id,
                    workflow="unknown",
                    status="failed",
                    error=str(e),
                )
            except Exception as wh_err:
                logger.error(f"Failed to dispatch failure webhook to {url}: {wh_err}")

        return {"run_id": run_id, "status": "failed", "error": str(e)}


async def run_evolution_job(
    ctx: dict,
    evolution_id: str,
    workflow_name: str,
    eval_suite_yaml: str,
    max_iterations: int,
    optimize_for: str,
    budget_limit: float | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Arq job: execute a persisted workflow evolution experiment."""
    import uuid as _uuid

    from sandcastle.engine.evolution import run_evolution
    from sandcastle.models.db import WorkflowEvolution, async_session

    evolution_uuid = _uuid.UUID(evolution_id)
    async with async_session() as session:
        evolution = await session.get(WorkflowEvolution, evolution_uuid)
        if evolution is None:
            logger.error("Evolution %s not found in database, cannot execute", evolution_id)
            return {
                "evolution_id": evolution_id,
                "status": "failed",
                "error": "Evolution record not found",
            }
        if evolution.status not in ("queued", "running"):
            logger.info(
                "Evolution %s is already in state %s; skipping queued execution",
                evolution_id,
                evolution.status,
            )
            return {"evolution_id": evolution_id, "status": evolution.status}
        evolution.status = "running"
        await session.commit()

    try:
        result = await run_evolution(
            workflow_name=workflow_name,
            eval_suite_yaml=eval_suite_yaml,
            max_iterations=max_iterations,
            optimize_for=optimize_for,
            budget_limit=budget_limit,
            tenant_id=tenant_id,
            evolution_id=evolution_uuid,
            record_exists=True,
        )
        if result.get("status") == "failed":
            await _mark_evolution_failed(evolution_id, result.get("error"))
        return result
    except asyncio.CancelledError:
        await _mark_evolution_failed(evolution_id, "Evolution job was interrupted")
        raise
    except Exception as exc:
        logger.exception("Evolution job %s failed", evolution_id)
        await _mark_evolution_failed(evolution_id, str(exc))
        return {"evolution_id": evolution_id, "status": "failed", "error": str(exc)}


async def _recover_stuck_runs() -> None:
    """Recover runs stuck in RUNNING after a worker crash.

    Finds runs that have been RUNNING for longer than twice the job timeout
    and marks them as FAILED. QUEUED jobs can legitimately remain in Redis
    during a backlog, so they are left for the queue to deliver. Called during
    worker startup to clean up after unexpected shutdowns.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from sandcastle.models.db import Run, RunStatus, async_session

    timeout_seconds = int(os.environ.get("SANDCASTLE_WORKER_JOB_TIMEOUT", "600"))
    threshold = timedelta(seconds=2 * timeout_seconds)
    cutoff = datetime.now(timezone.utc) - threshold

    try:
        async with async_session() as session:
            # Find runs stuck in RUNNING that started before the cutoff
            stmt_running = select(Run).where(
                Run.status == RunStatus.RUNNING,
                Run.started_at.isnot(None),
                Run.started_at <= cutoff,
            )
            result = await session.execute(stmt_running)
            stuck_running = result.scalars().all()

            recovered = 0
            now = datetime.now(timezone.utc)
            for run in stuck_running:
                run.status = RunStatus.FAILED
                run.completed_at = now
                run.error = "Worker crashed or timed out - recovered on startup"
                recovered += 1

            if recovered:
                await session.commit()
                logger.warning(
                    "Recovered %d stuck run(s) (threshold=%ds)",
                    recovered,
                    2 * timeout_seconds,
                )
    except Exception as e:
        logger.error("Failed to recover stuck runs on startup: %s", e)


async def _recover_stuck_evolutions() -> None:
    """Fail evolution jobs left running beyond twice their configured timeout."""
    from datetime import timedelta

    from sqlalchemy import update

    from sandcastle.models.db import WorkflowEvolution, async_session

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=2 * settings.evolution_job_timeout
    )
    try:
        async with async_session() as session:
            result = await session.execute(
                update(WorkflowEvolution)
                .where(
                    WorkflowEvolution.status == "running",
                    WorkflowEvolution.created_at <= cutoff,
                )
                .values(
                    status="failed",
                    completed_at=datetime.now(timezone.utc),
                    error="Worker crashed or evolution job timed out",
                )
            )
            if result.rowcount:
                await session.commit()
                logger.warning(
                    "Recovered %d stuck evolution job(s)",
                    result.rowcount,
                )
    except Exception as exc:
        logger.error("Failed to recover stuck evolutions on startup: %s", exc)


async def startup(ctx: dict) -> None:
    """Worker startup hook."""
    logger.info("Sandcastle worker starting up")
    # Dashboard-managed settings (provider API keys, workflow_default_model, ...)
    # live in the DB; without this the worker executed steps with env defaults
    # regardless of what the user configured.
    try:
        from sandcastle.db_settings import restore_db_settings

        await restore_db_settings()
    except Exception as e:  # noqa: BLE001 - startup must not die on a bad setting
        logger.warning("Could not restore DB settings on worker startup: %s", e)
    await _recover_stuck_runs()
    await _recover_stuck_evolutions()


async def shutdown(ctx: dict) -> None:
    """Worker shutdown hook.

    Cleans up the shared Redis connection pool to prevent leaked connections
    on process exit.
    """
    logger.info("Sandcastle worker shutting down")
    await cleanup_enqueue_pool()


class WorkerSettings:
    """Arq worker settings (only used with Redis)."""

    functions = [
        run_workflow_job,
        func(run_evolution_job, timeout=settings.evolution_job_timeout),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = int(os.environ.get("SANDCASTLE_WORKER_MAX_JOBS", "10"))
    job_timeout = int(os.environ.get("SANDCASTLE_WORKER_JOB_TIMEOUT", "600"))
    redis_settings = None


# Lazy init: only set redis_settings when Redis is configured
if uses_redis_queue():
    WorkerSettings.redis_settings = _parse_redis_url(settings.redis_url)


async def _mark_run_timed_out(run_id: str, timeout_seconds: int) -> None:
    """Mark an in-process job as failed after its worker timeout expires."""
    import uuid as _uuid

    from sandcastle.models.db import Run, RunStatus, async_session

    error_message = f"Workflow job timed out after {timeout_seconds} seconds"
    try:
        async with async_session() as session:
            run = await session.get(Run, _uuid.UUID(run_id))
            if run and run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                run.status = RunStatus.FAILED
                run.error = error_message
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                logger.error("Marked run %s as FAILED: %s", run_id, error_message)
    except Exception as db_err:
        logger.error("Failed to mark timed-out run %s as FAILED: %s", run_id, db_err)


async def enqueue_workflow(
    workflow_yaml: str,
    input_data: dict,
    run_id: str,
    max_cost_usd: float | None = None,
    initial_context: dict | None = None,
    skip_steps: list[str] | None = None,
    step_overrides: dict | None = None,
    admin_trusted: bool = False,
    mark_failed_on_error: bool = True,
) -> None:
    """Enqueue a workflow job - via Redis (arq) or in-process (asyncio.create_task).

    If Redis enqueue fails (connection error, timeout, etc.), the run is
    marked as FAILED in the database so it does not stay stuck in QUEUED
    status forever.

    Note on timeouts: The arq job_timeout (default 600s, set via
    SANDCASTLE_WORKER_JOB_TIMEOUT) must be >= the maximum sandbox execution
    timeout defined in workflow YAML files. If a workflow's sandbox timeout
    exceeds job_timeout, arq will kill the job mid-execution. Adjust
    SANDCASTLE_WORKER_JOB_TIMEOUT accordingly for long-running workflows.
    """
    global _enqueue_redis_pool

    if uses_redis_queue():
        # Production mode: enqueue via arq/Redis using a shared connection pool
        from arq import create_pool

        try:
            if _enqueue_redis_pool is None:
                async with _get_enqueue_lock():
                    if _enqueue_redis_pool is None:
                        _enqueue_redis_pool = await create_pool(
                            _parse_redis_url(settings.redis_url)
                        )

            await _enqueue_redis_pool.enqueue_job(
                "run_workflow_job",
                workflow_yaml,
                input_data,
                run_id,
                max_cost_usd=max_cost_usd,
                initial_context=initial_context,
                skip_steps=skip_steps,
                step_overrides=step_overrides,
                admin_trusted=admin_trusted,
                _job_id=run_id,
            )
        except Exception as enqueue_err:
            logger.error(
                "Failed to enqueue run %s to Redis: %s", run_id, enqueue_err
            )
            if mark_failed_on_error:
                # Mark newly submitted runs as FAILED so they do not remain
                # stuck in QUEUED when their initial enqueue fails.
                await _mark_run_failed(
                    run_id,
                    f"Redis enqueue failed: {enqueue_err}",
                )
            raise
    else:
        # Local mode: run directly in-process
        logger.info(f"Local mode: executing run {run_id} in-process")

        async def run_with_timeout() -> None:
            timeout_seconds = int(os.environ.get("SANDCASTLE_WORKER_JOB_TIMEOUT", "600"))
            try:
                await asyncio.wait_for(
                    run_workflow_job(
                        {},  # empty ctx for in-process
                        workflow_yaml,
                        input_data,
                        run_id,
                        max_cost_usd=max_cost_usd,
                        initial_context=initial_context,
                        skip_steps=skip_steps,
                        step_overrides=step_overrides,
                        admin_trusted=admin_trusted,
                    ),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Local workflow run %s exceeded worker timeout of %d seconds",
                    run_id,
                    timeout_seconds,
                )
                await _mark_run_timed_out(run_id, timeout_seconds)

        task = asyncio.create_task(
            run_with_timeout(),
            name=f"workflow-{run_id}",
        )
        _background_tasks.add(task)
        task.add_done_callback(_task_done_callback)


async def enqueue_evolution(
    evolution_id: str,
    workflow_name: str,
    eval_suite_yaml: str,
    max_iterations: int,
    optimize_for: str,
    budget_limit: float | None = None,
    tenant_id: str | None = None,
    mark_failed_on_error: bool = True,
) -> None:
    """Enqueue a persisted evolution via Redis or the in-process local queue."""
    global _enqueue_redis_pool

    if uses_redis_queue():
        from arq import create_pool

        try:
            if _enqueue_redis_pool is None:
                async with _get_enqueue_lock():
                    if _enqueue_redis_pool is None:
                        _enqueue_redis_pool = await create_pool(
                            _parse_redis_url(settings.redis_url)
                        )
            await _enqueue_redis_pool.enqueue_job(
                "run_evolution_job",
                evolution_id,
                workflow_name,
                eval_suite_yaml,
                max_iterations,
                optimize_for,
                budget_limit=budget_limit,
                tenant_id=tenant_id,
                _job_id=f"evolution-{evolution_id}",
            )
        except Exception as enqueue_err:
            logger.error(
                "Failed to enqueue evolution %s to Redis: %s",
                evolution_id,
                enqueue_err,
            )
            if mark_failed_on_error:
                await _mark_evolution_failed(
                    evolution_id,
                    f"Redis enqueue failed: {enqueue_err}",
                )
            raise
    else:
        logger.info("Local mode: executing evolution %s in-process", evolution_id)

        async def run_with_timeout() -> None:
            try:
                await asyncio.wait_for(
                    run_evolution_job(
                        {},
                        evolution_id,
                        workflow_name,
                        eval_suite_yaml,
                        max_iterations,
                        optimize_for,
                        budget_limit=budget_limit,
                        tenant_id=tenant_id,
                    ),
                    timeout=settings.evolution_job_timeout,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Local evolution %s exceeded timeout of %d seconds",
                    evolution_id,
                    settings.evolution_job_timeout,
                )
                await _mark_evolution_failed(
                    evolution_id,
                    (
                        "Evolution job timed out after "
                        f"{settings.evolution_job_timeout} seconds"
                    ),
                )

        task = asyncio.create_task(
            run_with_timeout(),
            name=f"evolution-{evolution_id}",
        )
        _background_tasks.add(task)
        task.add_done_callback(_task_done_callback)


async def _mark_evolution_failed(
    evolution_id: str,
    error_message: str | None = None,
) -> None:
    """Mark an active evolution failed without overriding cancellation."""
    import uuid as _uuid

    from sandcastle.models.db import WorkflowEvolution, async_session

    try:
        async with async_session() as session:
            evolution = await session.get(WorkflowEvolution, _uuid.UUID(evolution_id))
            if evolution and evolution.status in ("queued", "running"):
                evolution.status = "failed"
                evolution.completed_at = datetime.now(timezone.utc)
                evolution.error = error_message
                await session.commit()
    except Exception as db_err:
        logger.error(
            "Failed to mark evolution %s as failed: %s",
            evolution_id,
            db_err,
        )


async def _mark_run_failed(run_id: str, error_message: str) -> None:
    """Mark a run as FAILED in the database.

    Used when enqueue fails so the run does not stay stuck in QUEUED.
    Silently handles DB errors to avoid masking the original failure.
    """
    import uuid as _uuid

    try:
        from sandcastle.models.db import Run, RunStatus, async_session

        async with async_session() as session:
            run = await session.get(Run, _uuid.UUID(run_id))
            if run and run.status == RunStatus.QUEUED:
                run.status = RunStatus.FAILED
                run.error = error_message[:4096]
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info("Marked run %s as FAILED after enqueue failure", run_id)
    except Exception as db_err:
        logger.error(
            "Failed to mark run %s as FAILED in DB: %s", run_id, db_err
        )
