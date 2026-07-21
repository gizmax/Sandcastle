"""Regression coverage for wave D3 audit, scheduler, and worker robustness."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


@pytest.mark.asyncio
async def test_audit_concurrent_appends_are_a_single_chain_and_tampering_is_detected():
    """Concurrent sessions must serialize one chain, which remains tamper-evident."""
    from sandcastle.engine.audit import append_audit_event, verify_audit_chain
    from sandcastle.models.db import AuditEvent, Run, RunStatus, async_session

    run_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            Run(
                id=run_id,
                workflow_name="audit-concurrency",
                status=RunStatus.QUEUED,
                input_data={},
            )
        )
        await session.commit()

    async def append_one(index: int) -> None:
        async with async_session() as session:
            event = await append_audit_event(
                session,
                event_type="step.completed",
                run_id=str(run_id),
                actor_id="system",
                payload={"index": index},
            )
            assert event is not None
            await session.commit()

    await asyncio.gather(*(append_one(index) for index in range(8)))

    async with async_session() as session:
        valid, length, broken = await verify_audit_chain(session, str(run_id))
    assert (valid, length, broken) == (True, 8, None)

    async with async_session() as session:
        events = (
            (
                await session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.run_id == run_id)
                    .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
                )
            )
            .scalars()
            .all()
        )
        events[len(events) // 2].payload = {"tampered": True}
        await session.commit()

    async with async_session() as session:
        valid, length, broken = await verify_audit_chain(session, str(run_id))
    assert valid is False
    assert length == 8
    assert broken is not None


@pytest.mark.asyncio
async def test_audit_flush_failure_does_not_poison_callers_transaction():
    """The failed best-effort audit insert must not roll back real caller work."""
    from sandcastle.engine.audit import append_audit_event
    from sandcastle.models.db import Run, RunStatus, async_session

    run_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            Run(
                id=run_id,
                workflow_name="real-operation",
                status=RunStatus.QUEUED,
                input_data={},
            )
        )

        async def fail_audit_flush(*_args, **_kwargs):
            raise IntegrityError("INSERT audit_events", {}, RuntimeError("forced audit failure"))

        with patch.object(session, "flush", new=fail_audit_flush):
            event = await append_audit_event(
                session,
                event_type="run.started",
                run_id=str(run_id),
                actor_id="system",
                payload={},
            )

        assert event is None
        await session.commit()

    async with async_session() as session:
        persisted_run = await session.get(Run, run_id)
    assert persisted_run is not None
    assert persisted_run.workflow_name == "real-operation"


async def _create_timed_out_skip_approval(*, created_at: datetime | None = None):
    """Create an AWAITING_APPROVAL run with one approval gate for scheduler tests."""
    from sandcastle.models.db import ApprovalRequest, ApprovalStatus, Run, RunStatus, async_session

    run_id = uuid.uuid4()
    approval_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        session.add(
            Run(
                id=run_id,
                workflow_name="approval-retry",
                status=RunStatus.AWAITING_APPROVAL,
                input_data={},
                created_at=created_at or now,
            )
        )
        session.add(
            ApprovalRequest(
                id=approval_id,
                run_id=run_id,
                step_id="human-gate",
                status=ApprovalStatus.PENDING,
                timeout_at=now - timedelta(seconds=1),
                on_timeout="skip",
            )
        )
        await session.commit()
    return run_id, approval_id


@pytest.mark.asyncio
async def test_approval_timeout_skip_retries_then_fails_at_the_bound():
    """A failed timeout skip becomes pending again, then cannot wedge forever."""
    import sandcastle.queue.scheduler as scheduler
    from sandcastle.models.db import ApprovalRequest, ApprovalStatus, Run, RunStatus, async_session

    run_id, approval_id = await _create_timed_out_skip_approval()
    scheduler._approval_resume_attempts.clear()

    with (
        patch.object(scheduler, "_APPROVAL_RESUME_MAX_ATTEMPTS", 2),
        patch(
            "sandcastle.api.routes._resume_after_approval",
            new_callable=AsyncMock,
            side_effect=RuntimeError("temporary queue outage"),
        ) as resume,
    ):
        await scheduler._check_approval_timeouts()
        async with async_session() as session:
            approval = await session.get(ApprovalRequest, approval_id)
            run = await session.get(Run, run_id)
        assert approval.status == ApprovalStatus.PENDING
        assert run.status == RunStatus.AWAITING_APPROVAL

        await scheduler._check_approval_timeouts()
        async with async_session() as session:
            approval = await session.get(ApprovalRequest, approval_id)
            run = await session.get(Run, run_id)

    # Other tests may leave timed-out approvals behind in the shared test DB;
    # count only the attempts made for this test's approval.
    own_attempts = [
        call
        for call in resume.await_args_list
        if call.args and getattr(call.args[0], "id", None) == approval_id
    ]
    assert len(own_attempts) == 2
    assert approval.status == ApprovalStatus.TIMED_OUT
    assert run.status == RunStatus.FAILED
    assert "could not resume after 2 attempts" in run.error


@pytest.mark.asyncio
async def test_approval_timeout_sweep_reconciles_old_terminal_approval():
    """The periodic timeout job redrives a terminal approval left paused by a crash."""
    import sandcastle.queue.scheduler as scheduler
    from sandcastle.models.db import ApprovalRequest, ApprovalStatus, Run, RunStatus, async_session

    run_id, approval_id = await _create_timed_out_skip_approval(
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10)
    )
    async with async_session() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        approval.status = ApprovalStatus.SKIPPED
        approval.resolved_at = datetime.now(timezone.utc)
        await session.commit()

    async def resume(approval, output_data):
        assert approval.id == approval_id
        assert output_data is None
        async with async_session() as session:
            run = await session.get(Run, run_id)
            run.status = RunStatus.QUEUED
            await session.commit()
        return True

    with patch("sandcastle.api.routes._resume_after_approval", new=resume):
        await scheduler._check_approval_timeouts()

    async with async_session() as session:
        run = await session.get(Run, run_id)
    assert run.status == RunStatus.QUEUED


@pytest.mark.asyncio
async def test_scheduler_budget_uses_lowest_non_null_tenant_key_limit():
    """A null/unlimited key cannot mask a stricter active tenant key budget."""
    from sandcastle.models.db import ApiKey, Run, Schedule, async_session
    from sandcastle.queue.scheduler import _run_scheduled_workflow

    schedule_id = uuid.uuid4()
    tenant_id = f"budget-{uuid.uuid4()}"
    async with async_session() as session:
        session.add_all(
            [
                ApiKey(
                    id=uuid.uuid4(),
                    name="unlimited",
                    key_hash=f"unlimited-{tenant_id}",
                    is_active=True,
                    tenant_id=tenant_id,
                    max_cost_per_run_usd=None,
                ),
                ApiKey(
                    id=uuid.uuid4(),
                    name="limited",
                    key_hash=f"limited-{tenant_id}",
                    is_active=True,
                    tenant_id=tenant_id,
                    max_cost_per_run_usd=5.0,
                ),
                Schedule(
                    id=schedule_id,
                    workflow_name="budget-workflow",
                    cron_expression="* * * * *",
                    enabled=True,
                    tenant_id=tenant_id,
                ),
            ]
        )
        await session.commit()

    with (
        patch(
            "sandcastle.queue.scheduler._load_workflow_yaml",
            return_value="name: budget-workflow\nsteps: []",
        ),
        patch("sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock),
    ):
        await _run_scheduled_workflow(str(schedule_id), "budget-workflow", {})

    async with async_session() as session:
        scheduled_run = (
            await session.execute(
                select(Run).where(
                    Run.workflow_name == "budget-workflow",
                    Run.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
    assert scheduled_run.max_cost_usd == 5.0


@pytest.mark.asyncio
async def test_local_worker_timeout_marks_run_failed(monkeypatch):
    """The in-process queue uses the same job timeout as an arq worker."""
    import sandcastle.queue.worker as worker
    from sandcastle.models.db import Run, RunStatus, async_session

    run_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            Run(
                id=run_id,
                workflow_name="hung-local-job",
                status=RunStatus.QUEUED,
                input_data={},
            )
        )
        await session.commit()

    async def sleeping_job(*_args, **_kwargs):
        await asyncio.sleep(2)

    monkeypatch.setenv("SANDCASTLE_WORKER_JOB_TIMEOUT", "1")
    with (
        patch.object(worker.settings, "redis_url", ""),
        patch.object(worker, "run_workflow_job", new=sleeping_job),
    ):
        await worker.enqueue_workflow("name: hung\nsteps: []", {}, str(run_id))
        task = next(
            task for task in worker._background_tasks if task.get_name() == f"workflow-{run_id}"
        )
        await asyncio.wait_for(task, timeout=3)

    async with async_session() as session:
        run = await session.get(Run, run_id)
    assert run.status == RunStatus.FAILED
    assert "timed out after 1 seconds" in run.error


@pytest.mark.asyncio
async def test_lifespan_shutdown_cleans_up_api_enqueue_pool():
    """The API process closes a shared enqueue pool it may have created."""
    import sandcastle.main as main

    with (
        patch.object(main.settings, "scheduler_enabled", False),
        patch.object(main, "_cleanup_orphaned_runs", new=AsyncMock(return_value=(0, 0))),
        patch.object(main, "_validate_providers", new=AsyncMock()),
        patch("sandcastle.models.db._is_in_memory_sqlite", return_value=True),
        patch("sandcastle.queue.worker.cleanup_enqueue_pool", new_callable=AsyncMock) as cleanup,
    ):
        async with main.lifespan(main.app):
            pass

    cleanup.assert_awaited_once()
