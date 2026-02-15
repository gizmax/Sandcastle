"""Redis queue worker using arq for async workflow execution."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from sandcastle.config import settings

logger = logging.getLogger(__name__)


def _parse_redis_url(url: str) -> RedisSettings:
    """Parse a Redis URL into arq RedisSettings."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or 0),
        password=parsed.password,
    )


async def run_workflow_job(ctx: dict, workflow_yaml: str, input_data: dict, run_id: str) -> dict:
    """Arq job: execute a workflow asynchronously.

    Updates the database with progress and results.
    """
    from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
    from sandcastle.engine.executor import execute_workflow
    from sandcastle.engine.storage import LocalStorage
    from sandcastle.models.db import Run, RunStatus, async_session

    logger.info(f"Worker picked up run {run_id}")

    async with async_session() as session:
        # Update run status to RUNNING
        run = await session.get(Run, uuid.UUID(run_id))
        if run:
            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(timezone.utc)
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
        )

        # Update DB with result
        async with async_session() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            if run:
                run.status = RunStatus.COMPLETED if result.status == "completed" else RunStatus.FAILED
                run.output_data = result.outputs
                run.total_cost_usd = result.total_cost_usd
                run.completed_at = datetime.now(timezone.utc)
                run.error = result.error
                await session.commit()

        logger.info(f"Run {run_id} completed with status {result.status}")
        return {"run_id": run_id, "status": result.status}

    except Exception as e:
        logger.error(f"Run {run_id} failed: {e}")
        async with async_session() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            if run:
                run.status = RunStatus.FAILED
                run.completed_at = datetime.now(timezone.utc)
                run.error = str(e)
                await session.commit()
        return {"run_id": run_id, "status": "failed", "error": str(e)}


async def startup(ctx: dict) -> None:
    """Worker startup hook."""
    logger.info("Sandcastle worker starting up")


async def shutdown(ctx: dict) -> None:
    """Worker shutdown hook."""
    logger.info("Sandcastle worker shutting down")


class WorkerSettings:
    """Arq worker settings."""

    functions = [run_workflow_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _parse_redis_url(settings.redis_url)
    max_jobs = 10
    job_timeout = 600  # 10 minutes max per workflow


async def enqueue_workflow(
    workflow_yaml: str,
    input_data: dict,
    run_id: str,
) -> None:
    """Enqueue a workflow job into the Redis queue."""
    redis = await create_pool(_parse_redis_url(settings.redis_url))
    await redis.enqueue_job(
        "run_workflow_job",
        workflow_yaml,
        input_data,
        run_id,
    )
    await redis.close()
