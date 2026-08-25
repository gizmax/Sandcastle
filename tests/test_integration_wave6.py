"""Wave 6 deep audit - integration tests for missing coverage areas.

Covers: worker duplicate delivery guard, DB persist retry exhaustion,
stuck run recovery, scheduler invalid cron restore, _escape_braces,
_save_checkpoint failure, and assertion-less test backfills.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Worker duplicate delivery guard
# ---------------------------------------------------------------------------


class TestWorkerDuplicateDeliveryGuard:
    """run_workflow_job must skip execution when run status is not QUEUED."""

    @pytest.mark.asyncio
    async def test_running_status_skips_execution(self):
        """A run already in RUNNING state should be skipped immediately."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        # Insert a run that is already RUNNING
        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="dup-guard-test",
                status=RunStatus.RUNNING,
                input_data={"key": "value"},
            )
            session.add(run)
            await session.commit()

        result = await run_workflow_job(
            ctx={},
            workflow_yaml="name: test\nsteps: []",
            input_data={},
            run_id=run_id,
        )

        # Should return early with the current status
        assert result["run_id"] == run_id
        assert result["status"] == "running"

        # Verify the run was NOT modified in the DB (still RUNNING, no started_at change)
        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run is not None
            assert run.status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_completed_status_skips_execution(self):
        """A run already COMPLETED should be skipped."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="dup-guard-completed",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        result = await run_workflow_job(
            ctx={},
            workflow_yaml="name: test\nsteps: []",
            input_data={},
            run_id=run_id,
        )

        assert result["run_id"] == run_id
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failed_status_skips_execution(self):
        """A run already FAILED should be skipped."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="dup-guard-failed",
                status=RunStatus.FAILED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        result = await run_workflow_job(
            ctx={},
            workflow_yaml="name: test\nsteps: []",
            input_data={},
            run_id=run_id,
        )

        assert result["run_id"] == run_id
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# 2. Worker DB persist retry exhaustion
# ---------------------------------------------------------------------------


class TestWorkerDBPersistRetryExhaustion:
    """Test the 3-attempt retry loop when DB persist fails."""

    @pytest.mark.asyncio
    async def test_three_failures_logged_no_crash(self, caplog):
        """When DB commit fails 3 times, function logs error but doesn't crash."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        # Create a real QUEUED run that will transition to RUNNING
        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="retry-exhaust-test",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        # Mock execute_workflow to return a successful result
        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.outputs = {"result": "ok"}
        mock_result.total_cost_usd = 0.01
        mock_result.error = None
        mock_result.started_at = datetime.now(timezone.utc)
        mock_result.completed_at = datetime.now(timezone.utc)

        # Track how many times async_session is called during the persist phase.
        # The imports inside run_workflow_job are local, so we patch at the
        # source module level.
        original_async_session = async_session
        call_count = 0

        def make_failing_session():
            nonlocal call_count
            call_count += 1
            ctx = original_async_session()
            if call_count >= 2:
                # Wrap the context manager to fail on commit for the retry loop
                original_ctx = ctx

                class FailCommitCtx:
                    async def __aenter__(self):
                        self._session = await original_ctx.__aenter__()

                        async def failing_commit():
                            raise Exception("Simulated DB failure")

                        self._session.commit = failing_commit
                        return self._session

                    async def __aexit__(self, *args):
                        return await original_ctx.__aexit__(*args)

                return FailCommitCtx()
            return ctx

        with (
            patch(
                "sandcastle.engine.executor.execute_workflow",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("sandcastle.engine.dag.parse_yaml_string") as mock_parse,
            patch("sandcastle.engine.dag.validate", return_value=[]),
            patch("sandcastle.engine.dag.build_plan"),
            patch("sandcastle.engine.storage.create_storage"),
            patch(
                "sandcastle.webhooks.dispatcher.dispatch_webhook",
                new_callable=AsyncMock,
            ),
            patch(
                "sandcastle.models.db.async_session",
                side_effect=make_failing_session,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level(logging.WARNING),
        ):
            mock_wf = MagicMock()
            mock_wf.name = "retry-exhaust-test"
            mock_wf.on_complete = None
            mock_wf.on_failure = None
            mock_parse.return_value = mock_wf

            result = await run_workflow_job(
                ctx={},
                workflow_yaml="name: retry-exhaust-test\nsteps: []",
                input_data={},
                run_id=run_id,
            )

        # The function should complete without crashing
        assert result["run_id"] == run_id
        assert result["status"] == "completed"
        # All 3 attempts should have been made (call_count >= 4: 1 initial + 3 retries)
        assert call_count >= 4
        # Verify warning/error was logged about DB persist failures
        persist_messages = [
            r for r in caplog.records
            if "persist" in r.message.lower() or "db" in r.message.lower()
        ]
        assert len(persist_messages) >= 1


# ---------------------------------------------------------------------------
# 3. Stuck run recovery
# ---------------------------------------------------------------------------


class TestStuckRunRecovery:
    """Test _recover_stuck_runs identifies runs stranded past 2x the timeout."""

    @pytest.mark.asyncio
    async def test_running_run_beyond_threshold_recovered(self):
        """A run past 2x timeout is picked up and no longer left RUNNING.

        0.46 resumes such a run instead of burying it (see
        tests/test_crash_resume_046.py). What this test still pins is the
        *detection*: with crash resume opted out, the pre-0.46 answer stands,
        which is the behaviour every deployment falls back to.
        """
        from sandcastle.config import settings
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import _recover_stuck_runs

        settings.crash_resume_enabled = False

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        # Create a run that started 30 minutes ago (well past 2*600s = 1200s)
        old_time = datetime.now(timezone.utc) - timedelta(minutes=30)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="stuck-running",
                status=RunStatus.RUNNING,
                input_data={},
                started_at=old_time,
            )
            session.add(run)
            await session.commit()

        await _recover_stuck_runs()

        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run is not None
            assert run.status == RunStatus.FAILED
            assert run.completed_at is not None
            assert "crashed or timed out" in run.error.lower()

    @pytest.mark.asyncio
    async def test_queued_run_beyond_threshold_is_not_recovered(self):
        """Queued Redis jobs may wait under backlog and must remain queued."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import _recover_stuck_runs

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        old_time = datetime.now(timezone.utc) - timedelta(minutes=30)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="stuck-queued",
                status=RunStatus.QUEUED,
                input_data={},
                created_at=old_time,
            )
            session.add(run)
            await session.commit()

        await _recover_stuck_runs()

        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run is not None
            assert run.status == RunStatus.QUEUED
            assert run.completed_at is None
            assert run.error is None

    @pytest.mark.asyncio
    async def test_recent_running_run_not_recovered(self):
        """Runs started recently should NOT be marked as stuck."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import _recover_stuck_runs

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        # Started just 1 minute ago - well within 2*600s threshold
        recent_time = datetime.now(timezone.utc) - timedelta(minutes=1)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="recent-running",
                status=RunStatus.RUNNING,
                input_data={},
                started_at=recent_time,
            )
            session.add(run)
            await session.commit()

        await _recover_stuck_runs()

        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run is not None
            assert run.status == RunStatus.RUNNING
            assert run.error is None

    @pytest.mark.asyncio
    async def test_completed_run_not_affected(self):
        """Runs already COMPLETED should not be touched by recovery."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import _recover_stuck_runs

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        old_time = datetime.now(timezone.utc) - timedelta(hours=2)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="completed-old",
                status=RunStatus.COMPLETED,
                input_data={},
                started_at=old_time,
                completed_at=old_time + timedelta(minutes=5),
            )
            session.add(run)
            await session.commit()

        await _recover_stuck_runs()

        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run is not None
            assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_mixed_stuck_and_recent_runs(self):
        """Only stuck runs should be recovered, recent ones left alone."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import _recover_stuck_runs

        stuck_id = uuid.uuid4()
        recent_id = uuid.uuid4()

        old_time = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_time = datetime.now(timezone.utc) - timedelta(seconds=30)

        async with async_session() as session:
            stuck_run = Run(
                id=stuck_id,
                workflow_name="stuck",
                status=RunStatus.RUNNING,
                input_data={},
                started_at=old_time,
            )
            recent_run = Run(
                id=recent_id,
                workflow_name="recent",
                status=RunStatus.RUNNING,
                input_data={},
                started_at=recent_time,
            )
            session.add(stuck_run)
            session.add(recent_run)
            await session.commit()

        await _recover_stuck_runs()

        async with async_session() as session:
            stuck = await session.get(Run, stuck_id)
            recent = await session.get(Run, recent_id)

            assert stuck.status == RunStatus.FAILED
            assert stuck.error is not None
            assert recent.status == RunStatus.RUNNING
            assert recent.error is None


# ---------------------------------------------------------------------------
# 4. Scheduler restore_schedules with invalid cron
# ---------------------------------------------------------------------------


class TestSchedulerInvalidCronRestore:
    """Test restore_schedules handles invalid cron expressions gracefully."""

    @pytest.mark.asyncio
    async def test_invalid_cron_disables_schedule(self):
        """A schedule with invalid cron_expression should be disabled on restore."""
        from sandcastle.models.db import Schedule, async_session
        from sandcastle.queue.scheduler import restore_schedules

        sched_id = uuid.uuid4()

        async with async_session() as session:
            schedule = Schedule(
                id=sched_id,
                workflow_name="some-workflow",
                cron_expression="INVALID CRON EXPRESSION",
                enabled=True,
                input_data={},
            )
            session.add(schedule)
            await session.commit()

        # restore_schedules should handle the invalid cron and disable it
        await restore_schedules()

        async with async_session() as session:
            schedule = await session.get(Schedule, sched_id)
            assert schedule is not None
            assert schedule.enabled is False

    @pytest.mark.asyncio
    async def test_valid_cron_remains_enabled(self):
        """Schedules with valid cron expressions should remain enabled."""
        from sandcastle.models.db import Schedule, async_session
        from sandcastle.queue.scheduler import get_scheduler, restore_schedules

        sched_id = uuid.uuid4()

        async with async_session() as session:
            schedule = Schedule(
                id=sched_id,
                workflow_name="valid-cron-wf",
                cron_expression="0 * * * *",  # Every hour - valid cron
                enabled=True,
                input_data={},
            )
            session.add(schedule)
            await session.commit()

        # Ensure the scheduler is running so add_schedule works
        scheduler = get_scheduler()
        if not scheduler.running:
            scheduler.start()

        try:
            # Mock the workflow file check in add_schedule (workflows dir may not exist)
            with patch(
                "sandcastle.queue.scheduler.Path.is_dir",
                return_value=False,
            ):
                await restore_schedules()

            async with async_session() as session:
                schedule = await session.get(Schedule, sched_id)
                assert schedule is not None
                assert schedule.enabled is True
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_empty_cron_disables_schedule(self):
        """A schedule with empty cron_expression should be disabled on restore."""
        from sandcastle.models.db import Schedule, async_session
        from sandcastle.queue.scheduler import restore_schedules

        sched_id = uuid.uuid4()

        async with async_session() as session:
            schedule = Schedule(
                id=sched_id,
                workflow_name="empty-cron-wf",
                cron_expression="",
                enabled=True,
                input_data={},
            )
            session.add(schedule)
            await session.commit()

        await restore_schedules()

        async with async_session() as session:
            schedule = await session.get(Schedule, sched_id)
            assert schedule is not None
            assert schedule.enabled is False


# ---------------------------------------------------------------------------
# 5. Tests with no assertions - add real assertions
# ---------------------------------------------------------------------------


class TestBackfillAssertions:
    """Add assertions to tests that previously had none."""

    # --- test_executor.py: test_no_csv_output_config ---
    def test_csv_output_none_returns_none(self):
        """_write_csv_output with csv_output=None should return early (no files)."""
        import tempfile

        from sandcastle.engine.dag import StepDefinition
        from sandcastle.engine.executor import _write_csv_output

        step = StepDefinition(
            id="noop-csv",
            prompt="test",
            model="test",
        )
        step.csv_output = None

        # Capture return value - function should exit early without error
        result = _write_csv_output(step, {"x": 1}, "run-noop")
        assert result is None

    # --- test_storage.py: test_delete_nonexistent_is_noop ---
    @pytest.mark.asyncio
    async def test_delete_nonexistent_file_succeeds(self):
        """Deleting a non-existent file should succeed and not raise."""
        import tempfile

        from sandcastle.engine.storage import LocalStorage

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_dir=tmpdir)

            # Delete should not raise
            await storage.delete("does-not-exist.txt")

            # Verify the file still doesn't exist (read returns None)
            result = await storage.read("does-not-exist.txt")
            assert result is None

    # --- test_performance.py: test_allows_under_limit ---
    @pytest.mark.asyncio
    async def test_rate_limiter_allows_under_limit(self):
        """RateLimiter should not raise for requests under the limit."""
        from sandcastle.api.rate_limit import RateLimiter

        limiter = RateLimiter(max_requests=5, window_seconds=60)
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "10.0.0.1"
        req.state = MagicMock()
        req.state.tenant_id = None

        # All 5 requests should succeed without raising
        for i in range(5):
            await limiter.check(req)

        # Verify the 6th DOES raise (proves the first 5 were correctly counted)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await limiter.check(req)
        assert exc_info.value.status_code == 429

    # --- test_performance.py: test_separate_keys_per_ip ---
    @pytest.mark.asyncio
    async def test_separate_rate_limit_keys_per_ip(self):
        """Different IPs should have independent rate limit counters."""
        from fastapi import HTTPException

        from sandcastle.api.rate_limit import RateLimiter

        limiter = RateLimiter(max_requests=2, window_seconds=60)

        def make_req(ip):
            req = MagicMock()
            req.client = MagicMock()
            req.client.host = ip
            req.state = MagicMock()
            req.state.tenant_id = None
            return req

        req1 = make_req("10.0.0.1")
        req2 = make_req("10.0.0.2")

        # Exhaust limit for IP1
        for _ in range(2):
            await limiter.check(req1)

        # IP1 should now be rate limited
        with pytest.raises(HTTPException):
            await limiter.check(req1)

        # IP2 should still be allowed (independent counter)
        await limiter.check(req2)  # Should not raise

    # --- test_settings.py: test_require_admin_passes_for_admin ---
    def test_require_admin_passes_for_admin_returns_none(self):
        """_require_admin should return None (no exception) for admin users."""
        from sandcastle.api.routes import _require_admin
        from sandcastle.config import settings

        settings.auth_required = True
        mock_request = MagicMock()

        with patch("sandcastle.api.routes.is_admin", return_value=True):
            result = _require_admin(mock_request)
            assert result is None

    # --- test_settings.py: test_require_admin_passes_when_auth_disabled ---
    def test_require_admin_passes_when_auth_disabled_returns_none(self):
        """_require_admin should return None when auth_required is False.

        When auth is disabled, is_admin() returns True (everyone is admin).
        """
        from sandcastle.api.routes import _require_admin
        from sandcastle.config import settings

        settings.auth_required = False
        mock_request = MagicMock()

        # When auth_required=False, is_admin returns True
        with patch("sandcastle.api.routes.is_admin", return_value=True):
            result = _require_admin(mock_request)
            assert result is None

    # --- test_mcp.py: test_module_imports ---
    def test_mcp_module_has_expected_attributes(self):
        """Importing sandcastle.mcp_server should expose create_mcp_server."""
        import sandcastle.mcp_server as mcp_mod

        assert hasattr(mcp_mod, "create_mcp_server")
        assert callable(mcp_mod.create_mcp_server)

    # --- test_backends.py: test_close_is_noop (E2B) ---
    @pytest.mark.asyncio
    async def test_e2b_close_is_noop(self):
        """E2BBackend.close() should return without error or side effects."""
        from sandcastle.engine.backends import E2BBackend

        backend = E2BBackend(e2b_api_key="test-key")
        result = await backend.close()
        # close() returns None and doesn't alter state
        assert result is None

    # --- test_backends.py: test_close_is_noop (Docker) ---
    @pytest.mark.asyncio
    async def test_docker_close_is_noop(self):
        """DockerBackend.close() should return without error."""
        from sandcastle.engine.backends import DockerBackend

        backend = DockerBackend()
        result = await backend.close()
        assert result is None

    # --- test_backends.py: test_close_is_noop (Local) ---
    @pytest.mark.asyncio
    async def test_local_close_is_noop(self):
        """LocalBackend.close() should return without error."""
        from sandcastle.engine.backends import LocalBackend

        backend = LocalBackend()
        result = await backend.close()
        assert result is None

    # --- test_backends.py: test_close_is_noop (Cloudflare) ---
    @pytest.mark.asyncio
    async def test_cloudflare_close_is_noop(self):
        """CloudflareBackend.close() should return without error."""
        from sandcastle.engine.backends import CloudflareBackend

        backend = CloudflareBackend(
            worker_url="https://sandbox.example.workers.dev"
        )
        result = await backend.close()
        assert result is None


# ---------------------------------------------------------------------------
# 6. _escape_braces test coverage
# ---------------------------------------------------------------------------


class TestEscapeBraces:
    """Tests for _escape_braces helper in executor.py."""

    def test_empty_string(self):
        from sandcastle.engine.executor import _escape_braces

        assert _escape_braces("") == ""

    def test_no_braces(self):
        from sandcastle.engine.executor import _escape_braces

        assert _escape_braces("hello world") == "hello world"

    def test_single_open_brace(self):
        from sandcastle.engine.executor import _escape_braces

        assert _escape_braces("before { after") == "before {{ after"

    def test_single_close_brace(self):
        from sandcastle.engine.executor import _escape_braces

        assert _escape_braces("before } after") == "before }} after"

    def test_matched_braces(self):
        from sandcastle.engine.executor import _escape_braces

        assert _escape_braces("{key}") == "{{key}}"

    def test_already_doubled_braces(self):
        """Already doubled braces should get doubled again (4 total)."""
        from sandcastle.engine.executor import _escape_braces

        assert _escape_braces("{{x}}") == "{{{{x}}}}"

    def test_mixed_content(self):
        from sandcastle.engine.executor import _escape_braces

        result = _escape_braces("Hello {name}, your balance is ${amount}")
        assert result == "Hello {{name}}, your balance is ${{amount}}"

    def test_multiple_brace_pairs(self):
        from sandcastle.engine.executor import _escape_braces

        result = _escape_braces("{a} and {b}")
        assert result == "{{a}} and {{b}}"

    def test_nested_braces(self):
        from sandcastle.engine.executor import _escape_braces

        result = _escape_braces("{outer {inner} end}")
        assert result == "{{outer {{inner}} end}}"

    def test_only_braces(self):
        from sandcastle.engine.executor import _escape_braces

        assert _escape_braces("{}") == "{{}}"

    def test_json_like_content(self):
        """JSON-like strings should have all braces escaped."""
        from sandcastle.engine.executor import _escape_braces

        result = _escape_braces('{"key": "value", "num": 42}')
        assert result == '{{"key": "value", "num": 42}}'

    def test_preserves_non_brace_special_chars(self):
        from sandcastle.engine.executor import _escape_braces

        result = _escape_braces("tab\there\nnewline [bracket] (paren)")
        assert result == "tab\there\nnewline [bracket] (paren)"


# ---------------------------------------------------------------------------
# 7. _save_checkpoint failure path
# ---------------------------------------------------------------------------


class TestSaveCheckpointFailure:
    """Test that _save_checkpoint logs warning on DB failure but doesn't crash."""

    @pytest.mark.asyncio
    async def test_db_failure_logs_warning(self, caplog):
        """_save_checkpoint should log a warning when commit fails."""
        from sandcastle.engine.executor import RunContext, _save_checkpoint

        run_id = str(uuid.uuid4())
        context = RunContext(
            run_id=run_id,
            input={"test": "data"},
            step_outputs={"step1": "output1"},
        )

        # Mock async_session to fail on commit
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock(
            side_effect=Exception("Simulated DB error")
        )

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "sandcastle.models.db.async_session",
                return_value=mock_ctx,
            ),
            caplog.at_level(logging.WARNING),
        ):
            # Should not raise
            await _save_checkpoint(
                run_id=run_id,
                step_id="step1",
                stage_index=0,
                context=context,
            )

        # Verify warning was logged
        assert any("checkpoint" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_successful_checkpoint_saves_data(self):
        """_save_checkpoint should create a RunCheckpoint record on success."""
        from sandcastle.engine.executor import RunContext, _save_checkpoint
        from sandcastle.models.db import Run, RunCheckpoint, RunStatus, async_session

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        # Create the parent run first (FK constraint)
        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="checkpoint-test",
                status=RunStatus.RUNNING,
                input_data={},
            )
            session.add(run)
            await session.commit()

        context = RunContext(
            run_id=run_id,
            input={"foo": "bar"},
            step_outputs={"step1": "result"},
        )

        await _save_checkpoint(
            run_id=run_id,
            step_id="step1",
            stage_index=0,
            context=context,
        )

        # Verify checkpoint was saved
        from sqlalchemy import select

        async with async_session() as session:
            stmt = select(RunCheckpoint).where(
                RunCheckpoint.run_id == run_uuid
            )
            result = await session.execute(stmt)
            checkpoint = result.scalar_one_or_none()

            assert checkpoint is not None
            assert checkpoint.step_id == "step1"
            assert checkpoint.stage_index == 0
            assert checkpoint.context_snapshot["input"] == {"foo": "bar"}
            assert checkpoint.context_snapshot["step_outputs"] == {
                "step1": "result"
            }

    @pytest.mark.asyncio
    async def test_checkpoint_failure_does_not_propagate(self):
        """Even with a total DB meltdown, _save_checkpoint should not raise."""
        from sandcastle.engine.executor import RunContext, _save_checkpoint

        context = RunContext(
            run_id=str(uuid.uuid4()),
            input={},
        )

        # Make async_session itself throw
        with patch(
            "sandcastle.models.db.async_session",
            side_effect=Exception("Total DB failure"),
        ):
            # Must not raise
            await _save_checkpoint(
                run_id=context.run_id,
                step_id="broken",
                stage_index=0,
                context=context,
            )
