"""Queue worker - arq (Redis) or in-process (asyncio) for local mode."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sandcastle.config import settings

logger = logging.getLogger(__name__)


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


async def run_workflow_job(
    ctx: dict,
    workflow_yaml: str,
    input_data: dict,
    run_id: str,
    max_cost_usd: float | None = None,
    initial_context: dict | None = None,
    skip_steps: list[str] | None = None,
    step_overrides: dict | None = None,
) -> dict:
    """Arq job: execute a workflow asynchronously.

    Updates the database with progress and results. Dispatches webhooks.
    Supports budget limits, replay (initial_context + skip_steps), and fork (step_overrides).
    """
    from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
    from sandcastle.engine.executor import execute_workflow
    from sandcastle.engine.storage import LocalStorage
    from sandcastle.models.db import Run, RunStatus, async_session
    from sandcastle.webhooks.dispatcher import dispatch_webhook

    logger.info(f"Worker picked up run {run_id}")

    import uuid as _uuid
    run_uuid = _uuid.UUID(run_id) if isinstance(run_id, str) else run_id

    callback_url = None

    async with async_session() as session:
        run = await session.get(Run, run_uuid)
        if run:
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
        storage = LocalStorage()

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
        )

        # Map result status to RunStatus
        status_map = {
            "completed": RunStatus.COMPLETED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
            "budget_exceeded": RunStatus.BUDGET_EXCEEDED,
            "awaiting_approval": RunStatus.AWAITING_APPROVAL,
        }

        # Update DB with result
        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            if run:
                run.status = status_map.get(result.status, RunStatus.FAILED)
                run.output_data = result.outputs
                run.total_cost_usd = result.total_cost_usd
                # Don't set completed_at for paused workflows
                if result.status != "awaiting_approval":
                    run.completed_at = datetime.now(timezone.utc)
                run.error = result.error
                await session.commit()

        # Dispatch webhook - on_complete or on_failure depending on status
        webhook_urls = []
        if callback_url:
            webhook_urls.append(callback_url)

        if result.status == "completed":
            if not callback_url and workflow.on_complete and workflow.on_complete.webhook:
                webhook_urls.append(workflow.on_complete.webhook)
        else:
            if workflow.on_failure and workflow.on_failure.webhook:
                webhook_urls.append(workflow.on_failure.webhook)

        for webhook_url in webhook_urls:
            duration = 0.0
            if result.started_at and result.completed_at:
                duration = (result.completed_at - result.started_at).total_seconds()

            await dispatch_webhook(
                url=webhook_url,
                event="workflow.completed" if result.status == "completed" else "workflow.failed",
                run_id=run_id,
                workflow=workflow.name,
                status=result.status,
                outputs=result.outputs,
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
                run.error = str(e)
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

        for url in failure_urls:
            await dispatch_webhook(
                url=url,
                event="workflow.failed",
                run_id=run_id,
                workflow="unknown",
                status="failed",
                error=str(e),
            )

        return {"run_id": run_id, "status": "failed", "error": str(e)}


async def startup(ctx: dict) -> None:
    """Worker startup hook."""
    logger.info("Sandcastle worker starting up")


async def shutdown(ctx: dict) -> None:
    """Worker shutdown hook."""
    logger.info("Sandcastle worker shutting down")


class WorkerSettings:
    """Arq worker settings (only used with Redis)."""

    functions = [run_workflow_job]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 600

    @classmethod
    def get_redis_settings(cls):
        if settings.redis_url:
            return _parse_redis_url(settings.redis_url)
        return None

    redis_settings = property(get_redis_settings)


# Lazy init: only set redis_settings when Redis is configured
if settings.redis_url:
    WorkerSettings.redis_settings = _parse_redis_url(settings.redis_url)


async def enqueue_workflow(
    workflow_yaml: str,
    input_data: dict,
    run_id: str,
    max_cost_usd: float | None = None,
    initial_context: dict | None = None,
    skip_steps: list[str] | None = None,
    step_overrides: dict | None = None,
) -> None:
    """Enqueue a workflow job - via Redis (arq) or in-process (asyncio.create_task)."""
    if settings.redis_url:
        # Production mode: enqueue via arq/Redis
        from arq import create_pool

        redis = await create_pool(_parse_redis_url(settings.redis_url))
        await redis.enqueue_job(
            "run_workflow_job",
            workflow_yaml,
            input_data,
            run_id,
            max_cost_usd=max_cost_usd,
            initial_context=initial_context,
            skip_steps=skip_steps,
            step_overrides=step_overrides,
        )
        await redis.close()
    else:
        # Local mode: run directly in-process
        logger.info(f"Local mode: executing run {run_id} in-process")
        asyncio.create_task(
            run_workflow_job(
                {},  # empty ctx for in-process
                workflow_yaml,
                input_data,
                run_id,
                max_cost_usd=max_cost_usd,
                initial_context=initial_context,
                skip_steps=skip_steps,
                step_overrides=step_overrides,
            )
        )
