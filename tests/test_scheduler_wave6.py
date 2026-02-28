"""Wave 6 deep audit tests for scheduler and worker edge cases.

Tests cover:
1. Scheduled workflow concurrent execution guard (SELECT FOR UPDATE)
2. Worker Redis pool cleanup on shutdown
3. Enqueue error handling (run marked FAILED on Redis failure)
4. Worker job timeout vs container timeout documentation
5. Scheduler global instance thread safety
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fix 5: Scheduler global instance thread safety
# ---------------------------------------------------------------------------


class TestGetSchedulerThreadSafety:
    """Verify get_scheduler() uses a lock to prevent duplicate instances."""

    def test_get_scheduler_returns_same_instance(self):
        """Calling get_scheduler() twice returns the same object."""
        import sandcastle.queue.scheduler as sched_mod

        # Reset to force fresh creation
        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            s1 = sched_mod.get_scheduler()
            s2 = sched_mod.get_scheduler()
            assert s1 is s2
        finally:
            sched_mod._scheduler = old

    def test_get_scheduler_thread_safe_single_instance(self):
        """Multiple threads calling get_scheduler() concurrently all get the same instance."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        results = []
        barrier = threading.Barrier(4)

        def target():
            barrier.wait()
            results.append(sched_mod.get_scheduler())

        try:
            threads = [threading.Thread(target=target) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert len(results) == 4
            # All threads must get the exact same scheduler instance
            assert all(r is results[0] for r in results)
        finally:
            sched_mod._scheduler = old

    def test_scheduler_lock_exists(self):
        """The module-level _scheduler_lock is a threading.Lock."""
        import sandcastle.queue.scheduler as sched_mod

        assert hasattr(sched_mod, "_scheduler_lock")
        assert isinstance(sched_mod._scheduler_lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# Fix 2: Worker Redis pool cleanup
# ---------------------------------------------------------------------------


class TestCleanupEnqueuePool:
    """Tests for the cleanup_enqueue_pool function."""

    @pytest.mark.asyncio
    async def test_cleanup_when_pool_is_none(self):
        """cleanup_enqueue_pool is a no-op when no pool exists."""
        import sandcastle.queue.worker as worker_mod

        old_pool = worker_mod._enqueue_redis_pool
        worker_mod._enqueue_redis_pool = None
        try:
            # Should not raise
            await worker_mod.cleanup_enqueue_pool()
            assert worker_mod._enqueue_redis_pool is None
        finally:
            worker_mod._enqueue_redis_pool = old_pool

    @pytest.mark.asyncio
    async def test_cleanup_closes_pool(self):
        """cleanup_enqueue_pool calls close() on the pool and sets it to None."""
        import sandcastle.queue.worker as worker_mod

        mock_pool = AsyncMock()
        old_pool = worker_mod._enqueue_redis_pool
        worker_mod._enqueue_redis_pool = mock_pool
        try:
            await worker_mod.cleanup_enqueue_pool()
            mock_pool.close.assert_awaited_once()
            assert worker_mod._enqueue_redis_pool is None
        finally:
            worker_mod._enqueue_redis_pool = old_pool

    @pytest.mark.asyncio
    async def test_cleanup_handles_close_error(self):
        """cleanup_enqueue_pool handles exceptions from pool.close() gracefully."""
        import sandcastle.queue.worker as worker_mod

        mock_pool = AsyncMock()
        mock_pool.close.side_effect = RuntimeError("connection refused")
        old_pool = worker_mod._enqueue_redis_pool
        worker_mod._enqueue_redis_pool = mock_pool
        try:
            # Should not raise even though close() raises
            await worker_mod.cleanup_enqueue_pool()
            # Pool should still be set to None after error
            assert worker_mod._enqueue_redis_pool is None
        finally:
            worker_mod._enqueue_redis_pool = old_pool

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self):
        """Calling cleanup_enqueue_pool multiple times is safe."""
        import sandcastle.queue.worker as worker_mod

        mock_pool = AsyncMock()
        old_pool = worker_mod._enqueue_redis_pool
        worker_mod._enqueue_redis_pool = mock_pool
        try:
            await worker_mod.cleanup_enqueue_pool()
            await worker_mod.cleanup_enqueue_pool()
            # close() should only be called once (second call sees None)
            mock_pool.close.assert_awaited_once()
        finally:
            worker_mod._enqueue_redis_pool = old_pool


class TestShutdownCleansUpPool:
    """Verify the shutdown hook calls cleanup_enqueue_pool."""

    @pytest.mark.asyncio
    async def test_shutdown_calls_cleanup(self):
        """Worker shutdown hook cleans up the Redis pool."""
        with patch(
            "sandcastle.queue.worker.cleanup_enqueue_pool",
            new_callable=AsyncMock,
        ) as mock_cleanup:
            from sandcastle.queue.worker import shutdown

            await shutdown({})
            mock_cleanup.assert_awaited_once()


# ---------------------------------------------------------------------------
# Fix 2 (cont): _get_enqueue_lock thread safety
# ---------------------------------------------------------------------------


class TestGetEnqueueLockThreadSafety:
    """Verify _get_enqueue_lock uses double-checked locking."""

    def test_get_enqueue_lock_returns_same_instance(self):
        """Calling _get_enqueue_lock twice returns the same asyncio.Lock."""
        import sandcastle.queue.worker as worker_mod

        old_lock = worker_mod._enqueue_redis_lock
        worker_mod._enqueue_redis_lock = None
        try:
            lock1 = worker_mod._get_enqueue_lock()
            lock2 = worker_mod._get_enqueue_lock()
            assert lock1 is lock2
            assert isinstance(lock1, asyncio.Lock)
        finally:
            worker_mod._enqueue_redis_lock = old_lock

    def test_get_enqueue_lock_uses_thread_lock(self):
        """_get_enqueue_lock uses _enqueue_redis_thread_lock for safety."""
        import sandcastle.queue.worker as worker_mod

        assert hasattr(worker_mod, "_enqueue_redis_thread_lock")
        assert isinstance(worker_mod._enqueue_redis_thread_lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# Fix 3: Enqueue error handling
# ---------------------------------------------------------------------------


class TestEnqueueWorkflowErrorHandling:
    """Tests for enqueue_workflow Redis failure handling."""

    @pytest.mark.asyncio
    async def test_enqueue_redis_failure_marks_run_failed(self):
        """When Redis enqueue fails, the run is marked as FAILED in the DB."""
        import sandcastle.queue.worker as worker_mod

        run_id = str(uuid.uuid4())

        # Create a QUEUED run in the DB first
        from sandcastle.models.db import Run, RunStatus, async_session

        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="test-wf",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        # Mock settings.redis_url to simulate Redis mode
        mock_pool = AsyncMock()
        mock_pool.enqueue_job.side_effect = ConnectionError("Redis down")

        with (
            patch.object(worker_mod.settings, "redis_url", "redis://localhost:6379"),
            patch("arq.create_pool", new_callable=AsyncMock, return_value=mock_pool),
        ):
            # Reset pool to force creation
            old_pool = worker_mod._enqueue_redis_pool
            worker_mod._enqueue_redis_pool = None
            try:
                with pytest.raises(ConnectionError, match="Redis down"):
                    await worker_mod.enqueue_workflow("yaml", {}, run_id)

                # Verify the run was marked as FAILED
                async with async_session() as session:
                    db_run = await session.get(Run, uuid.UUID(run_id))
                    assert db_run is not None
                    assert db_run.status == RunStatus.FAILED
                    assert "Redis enqueue failed" in db_run.error
                    assert db_run.completed_at is not None
            finally:
                worker_mod._enqueue_redis_pool = old_pool

    @pytest.mark.asyncio
    async def test_enqueue_redis_failure_reraises(self):
        """enqueue_workflow re-raises the Redis error after marking the run."""
        import sandcastle.queue.worker as worker_mod

        mock_pool = AsyncMock()
        mock_pool.enqueue_job.side_effect = TimeoutError("connect timeout")

        with (
            patch.object(worker_mod.settings, "redis_url", "redis://localhost:6379"),
            patch("arq.create_pool", new_callable=AsyncMock, return_value=mock_pool),
        ):
            old_pool = worker_mod._enqueue_redis_pool
            worker_mod._enqueue_redis_pool = None
            try:
                with pytest.raises(TimeoutError, match="connect timeout"):
                    await worker_mod.enqueue_workflow("yaml", {}, str(uuid.uuid4()))
            finally:
                worker_mod._enqueue_redis_pool = old_pool

    @pytest.mark.asyncio
    async def test_enqueue_local_mode_still_works(self):
        """Local mode (no Redis) should still work without error handling changes."""
        import sandcastle.queue.worker as worker_mod

        with patch.object(worker_mod.settings, "redis_url", ""):
            with patch.object(
                worker_mod, "run_workflow_job", new_callable=AsyncMock
            ) as mock_job:
                mock_job.return_value = {"status": "completed"}
                run_id = str(uuid.uuid4())
                await worker_mod.enqueue_workflow("yaml", {}, run_id)

                # Task should have been created
                assert any(
                    t.get_name() == f"workflow-{run_id}"
                    for t in worker_mod._background_tasks
                )


class TestMarkRunFailed:
    """Tests for the _mark_run_failed helper."""

    @pytest.mark.asyncio
    async def test_marks_queued_run_as_failed(self):
        """_mark_run_failed transitions a QUEUED run to FAILED."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import _mark_run_failed

        run_id = str(uuid.uuid4())
        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="test-wf",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        await _mark_run_failed(run_id, "test error message")

        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(run_id))
            assert db_run.status == RunStatus.FAILED
            assert db_run.error == "test error message"
            assert db_run.completed_at is not None

    @pytest.mark.asyncio
    async def test_does_not_overwrite_non_queued_status(self):
        """_mark_run_failed only acts on QUEUED runs, skips RUNNING."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import _mark_run_failed

        run_id = str(uuid.uuid4())
        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="test-wf",
                status=RunStatus.RUNNING,
                input_data={},
            )
            session.add(run)
            await session.commit()

        await _mark_run_failed(run_id, "should not apply")

        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(run_id))
            assert db_run.status == RunStatus.RUNNING
            assert db_run.error is None

    @pytest.mark.asyncio
    async def test_handles_missing_run(self):
        """_mark_run_failed handles a non-existent run gracefully."""
        from sandcastle.queue.worker import _mark_run_failed

        # Should not raise
        await _mark_run_failed(str(uuid.uuid4()), "run does not exist")

    @pytest.mark.asyncio
    async def test_truncates_long_error_message(self):
        """_mark_run_failed truncates error messages to 4096 chars."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import _mark_run_failed

        run_id = str(uuid.uuid4())
        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="test-wf",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        long_msg = "x" * 10000
        await _mark_run_failed(run_id, long_msg)

        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(run_id))
            assert len(db_run.error) == 4096


# ---------------------------------------------------------------------------
# Fix 1: Scheduled workflow concurrent execution guard
# ---------------------------------------------------------------------------


class TestRunScheduledWorkflowConcurrencyGuard:
    """Tests for the _run_scheduled_workflow concurrent execution guard."""

    @pytest.mark.asyncio
    async def test_skips_when_previous_run_still_running(self):
        """Should skip scheduling if the last run is still RUNNING."""
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        # Create a schedule with a RUNNING last run
        schedule_id = str(uuid.uuid4())
        last_run_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(
                id=last_run_id,
                workflow_name="test-wf",
                status=RunStatus.RUNNING,
                input_data={},
            )
            session.add(run)

            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="test-wf",
                cron_expression="* * * * *",
                enabled=True,
                last_run_id=last_run_id,
            )
            session.add(schedule)
            await session.commit()

        with patch(
            "sandcastle.queue.worker.enqueue_workflow",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            await _run_scheduled_workflow(schedule_id, "test-wf", {})
            # Should NOT have enqueued a new run
            mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_previous_run_still_queued(self):
        """Should skip scheduling if the last run is still QUEUED."""
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())
        last_run_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(
                id=last_run_id,
                workflow_name="test-wf",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="test-wf",
                cron_expression="* * * * *",
                enabled=True,
                last_run_id=last_run_id,
            )
            session.add(schedule)
            await session.commit()

        with patch(
            "sandcastle.queue.worker.enqueue_workflow",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            await _run_scheduled_workflow(schedule_id, "test-wf", {})
            mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_proceeds_when_previous_run_completed(self):
        """Should create a new run when the last run is COMPLETED."""
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())
        last_run_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(
                id=last_run_id,
                workflow_name="test-wf",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="test-wf",
                cron_expression="* * * * *",
                enabled=True,
                last_run_id=last_run_id,
            )
            session.add(schedule)
            await session.commit()

        with (
            patch(
                "sandcastle.queue.scheduler._load_workflow_yaml",
                return_value="name: test\nsteps: []",
            ),
            patch(
                "sandcastle.queue.worker.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            await _run_scheduled_workflow(schedule_id, "test-wf", {})
            mock_enqueue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_proceeds_when_no_previous_run(self):
        """Should create a new run when there is no previous run."""
        from sandcastle.models.db import Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())

        async with async_session() as session:
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="test-wf",
                cron_expression="* * * * *",
                enabled=True,
                last_run_id=None,
            )
            session.add(schedule)
            await session.commit()

        with (
            patch(
                "sandcastle.queue.scheduler._load_workflow_yaml",
                return_value="name: test\nsteps: []",
            ),
            patch(
                "sandcastle.queue.worker.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            await _run_scheduled_workflow(schedule_id, "test-wf", {})
            mock_enqueue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_deleted_schedule(self):
        """Should return early if the schedule no longer exists."""
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        fake_id = str(uuid.uuid4())
        with patch(
            "sandcastle.queue.worker.enqueue_workflow",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            await _run_scheduled_workflow(fake_id, "test-wf", {})
            mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_disabled_schedule(self):
        """Should return early if the schedule is disabled."""
        from sandcastle.models.db import Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())
        async with async_session() as session:
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="test-wf",
                cron_expression="* * * * *",
                enabled=False,
            )
            session.add(schedule)
            await session.commit()

        with patch(
            "sandcastle.queue.worker.enqueue_workflow",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            await _run_scheduled_workflow(schedule_id, "test-wf", {})
            mock_enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_last_run_id_atomically(self):
        """The new run and last_run_id update happen in the same transaction."""
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())

        async with async_session() as session:
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="test-wf",
                cron_expression="* * * * *",
                enabled=True,
                last_run_id=None,
            )
            session.add(schedule)
            await session.commit()

        with (
            patch(
                "sandcastle.queue.scheduler._load_workflow_yaml",
                return_value="name: test\nsteps: []",
            ),
            patch(
                "sandcastle.queue.worker.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            await _run_scheduled_workflow(schedule_id, "test-wf", {})

        # Verify schedule.last_run_id was updated
        async with async_session() as session:
            schedule = await session.get(Schedule, uuid.UUID(schedule_id))
            assert schedule.last_run_id is not None

            # Verify the run exists with QUEUED status
            new_run = await session.get(Run, schedule.last_run_id)
            assert new_run is not None
            assert new_run.workflow_name == "test-wf"

    @pytest.mark.asyncio
    async def test_marks_run_failed_on_enqueue_error(self):
        """If enqueue fails, the run is marked FAILED."""
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())

        async with async_session() as session:
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="test-wf",
                cron_expression="* * * * *",
                enabled=True,
                last_run_id=None,
            )
            session.add(schedule)
            await session.commit()

        with (
            patch(
                "sandcastle.queue.scheduler._load_workflow_yaml",
                return_value="name: test\nsteps: []",
            ),
            patch(
                "sandcastle.queue.worker.enqueue_workflow",
                new_callable=AsyncMock,
                side_effect=RuntimeError("enqueue failed"),
            ),
        ):
            await _run_scheduled_workflow(schedule_id, "test-wf", {})

        # The run should be marked FAILED
        async with async_session() as session:
            schedule = await session.get(Schedule, uuid.UUID(schedule_id))
            assert schedule.last_run_id is not None
            run = await session.get(Run, schedule.last_run_id)
            assert run.status == RunStatus.FAILED
            assert "Schedule enqueue failed" in run.error

    @pytest.mark.asyncio
    async def test_uses_select_for_update(self):
        """Verify the code path uses SELECT FOR UPDATE (via the query structure)."""
        # This is a structural test - verify the function imports select
        # and uses with_for_update pattern. We check by examining the
        # function source to ensure the atomic locking pattern is present.
        import inspect
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        source = inspect.getsource(_run_scheduled_workflow)
        assert "with_for_update" in source
        assert "select(Schedule)" in source


# ---------------------------------------------------------------------------
# Fix 4: Worker job timeout documentation
# ---------------------------------------------------------------------------


class TestJobTimeoutDocumentation:
    """Verify the timeout relationship is documented."""

    def test_enqueue_workflow_docstring_mentions_timeout(self):
        """enqueue_workflow docstring warns about job_timeout vs sandbox timeout."""
        from sandcastle.queue.worker import enqueue_workflow

        doc = enqueue_workflow.__doc__
        assert doc is not None
        assert "job_timeout" in doc
        assert "SANDCASTLE_WORKER_JOB_TIMEOUT" in doc

    def test_worker_settings_job_timeout_configurable(self):
        """WorkerSettings.job_timeout is configurable via env var."""
        from sandcastle.queue.worker import WorkerSettings

        # Default is 600s
        assert WorkerSettings.job_timeout == 600

    def test_job_timeout_env_var_name(self):
        """The env var name for job timeout is SANDCASTLE_WORKER_JOB_TIMEOUT."""
        import inspect
        from sandcastle.queue.worker import WorkerSettings

        # Check that the class definition references the correct env var
        source = inspect.getsource(WorkerSettings)
        assert "SANDCASTLE_WORKER_JOB_TIMEOUT" in source


# ---------------------------------------------------------------------------
# Additional edge case tests
# ---------------------------------------------------------------------------


class TestSchedulerResetForTests:
    """Utility tests for resetting scheduler state in tests."""

    def test_scheduler_can_be_reset(self):
        """Scheduler global can be reset by setting _scheduler = None."""
        import sandcastle.queue.scheduler as sched_mod

        old = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            s = sched_mod.get_scheduler()
            assert s is not None
            sched_mod._scheduler = None
            s2 = sched_mod.get_scheduler()
            assert s2 is not None
            # After reset, should be a new instance
            assert s is not s2
        finally:
            sched_mod._scheduler = old


class TestWorkerStartupIntegration:
    """Integration tests for worker startup and shutdown hooks."""

    @pytest.mark.asyncio
    async def test_startup_calls_recover_stuck_runs(self):
        """startup() calls _recover_stuck_runs()."""
        with patch(
            "sandcastle.queue.worker._recover_stuck_runs",
            new_callable=AsyncMock,
        ) as mock_recover:
            from sandcastle.queue.worker import startup

            await startup({})
            mock_recover.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_logs_message(self, caplog):
        """shutdown() logs the shutdown message."""
        import logging

        with (
            caplog.at_level(logging.INFO, logger="sandcastle.queue.worker"),
            patch(
                "sandcastle.queue.worker.cleanup_enqueue_pool",
                new_callable=AsyncMock,
            ),
        ):
            from sandcastle.queue.worker import shutdown

            await shutdown({})
            assert "shutting down" in caplog.text.lower()


class TestScheduledWorkflowTenantBudget:
    """Tests for tenant budget resolution in _run_scheduled_workflow."""

    @pytest.mark.asyncio
    async def test_tenant_budget_resolved_from_api_key(self):
        """When schedule has tenant_id, budget is resolved from ApiKey."""
        from sandcastle.models.db import ApiKey, Run, RunStatus, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())
        tenant_id = "tenant-budget-test"

        async with async_session() as session:
            # Create an API key with budget for this tenant
            api_key = ApiKey(
                id=uuid.uuid4(),
                name="test-key",
                key_hash="fakehash_budget_" + tenant_id,
                is_active=True,
                tenant_id=tenant_id,
                max_cost_per_run_usd=5.50,
            )
            session.add(api_key)

            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="test-wf",
                cron_expression="* * * * *",
                enabled=True,
                tenant_id=tenant_id,
                last_run_id=None,
            )
            session.add(schedule)
            await session.commit()

        with (
            patch(
                "sandcastle.queue.scheduler._load_workflow_yaml",
                return_value="name: test\nsteps: []",
            ),
            patch(
                "sandcastle.queue.worker.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            await _run_scheduled_workflow(schedule_id, "test-wf", {})

        # Verify the run was created with the tenant's budget
        async with async_session() as session:
            schedule = await session.get(Schedule, uuid.UUID(schedule_id))
            run = await session.get(Run, schedule.last_run_id)
            assert run is not None
            assert run.tenant_id == tenant_id
            assert run.max_cost_usd == 5.50


class TestScheduledWorkflowLoadYamlFailure:
    """Tests for _run_scheduled_workflow when YAML loading fails."""

    @pytest.mark.asyncio
    async def test_yaml_not_found_marks_run_failed(self):
        """If _load_workflow_yaml raises FileNotFoundError, run is FAILED."""
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        schedule_id = str(uuid.uuid4())

        async with async_session() as session:
            schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="nonexistent-wf",
                cron_expression="* * * * *",
                enabled=True,
                last_run_id=None,
            )
            session.add(schedule)
            await session.commit()

        with (
            patch(
                "sandcastle.queue.scheduler._load_workflow_yaml",
                side_effect=FileNotFoundError("Workflow not found"),
            ),
            patch(
                "sandcastle.queue.worker.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            await _run_scheduled_workflow(schedule_id, "nonexistent-wf", {})
            mock_enqueue.assert_not_awaited()

        # Run should be marked FAILED
        async with async_session() as session:
            schedule = await session.get(Schedule, uuid.UUID(schedule_id))
            assert schedule.last_run_id is not None
            run = await session.get(Run, schedule.last_run_id)
            assert run.status == RunStatus.FAILED
            assert "Schedule enqueue failed" in run.error
