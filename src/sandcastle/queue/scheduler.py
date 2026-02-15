"""Cron scheduler for recurring workflow executions."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
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
    """Start the cron scheduler and register periodic jobs."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        # Register approval timeout checker every 60 seconds
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.add_job(
            _check_approval_timeouts,
            trigger=IntervalTrigger(seconds=60),
            id="approval_timeout_checker",
            replace_existing=True,
            misfire_grace_time=30,
        )
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
        from sqlalchemy import select

        from sandcastle.models.db import Schedule, async_session

        async with async_session() as session:
            stmt = select(Schedule).where(Schedule.enabled.is_(True))
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
        workflow_yaml = _load_workflow_yaml(workflow_name)

        # Load tenant context and budget from the schedule record
        tenant_id = None
        max_cost_usd = None
        async with async_session() as session:
            schedule = await session.get(
                Schedule, uuid.UUID(schedule_id)
            )
            if schedule:
                tenant_id = schedule.tenant_id

            # Resolve tenant budget from API key if available
            if tenant_id:
                from sqlalchemy import select

                from sandcastle.models.db import ApiKey

                stmt = select(ApiKey.max_cost_per_run_usd).where(
                    ApiKey.tenant_id == tenant_id,
                    ApiKey.is_active.is_(True),
                ).limit(1)
                max_cost_usd = await session.scalar(stmt)

        # Create the run record with tenant context
        async with async_session() as session:
            db_run = Run(
                id=uuid.UUID(run_id),
                workflow_name=workflow_name,
                status=RunStatus.QUEUED,
                input_data=input_data,
                tenant_id=tenant_id,
                max_cost_usd=max_cost_usd,
            )
            session.add(db_run)

            # Update schedule's last_run_id
            schedule = await session.get(
                Schedule, uuid.UUID(schedule_id)
            )
            if schedule:
                schedule.last_run_id = uuid.UUID(run_id)

            await session.commit()

        # Enqueue the job (budget is read from DB by worker)
        await enqueue_workflow(workflow_yaml, input_data, run_id)
        logger.info(f"Schedule '{schedule_id}' enqueued run {run_id}")

    except Exception as e:
        logger.error(
            f"Schedule '{schedule_id}' failed to create run: {e}"
        )


async def _check_approval_timeouts() -> None:
    """Check for timed-out approval requests and apply on_timeout action."""
    from datetime import timezone

    from sqlalchemy import select

    from sandcastle.models.db import (
        ApprovalRequest,
        ApprovalStatus,
        Run,
        RunStatus,
        async_session,
    )

    now = datetime.now(timezone.utc)

    try:
        async with async_session() as session:
            stmt = select(ApprovalRequest).where(
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.timeout_at.isnot(None),
                ApprovalRequest.timeout_at <= now,
            )
            result = await session.execute(stmt)
            timed_out = result.scalars().all()

        for approval in timed_out:
            logger.info(
                f"Approval {approval.id} for step '{approval.step_id}' timed out "
                f"(on_timeout={approval.on_timeout})"
            )

            async with async_session() as session:
                ap = await session.get(ApprovalRequest, approval.id)
                if not ap or ap.status != ApprovalStatus.PENDING:
                    continue

                ap.status = ApprovalStatus.TIMED_OUT
                ap.resolved_at = now

                if approval.on_timeout == "skip":
                    # Skip the step and continue
                    await session.commit()
                    try:
                        from sandcastle.api.routes import _resume_after_approval

                        await _resume_after_approval(approval, output_data=None)
                    except Exception as e:
                        logger.error(f"Failed to resume after timeout skip: {e}")
                else:
                    # Abort - fail the run
                    run = await session.get(Run, approval.run_id)
                    if run:
                        run.status = RunStatus.FAILED
                        run.completed_at = now
                        run.error = f"Approval timed out at step '{approval.step_id}'"
                    await session.commit()

        if timed_out:
            logger.info(f"Processed {len(timed_out)} timed-out approval(s)")

    except Exception as e:
        logger.error(f"Error checking approval timeouts: {e}")


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
