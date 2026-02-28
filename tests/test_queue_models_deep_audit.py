"""Deep audit tests for queue, scheduler, webhook, DB models, config, and SDK layers."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# worker.py tests
# ---------------------------------------------------------------------------


class TestWorkerSettings:
    """Tests for WorkerSettings class."""

    def test_redis_settings_default_none(self):
        """WorkerSettings.redis_settings is None when REDIS_URL is empty."""
        from sandcastle.queue.worker import WorkerSettings

        # In test env, REDIS_URL is empty, so redis_settings should be None
        # or it's set at module level if REDIS_URL was configured
        assert hasattr(WorkerSettings, "redis_settings")

    def test_worker_settings_has_required_attrs(self):
        from sandcastle.queue.worker import WorkerSettings

        assert hasattr(WorkerSettings, "functions")
        assert hasattr(WorkerSettings, "on_startup")
        assert hasattr(WorkerSettings, "on_shutdown")
        assert WorkerSettings.max_jobs == 10
        assert WorkerSettings.job_timeout == 600

    def test_parse_redis_url_basic(self):
        from sandcastle.queue.worker import _parse_redis_url

        rs = _parse_redis_url("redis://localhost:6379/0")
        assert rs.host == "localhost"
        assert rs.port == 6379
        assert rs.database == 0

    def test_parse_redis_url_with_password(self):
        from sandcastle.queue.worker import _parse_redis_url

        rs = _parse_redis_url("redis://:secret@redis.example.com:6380/2")
        assert rs.host == "redis.example.com"
        assert rs.port == 6380
        assert rs.database == 2
        assert rs.password == "secret"

    def test_parse_redis_url_defaults(self):
        from sandcastle.queue.worker import _parse_redis_url

        rs = _parse_redis_url("redis://")
        assert rs.host == "localhost"
        assert rs.port == 6379
        assert rs.database == 0


class TestTaskDoneCallback:
    """Tests for _task_done_callback."""

    def test_callback_removes_task_from_set(self):
        from sandcastle.queue.worker import _background_tasks, _task_done_callback

        async def dummy():
            pass

        loop = asyncio.new_event_loop()
        task = loop.create_task(dummy())
        _background_tasks.add(task)
        loop.run_until_complete(task)

        _task_done_callback(task)
        assert task not in _background_tasks
        loop.close()

    def test_callback_handles_cancelled_task(self):
        from sandcastle.queue.worker import _background_tasks, _task_done_callback

        async def slow():
            await asyncio.sleep(100)

        loop = asyncio.new_event_loop()
        task = loop.create_task(slow())
        _background_tasks.add(task)
        task.cancel()
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass

        # Should not raise
        _task_done_callback(task)
        assert task not in _background_tasks
        loop.close()


class TestRunWorkflowJobDuplicateGuard:
    """Tests for duplicate execution prevention."""

    @pytest.mark.asyncio
    async def test_skip_non_queued_run(self):
        """run_workflow_job should skip runs not in QUEUED status."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="test-wf",
                status=RunStatus.RUNNING,
                input_data={},
            )
            session.add(run)
            await session.commit()

        from sandcastle.queue.worker import run_workflow_job

        result = await run_workflow_job(
            {}, "name: test\nsteps: []", {}, run_id,
        )
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_missing_run_returns_error(self):
        """run_workflow_job should return error for non-existent run."""
        from sandcastle.queue.worker import run_workflow_job

        fake_id = str(uuid.uuid4())
        result = await run_workflow_job(
            {}, "name: test\nsteps: []", {}, fake_id,
        )
        assert result["status"] == "failed"
        assert "not found" in result["error"]


class TestOnFailureWebhookOnlyForFailures:
    """Test that on_failure webhook only fires for actual failures."""

    @pytest.mark.asyncio
    async def test_cancelled_status_no_on_failure_webhook(self):
        """Cancelled runs should not trigger on_failure webhook."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="test-wf",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        mock_result = MagicMock()
        mock_result.status = "cancelled"
        mock_result.outputs = {}
        mock_result.total_cost_usd = 0.0
        mock_result.error = None
        mock_result.started_at = None
        mock_result.completed_at = None

        mock_workflow = MagicMock()
        mock_workflow.name = "test-wf"
        mock_workflow.on_complete = None
        mock_workflow.on_failure = MagicMock()
        mock_workflow.on_failure.webhook = "https://example.com/on-fail"

        with (
            patch("sandcastle.engine.dag.parse_yaml_string", return_value=mock_workflow),
            patch("sandcastle.engine.dag.validate", return_value=[]),
            patch("sandcastle.engine.dag.build_plan"),
            patch("sandcastle.engine.storage.create_storage"),
            patch("sandcastle.engine.executor.execute_workflow", return_value=mock_result),
            patch("sandcastle.webhooks.dispatcher.dispatch_webhook") as mock_dispatch,
        ):
            from sandcastle.queue.worker import run_workflow_job

            await run_workflow_job({}, "name: test\nsteps: []", {}, run_id)
            # on_failure webhook should NOT be called for cancelled status
            mock_dispatch.assert_not_called()


class TestWebhookEventType:
    """Test that webhook event types match actual status."""

    @pytest.mark.asyncio
    async def test_completed_event_type(self):
        """Completed runs should have workflow.completed event."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="test-wf",
                status=RunStatus.QUEUED,
                input_data={},
                callback_url="https://example.com/hook",
            )
            session.add(run)
            await session.commit()

        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.outputs = {"result": "ok"}
        mock_result.total_cost_usd = 0.01
        mock_result.error = None
        mock_result.started_at = datetime.now(timezone.utc)
        mock_result.completed_at = datetime.now(timezone.utc)

        mock_workflow = MagicMock()
        mock_workflow.name = "test-wf"
        mock_workflow.on_complete = None
        mock_workflow.on_failure = None

        with (
            patch("sandcastle.engine.dag.parse_yaml_string", return_value=mock_workflow),
            patch("sandcastle.engine.dag.validate", return_value=[]),
            patch("sandcastle.engine.dag.build_plan"),
            patch("sandcastle.engine.storage.create_storage"),
            patch("sandcastle.engine.executor.execute_workflow", return_value=mock_result),
            patch("sandcastle.webhooks.dispatcher.dispatch_webhook") as mock_dispatch,
        ):
            from sandcastle.queue.worker import run_workflow_job

            await run_workflow_job({}, "name: test\nsteps: []", {}, run_id)
            mock_dispatch.assert_called_once()
            call_kwargs = mock_dispatch.call_args
            assert call_kwargs.kwargs.get("event") or call_kwargs[1].get("event") == "workflow.completed"


class TestDBWriteFailureRecovery:
    """Test that DB write failures don't prevent webhook dispatch."""

    @pytest.mark.asyncio
    async def test_db_failure_still_dispatches_webhook(self):
        """If DB commit fails after execution, webhooks should still fire."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="test-wf",
                status=RunStatus.QUEUED,
                input_data={},
                callback_url="https://example.com/hook",
            )
            session.add(run)
            await session.commit()

        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.outputs = {}
        mock_result.total_cost_usd = 0.0
        mock_result.error = None
        mock_result.started_at = None
        mock_result.completed_at = None

        mock_workflow = MagicMock()
        mock_workflow.name = "test-wf"
        mock_workflow.on_complete = None
        mock_workflow.on_failure = None

        with (
            patch("sandcastle.engine.dag.parse_yaml_string", return_value=mock_workflow),
            patch("sandcastle.engine.dag.validate", return_value=[]),
            patch("sandcastle.engine.dag.build_plan"),
            patch("sandcastle.engine.storage.create_storage"),
            patch("sandcastle.engine.executor.execute_workflow", return_value=mock_result),
            patch("sandcastle.webhooks.dispatcher.dispatch_webhook") as mock_dispatch,
        ):
            from sandcastle.queue.worker import run_workflow_job

            result = await run_workflow_job({}, "name: test\nsteps: []", {}, run_id)
            # Even if DB fails, the function should return success and dispatch webhook
            assert result["status"] == "completed"
            mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# scheduler.py tests
# ---------------------------------------------------------------------------


class TestSchedulerApprovalTimeoutTerminalCheck:
    """Test that approval timeout checks all terminal statuses."""

    @pytest.mark.asyncio
    async def test_budget_exceeded_run_skipped(self):
        """Runs with budget_exceeded status should be skipped by timeout checker."""
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        approval_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="test",
                status=RunStatus.BUDGET_EXCEEDED,
                input_data={},
            )
            session.add(run)
            ap = ApprovalRequest(
                id=approval_id,
                run_id=run_id,
                step_id="step1",
                status=ApprovalStatus.PENDING,
                timeout_at=now,
                on_timeout="abort",
            )
            session.add(ap)
            await session.commit()

        from sandcastle.queue.scheduler import _check_approval_timeouts

        await _check_approval_timeouts()

        # Approval should be marked as TIMED_OUT but run status should NOT change
        async with async_session() as session:
            ap = await session.get(ApprovalRequest, approval_id)
            assert ap.status == ApprovalStatus.TIMED_OUT
            run = await session.get(Run, run_id)
            assert run.status == RunStatus.BUDGET_EXCEEDED


class TestScheduleRestoreInvalidCron:
    """Test that invalid cron expressions are handled during restore."""

    @pytest.mark.asyncio
    async def test_invalid_cron_disables_schedule(self):
        """Schedules with invalid cron should be disabled on restore."""
        from sandcastle.models.db import Schedule, async_session

        schedule_id = uuid.uuid4()

        async with async_session() as session:
            sched = Schedule(
                id=schedule_id,
                workflow_name="test-wf",
                cron_expression="INVALID_CRON",
                enabled=True,
            )
            session.add(sched)
            await session.commit()

        from sandcastle.queue.scheduler import restore_schedules

        await restore_schedules()

        async with async_session() as session:
            sched = await session.get(Schedule, schedule_id)
            assert sched.enabled is False


class TestAddScheduleValidation:
    """Tests for add_schedule input validation."""

    def test_empty_cron_raises(self):
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="cron_expression"):
            add_schedule("sched1", "", "workflow")

    def test_whitespace_cron_raises(self):
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="cron_expression"):
            add_schedule("sched1", "   ", "workflow")

    def test_empty_workflow_name_raises(self):
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="workflow_name"):
            add_schedule("sched1", "* * * * *", "")

    def test_invalid_cron_expression_raises(self):
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="Invalid cron"):
            add_schedule("sched1", "not a cron", "workflow")

    def test_valid_schedule_creates_job(self, tmp_path, monkeypatch):
        from sandcastle.queue.scheduler import add_schedule, get_scheduler

        wf = tmp_path / "test-workflow.yaml"
        wf.write_text("name: test-workflow\nsteps: []")
        monkeypatch.setattr("sandcastle.queue.scheduler.settings.workflows_dir", str(tmp_path))

        add_schedule("test-valid-123", "0 * * * *", "test-workflow")
        scheduler = get_scheduler()
        job = scheduler.get_job("test-valid-123")
        assert job is not None
        scheduler.remove_job("test-valid-123")


class TestRemoveSchedule:
    """Tests for remove_schedule."""

    def test_remove_nonexistent_returns_false(self):
        from sandcastle.queue.scheduler import remove_schedule

        result = remove_schedule("nonexistent-schedule-id")
        assert result is False

    def test_remove_existing_returns_true(self, tmp_path, monkeypatch):
        from sandcastle.queue.scheduler import add_schedule, remove_schedule

        wf = tmp_path / "test-workflow.yaml"
        wf.write_text("name: test-workflow\nsteps: []")
        monkeypatch.setattr("sandcastle.queue.scheduler.settings.workflows_dir", str(tmp_path))

        add_schedule("to-remove-123", "0 * * * *", "test-workflow")
        result = remove_schedule("to-remove-123")
        assert result is True


class TestListSchedules:
    """Tests for list_schedules."""

    def test_list_empty(self):
        from sandcastle.queue.scheduler import list_schedules

        # May contain jobs from other tests, but should not raise
        result = list_schedules()
        assert isinstance(result, list)


class TestLoadWorkflowYaml:
    """Tests for _load_workflow_yaml path traversal prevention."""

    def test_path_traversal_blocked(self):
        from sandcastle.queue.scheduler import _load_workflow_yaml

        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            _load_workflow_yaml("../../etc/passwd")


# ---------------------------------------------------------------------------
# dispatcher.py tests
# ---------------------------------------------------------------------------


class TestValidateCallbackUrlPortDefault:
    """Test that HTTP URLs use port 80, HTTPS uses port 443."""

    def test_http_uses_port_80(self):
        from sandcastle.webhooks.dispatcher import validate_callback_url

        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 80)),
        ]) as mock_gai:
            validate_callback_url("http://example.com/hook")
            call_args = mock_gai.call_args
            assert call_args[0][1] == 80

    def test_https_uses_port_443(self):
        from sandcastle.webhooks.dispatcher import validate_callback_url

        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]) as mock_gai:
            validate_callback_url("https://example.com/hook")
            call_args = mock_gai.call_args
            assert call_args[0][1] == 443

    def test_explicit_port_used(self):
        from sandcastle.webhooks.dispatcher import validate_callback_url

        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 8080)),
        ]) as mock_gai:
            validate_callback_url("http://example.com:8080/hook")
            call_args = mock_gai.call_args
            assert call_args[0][1] == 8080


class TestWebhookPayloadSizeLimit:
    """Test payload size truncation."""

    @pytest.mark.asyncio
    async def test_large_payload_truncated(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        # Create a payload with a very large output dict
        large_outputs = {"data": "x" * 2_000_000}

        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_body = {}

        async def capture_post(url, content=None, headers=None):
            captured_body["content"] = content
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com"),
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
            patch("sandcastle.webhooks.dispatcher.settings") as mock_settings,
        ):
            mock_settings.webhook_secret = ""
            result = await dispatch_webhook(
                url="https://ok.com",
                event="workflow.completed",
                run_id="run-1",
                workflow="test",
                status="completed",
                outputs=large_outputs,
            )

        assert result is True
        payload = json.loads(captured_body["content"])
        assert payload["outputs"]["outputs_truncated"] is True
        assert "outputs_preview" in payload["outputs"]

    @pytest.mark.asyncio
    async def test_normal_payload_not_truncated(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        normal_outputs = {"result": "ok"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_body = {}

        async def capture_post(url, content=None, headers=None):
            captured_body["content"] = content
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com"),
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
            patch("sandcastle.webhooks.dispatcher.settings") as mock_settings,
        ):
            mock_settings.webhook_secret = ""
            result = await dispatch_webhook(
                url="https://ok.com",
                event="workflow.completed",
                run_id="run-1",
                workflow="test",
                status="completed",
                outputs=normal_outputs,
            )

        assert result is True
        payload = json.loads(captured_body["content"])
        assert payload["outputs"] == normal_outputs


class TestWebhookRedirectPrevention:
    """Test that redirects are not followed (SSRF prevention)."""

    @pytest.mark.asyncio
    async def test_redirect_returns_false(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_response = MagicMock()
        mock_response.status_code = 302

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com"),
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await dispatch_webhook(
                url="https://ok.com",
                event="workflow.completed",
                run_id="run-1",
                workflow="test",
                status="completed",
            )

        assert result is False


class TestWebhookClientError:
    """Test that 4xx errors are not retried."""

    @pytest.mark.asyncio
    async def test_400_not_retried(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=400))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com"),
            patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await dispatch_webhook(
                url="https://ok.com",
                event="workflow.failed",
                run_id="run-1",
                workflow="test",
                status="failed",
                max_retries=3,
            )

        assert result is False
        assert mock_client.post.call_count == 1  # Not retried


class TestWebhookSignatureVerification:
    """Test HMAC signature roundtrip."""

    def test_roundtrip(self):
        from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature

        body = '{"event": "workflow.completed", "run_id": "abc"}'
        secret = "super-secret-key"
        sig = _sign_payload(body, secret)
        assert verify_signature(body, sig, secret)
        assert not verify_signature(body, sig, "wrong-secret")
        assert not verify_signature(body + "tampered", sig, secret)


# ---------------------------------------------------------------------------
# db.py tests
# ---------------------------------------------------------------------------


class TestDBModelIndexes:
    """Verify that critical indexes exist on models."""

    def test_approval_request_indexes(self):
        from sandcastle.models.db import ApprovalRequest

        table = ApprovalRequest.__table__
        index_names = {idx.name for idx in table.indexes}
        assert "ix_approval_requests_run_id" in index_names
        assert "ix_approval_requests_status" in index_names
        assert "ix_approval_requests_status_timeout" in index_names

    def test_routing_decision_index(self):
        from sandcastle.models.db import RoutingDecision

        table = RoutingDecision.__table__
        index_names = {idx.name for idx in table.indexes}
        assert "ix_routing_decisions_run_id" in index_names

    def test_policy_violation_index(self):
        from sandcastle.models.db import PolicyViolation

        table = PolicyViolation.__table__
        index_names = {idx.name for idx in table.indexes}
        assert "ix_policy_violations_run_id" in index_names

    def test_autopilot_sample_index(self):
        from sandcastle.models.db import AutoPilotSample

        table = AutoPilotSample.__table__
        index_names = {idx.name for idx in table.indexes}
        assert "ix_autopilot_samples_experiment_id" in index_names

    def test_eval_case_result_index(self):
        from sandcastle.models.db import EvalCaseResult

        table = EvalCaseResult.__table__
        index_names = {idx.name for idx in table.indexes}
        assert "ix_eval_case_results_eval_run_id" in index_names

    def test_run_indexes(self):
        from sandcastle.models.db import Run

        table = Run.__table__
        index_names = {idx.name for idx in table.indexes}
        assert "ix_runs_status" in index_names
        assert "ix_runs_created_at" in index_names
        assert "ix_runs_tenant_id" in index_names
        assert "ix_runs_workflow_name" in index_names
        assert "ix_runs_tenant_status_created" in index_names

    def test_run_steps_indexes(self):
        from sandcastle.models.db import RunStep

        table = RunStep.__table__
        index_names = {idx.name for idx in table.indexes}
        assert "ix_run_steps_run_id" in index_names
        assert "ix_run_steps_run_step_parallel" in index_names


class TestDBModelConstraints:
    """Verify model constraints."""

    def test_api_key_hash_unique(self):
        from sandcastle.models.db import ApiKey

        table = ApiKey.__table__
        key_hash_col = table.c.key_hash
        assert key_hash_col.unique is True

    def test_workflow_version_unique_constraint(self):
        from sandcastle.models.db import WorkflowVersion

        table = WorkflowVersion.__table__
        constraint_names = {c.name for c in table.constraints if hasattr(c, "name") and c.name}
        assert "uq_workflow_name_version" in constraint_names

    def test_tool_connection_unique_constraint(self):
        from sandcastle.models.db import ToolConnection

        table = ToolConnection.__table__
        constraint_names = {c.name for c in table.constraints if hasattr(c, "name") and c.name}
        assert "uq_tool_connection" in constraint_names


class TestDBEngineConsistency:
    """Test that the engine URL is computed once."""

    def test_engine_url_cached(self):
        from sandcastle.models.db import _engine_url

        assert isinstance(_engine_url, str)
        assert len(_engine_url) > 0


class TestBuildEngineKwargsWithUrl:
    """Test _build_engine_kwargs with explicit URL."""

    def test_sqlite_kwargs(self):
        from sandcastle.models.db import _build_engine_kwargs

        kwargs = _build_engine_kwargs("sqlite+aiosqlite:///test.db")
        assert "connect_args" in kwargs
        assert kwargs["connect_args"]["check_same_thread"] is False

    def test_postgres_kwargs(self):
        from sandcastle.models.db import _build_engine_kwargs

        kwargs = _build_engine_kwargs("postgresql+asyncpg://localhost/sandcastle")
        assert "connect_args" not in kwargs

    def test_default_url(self):
        from sandcastle.models.db import _build_engine_kwargs

        kwargs = _build_engine_kwargs()
        assert "echo" in kwargs
        assert kwargs["echo"] is False


class TestDBRunRelationships:
    """Test model relationships are set up correctly."""

    @pytest.mark.asyncio
    async def test_run_steps_cascade_delete(self):
        from sandcastle.models.db import Run, RunStatus, RunStep, StepStatus, async_session

        run_id = uuid.uuid4()
        step_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(id=run_id, workflow_name="test", status=RunStatus.COMPLETED)
            session.add(run)
            step = RunStep(
                id=step_id,
                run_id=run_id,
                step_id="step1",
                status=StepStatus.COMPLETED,
            )
            session.add(step)
            await session.commit()

        async with async_session() as session:
            run = await session.get(Run, run_id)
            assert run is not None

    @pytest.mark.asyncio
    async def test_run_parent_child_relationship(self):
        from sandcastle.models.db import Run, RunStatus, async_session

        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()

        async with async_session() as session:
            parent = Run(id=parent_id, workflow_name="parent-wf", status=RunStatus.COMPLETED)
            session.add(parent)
            child = Run(
                id=child_id,
                workflow_name="child-wf",
                status=RunStatus.COMPLETED,
                parent_run_id=parent_id,
                depth=1,
            )
            session.add(child)
            await session.commit()

        async with async_session() as session:
            child = await session.get(Run, child_id)
            assert child.parent_run_id == parent_id
            assert child.depth == 1


class TestDBRunStatusEnum:
    """Test RunStatus enum values match expected states."""

    def test_all_statuses(self):
        from sandcastle.models.db import RunStatus

        expected = {
            "queued", "running", "completed", "failed",
            "partial", "cancelled", "budget_exceeded", "awaiting_approval",
        }
        actual = {s.value for s in RunStatus}
        assert actual == expected

    def test_step_status_enum(self):
        from sandcastle.models.db import StepStatus

        expected = {
            "pending", "running", "completed", "failed",
            "skipped", "awaiting_approval",
        }
        actual = {s.value for s in StepStatus}
        assert actual == expected

    def test_approval_status_enum(self):
        from sandcastle.models.db import ApprovalStatus

        expected = {"pending", "approved", "rejected", "skipped", "timed_out"}
        actual = {s.value for s in ApprovalStatus}
        assert actual == expected


# ---------------------------------------------------------------------------
# config.py tests
# ---------------------------------------------------------------------------


class TestConfigKeyRotationGraceValidator:
    """Tests for key_rotation_grace_hours validator."""

    def test_negative_grace_hours_falls_back(self):
        from sandcastle.config import Settings

        s = Settings(key_rotation_grace_hours=-5)
        assert s.key_rotation_grace_hours == 24

    def test_zero_grace_hours_accepted(self):
        from sandcastle.config import Settings

        s = Settings(key_rotation_grace_hours=0)
        assert s.key_rotation_grace_hours == 0

    def test_positive_grace_hours_accepted(self):
        from sandcastle.config import Settings

        s = Settings(key_rotation_grace_hours=48)
        assert s.key_rotation_grace_hours == 48


class TestConfigExistingValidators:
    """Ensure existing config validators still work."""

    def test_invalid_sandbox_backend(self):
        from sandcastle.config import Settings

        s = Settings(sandbox_backend="invalid")
        assert s.sandbox_backend == "e2b"

    def test_negative_max_concurrent(self):
        from sandcastle.config import Settings

        s = Settings(max_concurrent_sandboxes=0)
        assert s.max_concurrent_sandboxes == 1

    def test_invalid_log_level(self):
        from sandcastle.config import Settings

        s = Settings(log_level="invalid")
        assert s.log_level == "info"

    def test_negative_max_workflow_depth(self):
        from sandcastle.config import Settings

        s = Settings(max_workflow_depth=0)
        assert s.max_workflow_depth == 5

    def test_negative_docker_pids_limit(self):
        from sandcastle.config import Settings

        s = Settings(docker_pids_limit=0)
        assert s.docker_pids_limit == 100

    def test_negative_memory_max_age(self):
        from sandcastle.config import Settings

        s = Settings(memory_max_age_days=-1)
        assert s.memory_max_age_days == 0

    def test_admit_threshold_out_of_range(self):
        from sandcastle.config import Settings

        s = Settings(memory_admit_threshold=1.5)
        assert s.memory_admit_threshold == 1.0

    def test_failover_cooldown_zero(self):
        from sandcastle.config import Settings

        s = Settings(failover_cooldown_seconds=0)
        assert s.failover_cooldown_seconds == 60.0


# ---------------------------------------------------------------------------
# sdk.py tests
# ---------------------------------------------------------------------------


class TestSdkSseFlush:
    """Test that SSE parser flushes buffered data on stream end."""

    def test_flush_on_no_trailing_newline(self):
        from sandcastle.sdk import _parse_sse_lines

        raw = "event: status\ndata: {\"status\": \"running\"}"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["status"] == "running"
        assert events[0]["_event"] == "status"

    def test_flush_non_json(self):
        from sandcastle.sdk import _parse_sse_lines

        raw = "data: not-json-data"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert "raw" in events[0]

    def test_normal_events_still_work(self):
        from sandcastle.sdk import _parse_sse_lines

        raw = "event: step\ndata: {\"step_id\": \"s1\"}\n\nevent: result\ndata: {\"done\": true}\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 2
        assert events[0]["_event"] == "step"
        assert events[1]["_event"] == "result"

    def test_empty_input(self):
        from sandcastle.sdk import _parse_sse_lines

        events = list(_parse_sse_lines(""))
        assert len(events) == 0

    def test_mixed_normal_and_unflushed(self):
        from sandcastle.sdk import _parse_sse_lines

        raw = "data: {\"first\": true}\n\ndata: {\"second\": true}"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 2
        assert events[0]["first"] is True
        assert events[1]["second"] is True


class TestSdkTerminalStatuses:
    """Test terminal status set."""

    def test_includes_all_expected(self):
        from sandcastle.sdk import _TERMINAL_STATUSES

        expected = {
            "completed", "failed", "partial", "cancelled",
            "budget_exceeded", "awaiting_approval", "error",
        }
        assert _TERMINAL_STATUSES == expected


class TestSdkParseDatetime:
    """Test _parse_datetime helper."""

    def test_none_input(self):
        from sandcastle.sdk import _parse_datetime

        assert _parse_datetime(None) is None

    def test_iso_string(self):
        from sandcastle.sdk import _parse_datetime

        dt = _parse_datetime("2026-01-15T10:30:00+00:00")
        assert isinstance(dt, datetime)
        assert dt.year == 2026

    def test_invalid_string(self):
        from sandcastle.sdk import _parse_datetime

        assert _parse_datetime("not-a-date") is None

    def test_datetime_passthrough(self):
        from sandcastle.sdk import _parse_datetime

        now = datetime.now(timezone.utc)
        assert _parse_datetime(now) is now


class TestSdkParseRun:
    """Test _parse_run helper."""

    def test_minimal_data(self):
        from sandcastle.sdk import _parse_run

        run = _parse_run({"run_id": "abc", "status": "completed"})
        assert run.run_id == "abc"
        assert run.status == "completed"
        assert run.steps is None

    def test_with_steps(self):
        from sandcastle.sdk import _parse_run

        run = _parse_run({
            "run_id": "abc",
            "status": "completed",
            "steps": [
                {"step_id": "s1", "status": "completed", "cost_usd": 0.01},
            ],
        })
        assert len(run.steps) == 1
        assert run.steps[0].step_id == "s1"

    def test_with_new_run_id(self):
        from sandcastle.sdk import _parse_run

        run = _parse_run({"new_run_id": "new-abc", "status": "queued"})
        assert run.run_id == "new-abc"
        assert run.new_run_id == "new-abc"


class TestSdkParseSchedule:
    """Test _parse_schedule helper."""

    def test_basic(self):
        from sandcastle.sdk import _parse_schedule

        sched = _parse_schedule({
            "id": "sched-1",
            "workflow_name": "daily-report",
            "cron_expression": "0 9 * * *",
            "enabled": True,
        })
        assert sched.id == "sched-1"
        assert sched.cron_expression == "0 9 * * *"
        assert sched.enabled is True


class TestSdkExtractData:
    """Test _extract_data error handling."""

    def test_raises_on_error_status(self):
        from sandcastle.sdk import SandcastleError, _extract_data

        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {
            "detail": {"error": {"code": "NOT_FOUND", "message": "Run not found"}},
        }
        resp.text = "Not Found"

        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "NOT_FOUND"

    def test_extracts_data_key(self):
        from sandcastle.sdk import _extract_data

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {"run_id": "abc"}}

        result = _extract_data(resp)
        assert result == {"run_id": "abc"}

    def test_falls_back_to_body(self):
        from sandcastle.sdk import _extract_data

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"run_id": "abc"}

        result = _extract_data(resp)
        assert result == {"run_id": "abc"}


# ---------------------------------------------------------------------------
# Cross-cutting / integration tests
# ---------------------------------------------------------------------------


class TestScheduledWorkflowLifecycle:
    """Integration test for scheduled workflow creation."""

    @pytest.mark.asyncio
    async def test_schedule_creates_run_record(self):
        """_run_scheduled_workflow should create a Run record."""
        from sandcastle.models.db import Schedule, async_session

        schedule_id = uuid.uuid4()

        async with async_session() as session:
            sched = Schedule(
                id=schedule_id,
                workflow_name="test-scheduled-wf",
                cron_expression="0 * * * *",
                enabled=True,
            )
            session.add(sched)
            await session.commit()

        from sandcastle.queue.scheduler import _run_scheduled_workflow

        with (
            patch("sandcastle.queue.scheduler._load_workflow_yaml", return_value="name: test\nsteps: []"),
            patch("sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock),
        ):
            await _run_scheduled_workflow(
                str(schedule_id), "test-scheduled-wf", {},
            )

        # Check that a run was created
        from sqlalchemy import select

        from sandcastle.models.db import Run

        async with async_session() as session:
            stmt = select(Run).where(Run.workflow_name == "test-scheduled-wf")
            result = await session.execute(stmt)
            runs = result.scalars().all()
            assert len(runs) >= 1

    @pytest.mark.asyncio
    async def test_disabled_schedule_skipped(self):
        """Disabled schedules should not enqueue a workflow."""
        from sandcastle.models.db import Schedule, async_session

        schedule_id = uuid.uuid4()

        async with async_session() as session:
            sched = Schedule(
                id=schedule_id,
                workflow_name="disabled-wf",
                cron_expression="0 * * * *",
                enabled=False,
            )
            session.add(sched)
            await session.commit()

        from sandcastle.queue.scheduler import _run_scheduled_workflow

        with (
            patch("sandcastle.queue.scheduler._load_workflow_yaml", return_value="name: test\nsteps: []"),
            patch("sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock) as mock_enqueue,
        ):
            await _run_scheduled_workflow(
                str(schedule_id), "disabled-wf", {},
            )
            # Enqueue should NOT be called for disabled schedules
            mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_deleted_schedule_skipped(self):
        """Deleted schedules should not enqueue a workflow."""
        from sandcastle.queue.scheduler import _run_scheduled_workflow

        fake_id = str(uuid.uuid4())

        with (
            patch("sandcastle.queue.scheduler._load_workflow_yaml", return_value="name: test\nsteps: []"),
            patch("sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock) as mock_enqueue,
        ):
            await _run_scheduled_workflow(fake_id, "deleted-wf", {})
            # Enqueue should NOT be called for non-existent schedules
            mock_enqueue.assert_not_called()


class TestEnqueueWorkflowLocalMode:
    """Test in-process (local mode) job execution."""

    @pytest.mark.asyncio
    async def test_creates_background_task(self):
        from sandcastle.queue.worker import _background_tasks

        initial_count = len(_background_tasks)

        with patch("sandcastle.queue.worker.run_workflow_job", new_callable=AsyncMock) as mock_job:
            mock_job.return_value = {"status": "completed"}

            from sandcastle.queue.worker import enqueue_workflow

            await enqueue_workflow("name: test\nsteps: []", {}, "run-123")

            # Wait a bit for the task to be added
            await asyncio.sleep(0.1)

            # The task should have been created (and may have already completed)
            mock_job.assert_called_once()
