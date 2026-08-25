"""Queue worker - arq (Redis) or in-process (asyncio) for local mode."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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
            # Replay/fork lineage for the step effect ledger. Read off the run
            # row rather than added to the job signature, so jobs enqueued by
            # an older API version keep deserializing.
            effect_scope_id=str(run.effect_scope_id) if run.effect_scope_id else None,
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
        # Stamped when this worker picks the job up, so the reaper can tell a
        # job that is actually running from one still sitting in the queue.
        evolution.started_at = datetime.now(timezone.utc)
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


def _as_int(value: Any, default: int) -> int:
    """Coerce a value to int, falling back when it is not a number.

    Same reason ``engine/effects.py`` has ``_numeric_setting``: tests routinely
    hand this code MagicMock rows and MagicMock settings, and a recovery sweep
    that raised on one would fail loudly in suites that are not testing it.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class _StuckRun:
    """The fields of a stranded run, read once and carried out of the session.

    Each run is then acted on in its own short transaction, so the sweep never
    holds a session open across an enqueue.
    """

    id: Any
    workflow_name: str
    workflow_version: int | None
    input_data: dict | None
    max_cost_usd: float | None
    admin_trusted: bool
    effect_scope_id: Any
    recovery_attempts: int


async def _ledger_can_carry_a_resume() -> tuple[bool, str]:
    """Whether a crashed run may be replayed, and if not, why not.

    Crash-resume is replay: the run re-executes from its first step and the
    step effect ledger is the only thing stopping the prefix that already
    landed from landing again. Without a reachable ledger a "recovery" would
    re-POST every completed effect - the exact bug 0.45 shipped the ledger to
    fix - so the honest answer there is the pre-0.46 one: FAILED.

    The probe is a real read of ``run_step_effects``. A missing table (a worker
    started against a pre-022 schema) and an unreachable database both surface
    here, once per sweep, before any run has been requeued.
    """
    if not settings.crash_resume_enabled:
        return False, "crash resume is disabled (CRASH_RESUME_ENABLED=0)"
    if not settings.effect_ledger_enabled:
        return False, "the step effect ledger is disabled (EFFECT_LEDGER_ENABLED=0)"
    try:
        from sandcastle.engine.effects import EffectLedger

        # A key that cannot exist: this asks whether the table is readable, not
        # whether it holds anything.
        await EffectLedger().lookup("crash-resume-probe")
    except Exception as exc:  # noqa: BLE001 - any failure means "do not replay"
        return False, f"the step effect ledger is unreachable ({exc})"
    return True, ""


async def _last_step_in_flight(run_id: Any) -> str:
    """Best-effort description of where a run was when its worker died.

    Steps are written to ``run_steps`` as RUNNING before they execute, so rows
    still marked RUNNING name what was in flight - which for a poison run is
    the step that keeps killing the worker. Returned as a phrase to splice into
    an error message, or "" when nothing is knowable.
    """
    try:
        from sqlalchemy import select

        from sandcastle.models.db import RunStep, StepStatus, async_session

        async with async_session() as session:
            rows = await session.scalars(
                select(RunStep.step_id)
                .where(
                    RunStep.run_id == run_id,
                    RunStep.status == StepStatus.RUNNING,
                )
                .limit(4)
            )
            names = sorted({name for name in rows if name})
        if not names:
            return ""
        if len(names) == 1:
            return f" while executing step '{names[0]}'"
        return " while executing steps " + ", ".join(f"'{n}'" for n in names)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break recovery
        logger.debug(
            "Could not determine the in-flight step for run %s: %s", run_id, exc
        )
        return ""


async def _fail_stuck_run(run_id: Any, error: str) -> bool:
    """Mark one stranded run FAILED, but only while it is still RUNNING."""
    from sqlalchemy import update

    from sandcastle.models.db import Run, RunStatus, async_session

    try:
        async with async_session() as session:
            result = await session.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == RunStatus.RUNNING)
                .values(
                    status=RunStatus.FAILED,
                    completed_at=datetime.now(timezone.utc),
                    error=error[:4096],
                )
            )
            await session.commit()
            return bool(result.rowcount)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not fail stuck run %s: %s", run_id, exc)
        return False


async def _requeue_stuck_run(run: _StuckRun, attempt: int) -> bool:
    """Put one stranded run back on the queue for a full replay.

    The transition RUNNING -> QUEUED is a compare-and-swap on both the status
    and the attempt counter, so two workers sweeping the same run in the same
    second cannot both requeue it and cannot both claim attempt *n*.

    Nothing is passed for ``skip_steps`` or ``initial_context``: this is a
    replay from the top, and the ledger rather than the checkpoint is what
    skips the completed prefix. ``_recover_stuck_runs`` explains why.
    """
    from sqlalchemy import update

    # Imported here, not at module import time: routes imports the worker for
    # enqueue_workflow, so a top-level import would be circular - and the
    # version-aware loader is the same one replay and fork use, which is the
    # point of borrowing it rather than writing a third copy.
    from sandcastle.api.routes import _load_versioned_workflow_yaml
    from sandcastle.models.db import Run, RunStatus, async_session

    run_id = str(run.id)
    # The lineage the completed effects were claimed in. A run that was itself
    # a replay already carries its parent's scope; a fresh one is its own.
    # Pinning it explicitly means the requeued job cannot drift into a new
    # scope - which would re-fire everything.
    scope_id = run.effect_scope_id or run.id

    try:
        workflow_yaml = await _load_versioned_workflow_yaml(
            run.workflow_name, run.workflow_version
        )
    except Exception as exc:  # noqa: BLE001 - a missing workflow is not resumable
        await _fail_stuck_run(
            run.id,
            "Worker crashed or timed out and the run could not be resumed: "
            f"workflow '{run.workflow_name}' could not be loaded ({exc})",
        )
        return False

    async with async_session() as session:
        result = await session.execute(
            update(Run)
            .where(
                Run.id == run.id,
                Run.status == RunStatus.RUNNING,
                Run.recovery_attempts == attempt - 1,
            )
            .values(
                status=RunStatus.QUEUED,
                recovery_attempts=attempt,
                effect_scope_id=scope_id,
                started_at=None,
                completed_at=None,
                error=None,
            )
        )
        await session.commit()
        if not result.rowcount:
            # Another worker got there first, or the run settled on its own
            # between the SELECT and here. Either way it is no longer ours.
            return False

    try:
        await enqueue_workflow(
            workflow_yaml,
            run.input_data or {},
            run_id,
            max_cost_usd=run.max_cost_usd,
            admin_trusted=run.admin_trusted,
            # arq keeps a result under the original job id and silently drops
            # an enqueue that reuses a spent one. One id per attempt.
            job_id=f"{run_id}:recovery:{attempt}",
        )
    except Exception as exc:  # noqa: BLE001 - enqueue_workflow already failed the run
        logger.error("Could not requeue crashed run %s: %s", run_id, exc)
        return False

    logger.warning(
        "Requeued crashed run %s for replay (attempt %d/%d, effect scope %s)",
        run_id,
        attempt,
        settings.max_recovery_attempts,
        scope_id,
    )
    return True


async def _recover_stuck_runs() -> None:
    """Resume runs stranded in RUNNING by a dead worker.

    Finds runs that have been RUNNING for longer than twice the job timeout -
    arq kills a job at the timeout, so anything past twice it has no worker -
    and puts them back on the queue instead of burying them. QUEUED jobs can
    legitimately remain in Redis during a backlog, so they are left for the
    queue to deliver. Called during worker startup.

    **Replay, not rewind.** The requeued run executes from its first step in
    the same effect scope. Every step whose effect already committed comes back
    out of the ledger at ``cost_usd=0.0`` without touching the network; a step
    whose claim is still ``in_flight`` past its lease is genuinely unknown, so
    it fails per its ``on_uncertain``. There is no new scheduler state machine:
    the ledger *is* the checkpoint.

    **Why no ``skip_steps``.** The checkpoint path that replay and fork use
    restores ``step_outputs`` and skips what it sees, and layering it under the
    ledger as belt and braces looks free. It is not. ``RunContext.snapshot``
    does not carry ``step_results`` and ``execute_workflow`` does not restore
    it, so downstream references to a skipped step's ``{steps.X.status}``,
    ``{steps.X.error}`` or ``{steps.X.cost}`` resolve to nothing; a skipped step
    also writes no ``run_steps`` row, so it disappears from the Black Box
    timeline. A memoized step has neither problem - it returns a real
    ``StepResult`` through the ordinary completion path. And the checkpoint
    covers strictly less than the ledger: a step cancelled mid-flight when a
    sibling paused the run never reaches a checkpoint but does leave a ledger
    claim. Skipping would buy nothing and cost two bugs.

    The price of replaying from the top is wall-clock, not money: pure steps
    (``transform``, ``code``, ``condition``) are ``replay: live`` by design and
    do run again.

    **Bounds.** ``settings.max_recovery_attempts`` caps the requeues per run,
    counted in a column so a run that kills the worker counting it still runs
    out of attempts. Past the cap the run is FAILED with the attempt count and,
    where ``run_steps`` knows it, the step it died on.

    **Refusals.** With ``crash_resume_enabled`` off, or the ledger disabled or
    unreachable, the pre-0.46 behaviour stands: FAILED, with the reason in the
    error. Replaying without a ledger would re-fire every completed effect.
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
            stuck = [
                _StuckRun(
                    id=run.id,
                    workflow_name=run.workflow_name,
                    workflow_version=run.workflow_version,
                    input_data=run.input_data,
                    max_cost_usd=run.max_cost_usd,
                    admin_trusted=bool(run.admin_trusted),
                    effect_scope_id=run.effect_scope_id,
                    recovery_attempts=_as_int(run.recovery_attempts, 0),
                )
                for run in result.scalars().all()
            ]
    except Exception as e:
        logger.error("Failed to recover stuck runs on startup: %s", e)
        return

    if not stuck:
        return

    resumable, refusal = await _ledger_can_carry_a_resume()
    max_attempts = max(_as_int(settings.max_recovery_attempts, 2), 0)

    requeued = 0
    failed = 0
    for run in stuck:
        if not resumable:
            if await _fail_stuck_run(
                run.id,
                f"Worker crashed or timed out - not resumed because {refusal}",
            ):
                failed += 1
            continue

        attempt = run.recovery_attempts + 1
        if attempt > max_attempts:
            where = await _last_step_in_flight(run.id)
            if await _fail_stuck_run(
                run.id,
                f"Worker crashed or timed out{where}. Giving up after "
                f"{run.recovery_attempts} recovery attempt(s), the configured "
                f"maximum (max_recovery_attempts={max_attempts}).",
            ):
                failed += 1
                logger.error(
                    "Run %s exhausted its %d recovery attempt(s)%s",
                    run.id,
                    max_attempts,
                    where,
                )
            continue

        if await _requeue_stuck_run(run, attempt):
            requeued += 1

    if requeued or failed:
        logger.warning(
            "Stuck run sweep (threshold=%ds): %d requeued, %d failed",
            2 * timeout_seconds,
            requeued,
            failed,
        )


async def _recover_stuck_evolutions() -> None:
    """Fail evolution jobs left running beyond twice their configured timeout."""
    from datetime import timedelta

    from sqlalchemy import update

    from sandcastle.models.db import WorkflowEvolution, async_session

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=2 * settings.evolution_job_timeout
    )
    # A row can also be stranded before it ever runs: the API commits it as
    # queued and only then enqueues, so a Redis failure between the two left it
    # queued forever - blocking every later start for that workflow with a 409.
    # The route compensates now, but a process killed between the two steps
    # cannot, so sweep long-queued rows here as well. created_at is the right
    # clock for this one: nothing has started, and queueing is what is stuck.
    queued_cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=2 * settings.evolution_job_timeout
    )
    try:
        async with async_session() as session:
            result = await session.execute(
                update(WorkflowEvolution)
                .where(
                    WorkflowEvolution.status == "running",
                    # started_at, not created_at: created_at is when the job was
                    # queued, so a queued or freshly-started evolution used to be
                    # failed on every worker startup - including one running on
                    # another worker. Rows with no start time are skipped rather
                    # than reaped on a guess.
                    WorkflowEvolution.started_at.isnot(None),
                    WorkflowEvolution.started_at <= cutoff,
                )
                .values(
                    status="failed",
                    completed_at=datetime.now(timezone.utc),
                    error="Worker crashed or evolution job timed out",
                )
            )
            queued_result = await session.execute(
                update(WorkflowEvolution)
                .where(
                    WorkflowEvolution.status == "queued",
                    WorkflowEvolution.created_at <= queued_cutoff,
                )
                .values(
                    status="failed",
                    completed_at=datetime.now(timezone.utc),
                    error="Never picked up by a worker after being queued",
                )
            )
            total = (result.rowcount or 0) + (queued_result.rowcount or 0)
            if total:
                await session.commit()
                logger.warning(
                    "Recovered %d stuck evolution job(s) (%d running, %d queued)",
                    total,
                    result.rowcount or 0,
                    queued_result.rowcount or 0,
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
    # The step effect ledger only grows otherwise: nothing sweeps settled rows
    # once they are past their TTL.
    try:
        from sandcastle.engine.effects import prune_expired_effects

        await prune_expired_effects()
    except Exception as e:  # noqa: BLE001 - startup must not die on a prune
        logger.warning("Could not prune the step effect ledger: %s", e)


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
    job_id: str | None = None,
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

    ``job_id`` overrides the arq job id, which defaults to the run id so a
    duplicate submission of the same run is deduplicated by Redis. Crash
    recovery has to override it: the original job id is spent for as long as
    arq keeps its result, and re-enqueueing under a spent id is silently
    dropped. See ``_recover_stuck_runs``.
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
                _job_id=job_id or run_id,
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
