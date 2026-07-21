"""Cron scheduler for recurring workflow executions."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from sandcastle.config import settings

logger = logging.getLogger(__name__)

# Global scheduler instance (guarded by _scheduler_lock for thread safety)
_scheduler: AsyncIOScheduler | None = None
_scheduler_lock = threading.Lock()

# ApprovalRequest has no retry counter. Keep timeout-skip resume attempts
# process-local so transient queue/workflow failures can be retried without a
# migration, while still bounding the time a run can remain paused.
_APPROVAL_RESUME_MAX_ATTEMPTS = 3
_approval_resume_attempts: dict[str, int] = {}
_AWAITING_APPROVAL_RECONCILE_AFTER = timedelta(minutes=5)


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the global scheduler instance.

    Thread-safe: uses a lock to prevent creating multiple instances
    in multi-threaded ASGI environments (e.g., Uvicorn with thread pools).
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    with _scheduler_lock:
        # Double-checked locking: re-check after acquiring lock
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
        # Register nightly self-healing pass (no-op unless healer_enabled)
        scheduler.add_job(
            _run_healer_nightly,
            trigger=CronTrigger(hour=3, minute=30, timezone=timezone.utc),
            id="healer_nightly_pass",
            replace_existing=True,
            misfire_grace_time=3600,
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

        restored_count = 0
        for schedule in schedules:
            try:
                add_schedule(
                    schedule_id=str(schedule.id),
                    cron_expression=schedule.cron_expression,
                    workflow_name=schedule.workflow_name,
                    input_data=schedule.input_data,
                )
                restored_count += 1
            except ValueError as e:
                # Invalid cron expression - disable the schedule to prevent
                # repeated failures on every restart
                logger.warning(
                    f"Could not restore schedule {schedule.id}: {e}. "
                    "Disabling invalid schedule."
                )
                try:
                    async with async_session() as dsession:
                        bad = await dsession.get(Schedule, schedule.id)
                        if bad:
                            bad.enabled = False
                            await dsession.commit()
                except Exception as de:
                    logger.error(
                        f"Failed to disable invalid schedule {schedule.id}: {de}"
                    )
            except Exception as e:
                logger.warning(f"Could not restore schedule {schedule.id}: {e}")

        logger.info(f"Restored {restored_count} schedule(s) from database")
    except Exception as e:
        logger.warning(f"Could not restore schedules from database: {e}")


def _load_workflow_yaml(workflow_name: str) -> str:
    """Load workflow YAML content from the workflows directory by name."""
    workflows_dir = Path(settings.workflows_dir).resolve()
    for candidate in [
        workflows_dir / f"{workflow_name}.yaml",
        workflows_dir / workflow_name,
    ]:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(workflows_dir):
            raise ValueError(
                f"Path traversal detected in workflow_name: {workflow_name!r}"
            )
        if resolved.exists() and resolved.is_file():
            return resolved.read_text()
    raise FileNotFoundError(f"Workflow '{workflow_name}' not found in {workflows_dir}")


async def _run_scheduled_workflow(
    schedule_id: str,
    workflow_name: str,
    input_data: dict,
) -> None:
    """Job function: enqueue a workflow run from a schedule trigger.

    Uses SELECT FOR UPDATE on the schedule row to prevent TOCTOU race
    conditions between checking the last run status and creating a new run.
    The entire check-and-create sequence happens inside a single transaction.
    """
    from sqlalchemy import select

    from sandcastle.models.db import Run, RunStatus, Schedule, async_session
    from sandcastle.queue.worker import enqueue_workflow

    run_id = str(uuid.uuid4())
    tenant_id = None
    max_cost_usd = None

    try:
        async with async_session() as session:
            # Lock the schedule row to prevent concurrent triggers from
            # both creating a run at the same time
            stmt = (
                select(Schedule)
                .where(Schedule.id == uuid.UUID(schedule_id))
                .with_for_update()
            )
            result = await session.execute(stmt)
            schedule = result.scalar_one_or_none()

            if not schedule:
                logger.warning(
                    f"Schedule '{schedule_id}' no longer exists in database, "
                    "skipping execution"
                )
                return
            if not schedule.enabled:
                logger.info(
                    f"Schedule '{schedule_id}' is disabled, skipping execution"
                )
                return

            # Guard against concurrent execution of the same scheduled workflow
            if schedule.last_run_id:
                last_run = await session.get(Run, schedule.last_run_id)
                if last_run and last_run.status in (
                    RunStatus.RUNNING,
                    RunStatus.QUEUED,
                    RunStatus.AWAITING_APPROVAL,
                ):
                    logger.warning(
                        "Skipping scheduled run for '%s' - previous run %s still active",
                        schedule.workflow_name,
                        schedule.last_run_id,
                    )
                    return

            tenant_id = schedule.tenant_id

            # Resolve tenant budget from API key if available
            if tenant_id:
                from sandcastle.models.db import ApiKey

                budget_stmt = select(ApiKey.max_cost_per_run_usd).where(
                    ApiKey.tenant_id == tenant_id,
                    ApiKey.is_active.is_(True),
                ).order_by(
                    ApiKey.max_cost_per_run_usd.asc().nulls_last()
                ).limit(1)
                max_cost_usd = await session.scalar(budget_stmt)

            # Create the run and update last_run_id atomically within the
            # same transaction that holds the row lock
            db_run = Run(
                id=uuid.UUID(run_id),
                workflow_name=workflow_name,
                status=RunStatus.QUEUED,
                input_data=input_data,
                tenant_id=tenant_id,
                max_cost_usd=max_cost_usd,
            )
            session.add(db_run)
            schedule.last_run_id = uuid.UUID(run_id)

            await session.commit()

    except Exception as e:
        logger.warning(f"Could not check schedule state for '{schedule_id}': {e}")
        return

    logger.info(f"Schedule '{schedule_id}' triggered: creating run {run_id}")

    try:
        workflow_yaml = _load_workflow_yaml(workflow_name)

        # Enqueue the job (budget is read from DB by worker)
        await enqueue_workflow(workflow_yaml, input_data, run_id)
        logger.info(f"Schedule '{schedule_id}' enqueued run {run_id}")

    except Exception as e:
        logger.error(
            f"Schedule '{schedule_id}' failed to create/enqueue run: {e}"
        )
        # Mark the run as FAILED so it doesn't stay stuck in QUEUED
        try:
            from sandcastle.models.db import Run, RunStatus, async_session

            async with async_session() as session:
                stuck_run = await session.get(Run, uuid.UUID(run_id))
                if stuck_run and stuck_run.status == RunStatus.QUEUED:
                    stuck_run.status = RunStatus.FAILED
                    stuck_run.error = f"Schedule enqueue failed: {e}"
                    stuck_run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
                    logger.info(f"Marked stuck run {run_id} as FAILED")
        except Exception as cleanup_err:
            logger.error(f"Failed to mark run {run_id} as FAILED: {cleanup_err}")


async def _run_healer_nightly() -> None:
    """Run the nightly self-healing pass when the healer is enabled."""
    if not settings.healer_enabled:
        return
    try:
        from sandcastle.engine.healer import run_healer_pass

        summary = await run_healer_pass()
        logger.info(f"Nightly healer pass: {summary}")
    except Exception as e:
        logger.error(f"Nightly healer pass failed: {e}", exc_info=True)


async def _retry_or_fail_timed_out_skip(
    approval_id: uuid.UUID,
    error: Exception,
) -> None:
    """Return a failed timeout-skip approval to PENDING, or fail after retries."""
    from sqlalchemy import select

    from sandcastle.models.db import ApprovalRequest, ApprovalStatus, Run, RunStatus, async_session

    approval_key = str(approval_id)
    attempt = _approval_resume_attempts.get(approval_key, 0) + 1
    error_detail = str(error)[:1000]

    async with async_session() as session:
        approval_stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id)
            .with_for_update()
        )
        approval = (await session.execute(approval_stmt)).scalar_one_or_none()
        if not approval or approval.status != ApprovalStatus.TIMED_OUT:
            return

        run = await session.get(Run, approval.run_id)
        if attempt < _APPROVAL_RESUME_MAX_ATTEMPTS:
            approval.status = ApprovalStatus.PENDING
            approval.resolved_at = None
            approval.reviewer_comment = (
                "Automatic timeout-skip resume retry "
                f"{attempt}/{_APPROVAL_RESUME_MAX_ATTEMPTS - 1}: {error_detail}"
            )
            # _resume_after_approval marks an enqueue failure as FAILED. Put it
            # back into its paused state while the scheduler retries.
            if run and run.status == RunStatus.FAILED:
                run.status = RunStatus.AWAITING_APPROVAL
                run.error = None
                run.completed_at = None
            await session.commit()
            _approval_resume_attempts[approval_key] = attempt
            logger.warning(
                "Timeout-skip resume for approval %s failed; retry %d/%d scheduled",
                approval_id,
                attempt,
                _APPROVAL_RESUME_MAX_ATTEMPTS - 1,
            )
            return

        _approval_resume_attempts.pop(approval_key, None)
        if run and run.status not in (
            RunStatus.COMPLETED,
            RunStatus.CANCELLED,
            RunStatus.BUDGET_EXCEEDED,
        ):
            run.status = RunStatus.FAILED
            run.completed_at = datetime.now(timezone.utc)
            run.error = (
                "Approval timeout skip could not resume after "
                f"{_APPROVAL_RESUME_MAX_ATTEMPTS} attempts: {error_detail}"
            )[:4096]
        approval.reviewer_comment = (
            "Automatic timeout-skip resume exhausted retries: "
            f"{error_detail}"
        )
        await session.commit()
        logger.error(
            "Timeout-skip resume for approval %s exhausted %d attempts",
            approval_id,
            _APPROVAL_RESUME_MAX_ATTEMPTS,
        )


async def _fail_awaiting_approval_run(run_id: uuid.UUID, error: str) -> None:
    """Fail a still-paused run after approval reconciliation cannot resume it."""
    from sandcastle.models.db import Run, RunStatus, async_session

    async with async_session() as session:
        run = await session.get(Run, run_id)
        if run and run.status == RunStatus.AWAITING_APPROVAL:
            run.status = RunStatus.FAILED
            run.completed_at = datetime.now(timezone.utc)
            run.error = error[:4096]
            await session.commit()


async def _reconcile_wedged_approval_runs(now: datetime) -> int:
    """Re-drive terminal approvals that left an old run paused, once.

    An approval can be durably resolved before its continuation is enqueued.
    This periodic reconciliation handles rows left behind by a process crash or
    a transient resume error that occurred before retry state was recorded.
    """
    from sqlalchemy import select

    from sandcastle.models.db import ApprovalRequest, ApprovalStatus, Run, RunStatus, async_session

    terminal_statuses = {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.SKIPPED,
        ApprovalStatus.TIMED_OUT,
    }
    cutoff = now - _AWAITING_APPROVAL_RECONCILE_AFTER

    async with async_session() as session:
        stale_run_ids = [
            row[0]
            for row in (
                await session.execute(
                    select(Run.id).where(
                        Run.status == RunStatus.AWAITING_APPROVAL,
                        Run.created_at <= cutoff,
                    )
                )
            ).all()
        ]

    reconciled = 0
    for run_id in stale_run_ids:
        async with async_session() as session:
            approval_result = await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.run_id == run_id)
                .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
            )
            approvals = approval_result.scalars().all()

        if not approvals or any(approval.status not in terminal_statuses for approval in approvals):
            continue

        approval = approvals[0]
        can_resume = approval.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.SKIPPED,
        } or (
            approval.status == ApprovalStatus.TIMED_OUT and approval.on_timeout == "skip"
        )
        if not can_resume:
            await _fail_awaiting_approval_run(
                run_id,
                f"Approval '{approval.step_id}' reached terminal state "
                f"'{approval.status.value}' without a continuation",
            )
            reconciled += 1
            continue

        try:
            from sandcastle.api.routes import _resume_after_approval

            output_data = approval.response_data if approval.status == ApprovalStatus.APPROVED else None
            await _resume_after_approval(approval, output_data=output_data)
            reconciled += 1
            logger.info("Reconciled paused run %s after approval %s", run_id, approval.id)
        except Exception as exc:
            logger.error("Could not reconcile paused run %s: %s", run_id, exc)
            await _fail_awaiting_approval_run(
                run_id,
                f"Approval reconciliation failed after terminal approval: {exc}",
            )
            reconciled += 1

    return reconciled


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
        # Fetch only IDs to avoid stale object references across sessions
        async with async_session() as session:
            stmt = select(ApprovalRequest.id).where(
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.timeout_at.isnot(None),
                ApprovalRequest.timeout_at <= now,
            )
            result = await session.execute(stmt)
            timed_out_ids = [row[0] for row in result.all()]

        processed = 0
        for approval_id in timed_out_ids:
            try:
                async with async_session() as session:
                    # Re-fetch with FOR UPDATE lock to prevent TOCTOU race
                    # with concurrent approval resolution requests
                    stmt = (
                        select(ApprovalRequest)
                        .where(ApprovalRequest.id == approval_id)
                        .with_for_update()
                    )
                    result = await session.execute(stmt)
                    ap = result.scalar_one_or_none()
                    if not ap or ap.status != ApprovalStatus.PENDING:
                        continue

                    logger.info(
                        f"Approval {ap.id} for step '{ap.step_id}' timed out "
                        f"(on_timeout={ap.on_timeout})"
                    )

                    ap.status = ApprovalStatus.TIMED_OUT
                    ap.resolved_at = now

                    run = await session.get(Run, ap.run_id)
                    if not run:
                        logger.warning(
                            f"Approval {ap.id} references non-existent run "
                            f"{ap.run_id}, marking as timed out"
                        )
                        await session.commit()
                        processed += 1
                        continue

                    run_status = run.status.value if hasattr(run.status, "value") else run.status
                    if run_status in ("completed", "failed", "cancelled", "budget_exceeded"):
                        logger.info(
                            f"Approval {ap.id} for already-finished run "
                            f"(status={run_status}), marking as timed out only"
                        )
                        await session.commit()
                        processed += 1
                        continue

                    on_timeout = ap.on_timeout
                    if on_timeout == "skip":
                        await session.commit()
                        try:
                            from sandcastle.api.routes import _resume_after_approval

                            await _resume_after_approval(ap, output_data=None)
                            _approval_resume_attempts.pop(str(ap.id), None)
                        except Exception as e:
                            logger.error(f"Failed to resume after timeout skip: {e}")
                            await _retry_or_fail_timed_out_skip(ap.id, e)
                    else:
                        run.status = RunStatus.FAILED
                        run.completed_at = now
                        run.error = f"Approval timed out at step '{ap.step_id}'"
                        await session.commit()

                    processed += 1

            except Exception as e:
                logger.error(f"Error processing approval timeout {approval_id}: {e}")

        reconciled = await _reconcile_wedged_approval_runs(now)
        if processed or reconciled:
            logger.info(
                "Processed %d timed-out approval(s), reconciled %d paused run(s)",
                processed,
                reconciled,
            )

    except Exception as e:
        logger.error(f"Error checking approval timeouts: {e}")


def add_schedule(
    schedule_id: str,
    cron_expression: str,
    workflow_name: str,
    input_data: dict | None = None,
) -> None:
    """Register a cron job for a workflow schedule."""
    if not cron_expression or not cron_expression.strip():
        raise ValueError("cron_expression must not be empty")
    if not workflow_name or not workflow_name.strip():
        raise ValueError("workflow_name must not be empty")

    scheduler = get_scheduler()

    try:
        trigger = CronTrigger.from_crontab(cron_expression, timezone=timezone.utc)
    except ValueError as e:
        raise ValueError(f"Invalid cron expression '{cron_expression}': {e}")

    # Validate that the workflow YAML file exists (skip if dir missing)
    workflows_dir = Path(settings.workflows_dir).resolve()
    if workflows_dir.is_dir():
        candidates = [
            workflows_dir / f"{workflow_name}.yaml",
            workflows_dir / workflow_name,
        ]
        if not any(c.resolve().is_file() for c in candidates if c.resolve().is_relative_to(workflows_dir)):
            raise ValueError(f"Workflow '{workflow_name}' not found")

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
        # Pending jobs (added while the scheduler is stopped/paused) have no
        # computed run time; APScheduler raises AttributeError instead of
        # returning None for those.
        try:
            next_run_time = job.next_run_time
        except AttributeError:
            next_run_time = None
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": str(next_run_time) if next_run_time else None,
            "trigger": str(job.trigger),
        })
    return jobs
