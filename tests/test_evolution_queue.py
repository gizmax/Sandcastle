"""Durable queue and restart behavior for workflow evolution."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_enqueue_evolution_uses_deduplicated_redis_job():
    from sandcastle.queue import worker

    evolution_id = str(uuid.uuid4())
    pool = AsyncMock()
    old_pool = worker._enqueue_redis_pool
    worker._enqueue_redis_pool = pool
    try:
        with patch.object(worker.settings, "redis_url", "redis://localhost:6379"):
            await worker.enqueue_evolution(
                evolution_id,
                "durable-workflow",
                "cases: []",
                7,
                "balanced",
                budget_limit=2.5,
                tenant_id="tenant-a",
            )
    finally:
        worker._enqueue_redis_pool = old_pool

    pool.enqueue_job.assert_awaited_once_with(
        "run_evolution_job",
        evolution_id,
        "durable-workflow",
        "cases: []",
        7,
        "balanced",
        budget_limit=2.5,
        tenant_id="tenant-a",
        _job_id=f"evolution-{evolution_id}",
    )
    evolution_function = next(
        function
        for function in worker.WorkerSettings.functions
        if getattr(function, "name", None) == "run_evolution_job"
    )
    assert evolution_function.timeout_s == worker.settings.evolution_job_timeout


@pytest.mark.asyncio
async def test_run_evolution_job_transitions_queued_record_and_propagates_tenant():
    from sandcastle.models.db import WorkflowEvolution, async_session
    from sandcastle.queue import worker

    evolution_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            WorkflowEvolution(
                id=evolution_id,
                workflow_name="queued-evolution",
                status="queued",
                eval_suite_yaml="cases: []",
                max_iterations=3,
                optimize_for="quality",
                tenant_id="tenant-a",
            )
        )
        await session.commit()

    run_evolution = AsyncMock(
        return_value={"evolution_id": str(evolution_id), "status": "completed"}
    )
    with patch("sandcastle.engine.evolution.run_evolution", run_evolution):
        result = await worker.run_evolution_job(
            {},
            str(evolution_id),
            "queued-evolution",
            "cases: []",
            3,
            "quality",
            tenant_id="tenant-a",
        )

    assert result["status"] == "completed"
    run_evolution.assert_awaited_once()
    assert run_evolution.await_args.kwargs["tenant_id"] == "tenant-a"
    assert run_evolution.await_args.kwargs["record_exists"] is True
    async with async_session() as session:
        evolution = await session.get(WorkflowEvolution, evolution_id)
    assert evolution is not None
    assert evolution.status == "running"


@pytest.mark.asyncio
async def test_run_evolution_job_does_not_restart_cancelled_record():
    from sandcastle.models.db import WorkflowEvolution, async_session
    from sandcastle.queue import worker

    evolution_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            WorkflowEvolution(
                id=evolution_id,
                workflow_name="cancelled-evolution",
                status="cancelled",
                eval_suite_yaml="cases: []",
            )
        )
        await session.commit()

    run_evolution = AsyncMock()
    with patch("sandcastle.engine.evolution.run_evolution", run_evolution):
        result = await worker.run_evolution_job(
            {},
            str(evolution_id),
            "cancelled-evolution",
            "cases: []",
            2,
            "balanced",
        )

    assert result["status"] == "cancelled"
    run_evolution.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_job_persists_failure_reason():
    from sandcastle.models.db import WorkflowEvolution, async_session
    from sandcastle.queue import worker

    evolution_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            WorkflowEvolution(
                id=evolution_id,
                workflow_name="failed-evolution",
                status="queued",
                eval_suite_yaml="cases: []",
            )
        )
        await session.commit()

    with patch(
        "sandcastle.engine.evolution.run_evolution",
        new=AsyncMock(return_value={"status": "failed", "error": "provider unavailable"}),
    ):
        result = await worker.run_evolution_job(
            {},
            str(evolution_id),
            "failed-evolution",
            "cases: []",
            2,
            "balanced",
        )

    assert result["status"] == "failed"
    async with async_session() as session:
        evolution = await session.get(WorkflowEvolution, evolution_id)
    assert evolution is not None
    assert evolution.status == "failed"
    assert evolution.error == "provider unavailable"
    assert evolution.completed_at is not None


@pytest.mark.asyncio
async def test_local_restart_marks_active_evolutions_failed():
    from sandcastle.main import _cleanup_orphaned_evolutions
    from sandcastle.models.db import WorkflowEvolution, async_session
    from sandcastle.queue import worker

    ids = [uuid.uuid4(), uuid.uuid4()]
    async with async_session() as session:
        session.add_all(
            [
                WorkflowEvolution(
                    id=ids[0],
                    workflow_name="queued-local-evolution",
                    status="queued",
                    eval_suite_yaml="cases: []",
                ),
                WorkflowEvolution(
                    id=ids[1],
                    workflow_name="running-local-evolution",
                    status="running",
                    eval_suite_yaml="cases: []",
                ),
            ]
        )
        await session.commit()

    with patch.object(worker.settings, "redis_url", ""):
        failed, reenqueued = await _cleanup_orphaned_evolutions()

    assert failed >= 2
    assert reenqueued == 0
    async with async_session() as session:
        evolutions = [await session.get(WorkflowEvolution, item_id) for item_id in ids]
    assert all(item is not None and item.status == "failed" for item in evolutions)
    assert all(item.completed_at is not None for item in evolutions if item is not None)


@pytest.mark.asyncio
async def test_redis_restart_reenqueues_persisted_evolution():
    from sandcastle.main import _cleanup_orphaned_evolutions
    from sandcastle.models.db import WorkflowEvolution, async_session
    from sandcastle.queue import worker

    evolution_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            WorkflowEvolution(
                id=evolution_id,
                workflow_name="redis-recovered-evolution",
                status="queued",
                eval_suite_yaml="cases: []",
                max_iterations=4,
                optimize_for="cost",
                budget_limit_usd=1.25,
                tenant_id="tenant-recovery",
            )
        )
        await session.commit()

    enqueue = AsyncMock()
    with (
        patch.object(worker.settings, "redis_url", "redis://localhost:6379"),
        patch.object(worker, "enqueue_evolution", enqueue),
    ):
        failed, reenqueued = await _cleanup_orphaned_evolutions()

    assert failed == 0
    assert reenqueued >= 1
    matching = [
        call
        for call in enqueue.await_args_list
        if call.args[0] == str(evolution_id)
    ]
    assert len(matching) == 1
    assert matching[0].args[1:6] == (
        "redis-recovered-evolution",
        "cases: []",
        4,
        "cost",
    )
    assert matching[0].kwargs == {
        "budget_limit": 1.25,
        "tenant_id": "tenant-recovery",
        "mark_failed_on_error": False,
    }


@pytest.mark.asyncio
async def test_worker_recovers_only_expired_running_evolutions():
    from sandcastle.models.db import WorkflowEvolution, async_session
    from sandcastle.queue import worker

    stale_id = uuid.uuid4()
    fresh_id = uuid.uuid4()
    queued_id = uuid.uuid4()
    stale_time = datetime.now(timezone.utc) - timedelta(
        seconds=2 * worker.settings.evolution_job_timeout + 60
    )
    async with async_session() as session:
        session.add_all(
            [
                WorkflowEvolution(
                    id=stale_id,
                    workflow_name="stale-evolution",
                    status="running",
                    eval_suite_yaml="cases: []",
                    created_at=stale_time,
                    # started_at is what the reaper keys on now. Keyed on
                    # created_at it also failed jobs that were only queued, or
                    # running on another worker, on every startup.
                    started_at=stale_time,
                ),
                WorkflowEvolution(
                    id=fresh_id,
                    workflow_name="fresh-evolution",
                    status="running",
                    eval_suite_yaml="cases: []",
                ),
                WorkflowEvolution(
                    id=queued_id,
                    workflow_name="queued-evolution-recovery",
                    status="queued",
                    eval_suite_yaml="cases: []",
                    created_at=stale_time,
                ),
            ]
        )
        await session.commit()

    await worker._recover_stuck_evolutions()

    async with async_session() as session:
        stale = await session.get(WorkflowEvolution, stale_id)
        fresh = await session.get(WorkflowEvolution, fresh_id)
        queued = await session.get(WorkflowEvolution, queued_id)
    assert stale is not None and stale.status == "failed"
    assert stale.completed_at is not None
    assert fresh is not None and fresh.status == "running"
    # A row queued this long was never picked up by any worker: the API commits
    # it before enqueuing, so a failure between the two used to strand it, and
    # every later start for that workflow returned 409 "already active".
    assert queued is not None and queued.status == "failed"
    assert "queued" in (queued.error or "").lower()


@pytest.mark.asyncio
async def test_local_enqueue_tracks_evolution_task():
    from sandcastle.queue import worker

    evolution_id = str(uuid.uuid4())
    run_job = AsyncMock(return_value={"status": "completed"})
    with (
        patch.object(worker.settings, "redis_url", ""),
        patch.object(worker, "run_evolution_job", run_job),
    ):
        await worker.enqueue_evolution(
            evolution_id,
            "local-evolution",
            "cases: []",
            1,
            "balanced",
        )
        task = next(
            task
            for task in worker._background_tasks
            if task.get_name() == f"evolution-{evolution_id}"
        )
        await asyncio.wait_for(task, timeout=1)

    run_job.assert_awaited_once()
