"""Cron scheduler for recurring workflow executions."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from sandcastle.config import settings

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


async def start_scheduler() -> None:
    """Start the cron scheduler."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started")


async def stop_scheduler() -> None:
    """Stop the cron scheduler."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


async def restore_schedules() -> None:
    """Restore enabled schedules from the database on startup."""
    try:
        from sandcastle.models.db import Schedule, async_session
        from sqlalchemy import select

        async with async_session() as session:
            stmt = select(Schedule).where(Schedule.enabled == True)
            result = await session.execute(stmt)
            schedules = result.scalars().all()

        for schedule in schedules:
            try:
                add_schedule(
                    schedule_id=str(schedule.id),
                    cron_expression=schedule.cron_expression,
                    workflow_name=schedule.workflow_name,
                    input_data=schedule.input_data,
                )
            except Exception as e:
                logger.warning(f"Could not restore schedule {schedule.id}: {e}")

        logger.info(f"Restored {len(schedules)} schedule(s) from database")
    except Exception as e:
        logger.warning(f"Could not restore schedules from database: {e}")


def _load_workflow_yaml(workflow_name: str) -> str:
    """Load workflow YAML content from the workflows directory by name."""
    workflows_dir = Path(settings.workflows_dir)
    for candidate in [
        workflows_dir / f"{workflow_name}.yaml",
        workflows_dir / workflow_name,
    ]:
        if candidate.exists() and candidate.is_file():
            return candidate.read_text()
    raise FileNotFoundError(f"Workflow '{workflow_name}' not found in {workflows_dir}")


async def _run_scheduled_workflow(
    schedule_id: str,
    workflow_name: str,
    input_data: dict,
) -> None:
    """Job function: enqueue a workflow run from a schedule trigger."""
    from sandcastle.models.db import Run, RunStatus, Schedule, async_session
    from sandcastle.queue.worker import enqueue_workflow

    run_id = str(uuid.uuid4())
    logger.info(f"Schedule '{schedule_id}' triggered: creating run {run_id}")

    try:
        # Load fresh workflow YAML from disk
        workflow_yaml = _load_workflow_yaml(workflow_name)

        # Create the run record
        async with async_session() as session:
            db_run = Run(
                id=uuid.UUID(run_id),
                workflow_name=workflow_name,
                status=RunStatus.QUEUED,
                input_data=input_data,
            )
            session.add(db_run)

            # Update schedule's last_run_id
            schedule = await session.get(Schedule, uuid.UUID(schedule_id))
            if schedule:
                schedule.last_run_id = uuid.UUID(run_id)

            await session.commit()

        # Enqueue the job
        await enqueue_workflow(workflow_yaml, input_data, run_id)
        logger.info(f"Schedule '{schedule_id}' enqueued run {run_id}")

    except Exception as e:
        logger.error(f"Schedule '{schedule_id}' failed to create run: {e}")


def add_schedule(
    schedule_id: str,
    cron_expression: str,
    workflow_name: str,
    input_data: dict | None = None,
) -> None:
    """Register a cron job for a workflow schedule."""
    scheduler = get_scheduler()

    trigger = CronTrigger.from_crontab(cron_expression)

    scheduler.add_job(
        _run_scheduled_workflow,
        trigger=trigger,
        id=schedule_id,
        args=[schedule_id, workflow_name, input_data or {}],
        replace_existing=True,
        misfire_grace_time=60,
    )

    logger.info(f"Schedule '{schedule_id}' registered: {cron_expression} for '{workflow_name}'")


def remove_schedule(schedule_id: str) -> bool:
    """Remove a scheduled job."""
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(schedule_id)
        logger.info(f"Schedule '{schedule_id}' removed")
        return True
    except Exception:
        logger.warning(f"Schedule '{schedule_id}' not found for removal")
        return False


def list_schedules() -> list[dict]:
    """List all active scheduled jobs."""
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return jobs
