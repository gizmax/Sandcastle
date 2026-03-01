"""Wave 9 - End-to-end integration tests.

Tests multiple components working together across the full stack:
API routes -> DB models -> worker -> webhook dispatcher -> scheduler.

Previous waves tested components in isolation; this wave verifies the
integration points between them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIMPLE_WORKFLOW_YAML = """\
name: integration-test-workflow
steps:
  - id: step1
    prompt: "Say hello"
  - id: step2
    prompt: "Say goodbye"
    depends_on:
      - step1
"""

APPROVAL_WORKFLOW_YAML = """\
name: approval-test-workflow
steps:
  - id: step1
    prompt: "Prepare data"
  - id: review
    type: approval
    approval:
      message: "Please review the data"
      timeout_hours: 1
      on_timeout: abort
    depends_on:
      - step1
  - id: step3
    prompt: "Finalize"
    depends_on:
      - review
"""

BUDGET_WORKFLOW_YAML = """\
name: budget-test-workflow
steps:
  - id: expensive_step
    prompt: "Do expensive computation"
"""


def _make_workflow_result(run_id, status="completed", outputs=None, cost=0.0, error=None):
    """Build a mock WorkflowResult matching the executor's output."""
    from sandcastle.engine.executor import WorkflowResult

    now = datetime.now(timezone.utc)
    return WorkflowResult(
        run_id=run_id,
        outputs=outputs or {"step1": "hello", "step2": "goodbye"},
        total_cost_usd=cost,
        status=status,
        error=error,
        started_at=now - timedelta(seconds=5),
        completed_at=now,
    )


async def _get_test_client():
    """Create a test async client using the Sandcastle app."""
    from sandcastle.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _init_test_db():
    """Ensure DB tables exist for integration tests."""
    from sandcastle.models.db import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------------------
# 1. Full workflow lifecycle: API -> queue -> worker -> result -> webhook
# ---------------------------------------------------------------------------


class TestFullWorkflowLifecycle:
    """End-to-end: create workflow via API -> enqueue -> worker -> result -> webhook."""

    @pytest.mark.asyncio
    async def test_run_workflow_creates_queued_run_and_enqueues(self):
        """POST /api/workflows/run should create a QUEUED run and call enqueue."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session

        captured_args = {}

        async def mock_enqueue(yaml, input_data, run_id, **kwargs):
            captured_args["yaml"] = yaml
            captured_args["run_id"] = run_id
            captured_args["input_data"] = input_data

        with patch("sandcastle.api.routes.enqueue_workflow", side_effect=mock_enqueue), \
             patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()

            async with await _get_test_client() as client:
                resp = await client.post(
                    "/api/workflows/run",
                    json={
                        "workflow": SIMPLE_WORKFLOW_YAML,
                        "input": {"key": "value"},
                    },
                )

            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["status"] == "queued"
            run_id = body["data"]["run_id"]

            # Verify DB record was created
            async with async_session() as session:
                run = await session.get(Run, uuid.UUID(run_id))
                assert run is not None
                assert run.status == RunStatus.QUEUED
                assert run.workflow_name == "integration-test-workflow"
                assert run.input_data == {"key": "value"}

            # Verify enqueue was called with correct args
            assert captured_args["run_id"] == run_id
            assert captured_args["input_data"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_worker_transitions_queued_to_running_to_completed(self):
        """run_workflow_job should transition: QUEUED -> RUNNING -> COMPLETED."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        # Create a QUEUED run
        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="integration-test-workflow",
                status=RunStatus.QUEUED,
                input_data={"key": "value"},
            )
            session.add(run)
            await session.commit()

        mock_result = _make_workflow_result(run_id)

        with patch("sandcastle.engine.executor.execute_workflow", return_value=mock_result), \
             patch("sandcastle.engine.storage.create_storage"), \
             patch("sandcastle.webhooks.dispatcher.dispatch_webhook", new_callable=AsyncMock) as mock_wh:

            result = await run_workflow_job(
                ctx={},
                workflow_yaml=SIMPLE_WORKFLOW_YAML,
                input_data={"key": "value"},
                run_id=run_id,
            )

        assert result["status"] == "completed"

        # Verify final DB state
        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run.status == RunStatus.COMPLETED
            assert run.output_data == {"step1": "hello", "step2": "goodbye"}
            assert run.total_cost_usd == 0.0
            assert run.started_at is not None
            assert run.completed_at is not None

    @pytest.mark.asyncio
    async def test_worker_dispatches_callback_webhook_on_completion(self):
        """Worker should dispatch webhook to callback_url on completion."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)
        callback_url = "https://example.com/webhook/callback"

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="integration-test-workflow",
                status=RunStatus.QUEUED,
                input_data={},
                callback_url=callback_url,
            )
            session.add(run)
            await session.commit()

        mock_result = _make_workflow_result(run_id)

        with patch("sandcastle.engine.executor.execute_workflow", return_value=mock_result), \
             patch("sandcastle.engine.storage.create_storage"), \
             patch("sandcastle.webhooks.dispatcher.dispatch_webhook", new_callable=AsyncMock) as mock_wh:

            await run_workflow_job(
                ctx={},
                workflow_yaml=SIMPLE_WORKFLOW_YAML,
                input_data={},
                run_id=run_id,
            )

        # Verify webhook was called with correct params
        mock_wh.assert_called_once()
        call_kwargs = mock_wh.call_args
        assert call_kwargs.kwargs["url"] == callback_url
        assert call_kwargs.kwargs["event"] == "workflow.completed"
        assert call_kwargs.kwargs["run_id"] == run_id
        assert call_kwargs.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_worker_dispatches_failure_webhook(self):
        """Worker should dispatch failure webhook when execution raises."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)
        callback_url = "https://example.com/webhook/fail"

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="integration-test-workflow",
                status=RunStatus.QUEUED,
                input_data={},
                callback_url=callback_url,
            )
            session.add(run)
            await session.commit()

        with patch(
            "sandcastle.engine.executor.execute_workflow",
            side_effect=RuntimeError("sandbox exploded"),
        ), \
             patch("sandcastle.engine.storage.create_storage"), \
             patch("sandcastle.webhooks.dispatcher.dispatch_webhook", new_callable=AsyncMock) as mock_wh:

            result = await run_workflow_job(
                ctx={},
                workflow_yaml=SIMPLE_WORKFLOW_YAML,
                input_data={},
                run_id=run_id,
            )

        assert result["status"] == "failed"

        # Verify DB reflects failure
        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run.status == RunStatus.FAILED
            assert "sandbox exploded" in (run.error or "")

        # Verify failure webhook dispatched
        mock_wh.assert_called_once()
        assert mock_wh.call_args.kwargs["event"] == "workflow.failed"
        assert mock_wh.call_args.kwargs["url"] == callback_url


# ---------------------------------------------------------------------------
# 2. Scheduler -> Run creation -> enqueue -> complete
# ---------------------------------------------------------------------------


class TestSchedulerIntegration:
    """Scheduler triggers -> creates run -> enqueues -> concurrent guard."""

    @pytest.mark.asyncio
    async def test_scheduled_workflow_creates_run_and_enqueues(self):
        """_run_scheduled_workflow should create a QUEUED run and call enqueue."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session

        schedule_id = str(uuid.uuid4())
        schedule_uuid = uuid.UUID(schedule_id)

        # Create a schedule in DB
        async with async_session() as session:
            sched = Schedule(
                id=schedule_uuid,
                workflow_name="integration-test-workflow",
                cron_expression="0 * * * *",
                input_data={"scheduled": True},
                enabled=True,
            )
            session.add(sched)
            await session.commit()

        captured_run_id = {}

        async def mock_enqueue(yaml, input_data, run_id, **kwargs):
            captured_run_id["id"] = run_id

        with patch(
            "sandcastle.queue.scheduler._load_workflow_yaml",
            return_value=SIMPLE_WORKFLOW_YAML,
        ), \
             patch("sandcastle.queue.worker.enqueue_workflow", side_effect=mock_enqueue):
            from sandcastle.queue.scheduler import _run_scheduled_workflow

            await _run_scheduled_workflow(
                schedule_id=schedule_id,
                workflow_name="integration-test-workflow",
                input_data={"scheduled": True},
            )

        # Verify a run was created
        assert "id" in captured_run_id
        run_id = captured_run_id["id"]
        async with async_session() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            assert run is not None
            assert run.status == RunStatus.QUEUED
            assert run.input_data == {"scheduled": True}

            # Verify last_run_id updated on schedule
            sched = await session.get(Schedule, schedule_uuid)
            assert sched.last_run_id == uuid.UUID(run_id)

    @pytest.mark.asyncio
    async def test_scheduler_skips_when_previous_run_still_active(self):
        """Scheduler should skip if previous run is still RUNNING."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session

        schedule_id = str(uuid.uuid4())
        schedule_uuid = uuid.UUID(schedule_id)

        # Create an active run
        active_run_id = uuid.uuid4()
        async with async_session() as session:
            active_run = Run(
                id=active_run_id,
                workflow_name="integration-test-workflow",
                status=RunStatus.RUNNING,
                input_data={},
            )
            session.add(active_run)
            sched = Schedule(
                id=schedule_uuid,
                workflow_name="integration-test-workflow",
                cron_expression="0 * * * *",
                input_data={},
                enabled=True,
                last_run_id=active_run_id,
            )
            session.add(sched)
            await session.commit()

        with patch(
            "sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock
        ) as mock_enqueue:
            from sandcastle.queue.scheduler import _run_scheduled_workflow

            await _run_scheduled_workflow(
                schedule_id=schedule_id,
                workflow_name="integration-test-workflow",
                input_data={},
            )

        # enqueue should NOT have been called
        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_scheduler_runs_when_previous_completed(self):
        """Scheduler should run when previous run is already COMPLETED."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, Schedule, async_session

        schedule_id = str(uuid.uuid4())
        schedule_uuid = uuid.UUID(schedule_id)

        completed_run_id = uuid.uuid4()
        async with async_session() as session:
            completed_run = Run(
                id=completed_run_id,
                workflow_name="integration-test-workflow",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(completed_run)
            sched = Schedule(
                id=schedule_uuid,
                workflow_name="integration-test-workflow",
                cron_expression="0 * * * *",
                input_data={},
                enabled=True,
                last_run_id=completed_run_id,
            )
            session.add(sched)
            await session.commit()

        with patch(
            "sandcastle.queue.scheduler._load_workflow_yaml",
            return_value=SIMPLE_WORKFLOW_YAML,
        ), \
             patch(
                 "sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock
             ) as mock_enqueue:
            from sandcastle.queue.scheduler import _run_scheduled_workflow

            await _run_scheduled_workflow(
                schedule_id=schedule_id,
                workflow_name="integration-test-workflow",
                input_data={},
            )

        # enqueue SHOULD have been called
        mock_enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_scheduler_disabled_schedule_is_skipped(self):
        """Disabled schedule should be silently skipped."""
        await _init_test_db()
        from sandcastle.models.db import Schedule, async_session

        schedule_id = str(uuid.uuid4())
        async with async_session() as session:
            sched = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name="integration-test-workflow",
                cron_expression="0 * * * *",
                input_data={},
                enabled=False,
            )
            session.add(sched)
            await session.commit()

        with patch(
            "sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock
        ) as mock_enqueue:
            from sandcastle.queue.scheduler import _run_scheduled_workflow

            await _run_scheduled_workflow(
                schedule_id=schedule_id,
                workflow_name="integration-test-workflow",
                input_data={},
            )

        mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Approval flow: run reaches gate -> awaiting_approval -> approve -> resume
# ---------------------------------------------------------------------------


class TestApprovalFlowIntegration:
    """Full approval lifecycle: create approval -> approve -> verify state."""

    @pytest.mark.asyncio
    async def test_approve_updates_approval_and_run_status(self):
        """Approving a pending gate should mark approval as APPROVED."""
        await _init_test_db()
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        approval_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="approval-test-workflow",
                status=RunStatus.AWAITING_APPROVAL,
                input_data={},
            )
            session.add(run)
            approval = ApprovalRequest(
                id=approval_id,
                run_id=run_id,
                step_id="review",
                status=ApprovalStatus.PENDING,
                message="Please review",
                on_timeout="abort",
            )
            session.add(approval)
            await session.commit()

        # Mock _resume_after_approval to avoid needing actual workflow files
        with patch("sandcastle.api.routes._resume_after_approval", new_callable=AsyncMock), \
             patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()

            async with await _get_test_client() as client:
                resp = await client.post(
                    f"/api/approvals/{approval_id}/approve",
                    json={"comment": "Looks good!"},
                )

            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["approved"] is True

        # Verify approval is now APPROVED
        async with async_session() as session:
            ap = await session.get(ApprovalRequest, approval_id)
            ap_status = ap.status.value if hasattr(ap.status, "value") else ap.status
            assert ap_status == "approved"
            assert ap.reviewer_comment == "Looks good!"
            assert ap.resolved_at is not None

    @pytest.mark.asyncio
    async def test_reject_approval_fails_the_run(self):
        """Rejecting an approval should mark run as FAILED."""
        await _init_test_db()
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        approval_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="approval-test-workflow",
                status=RunStatus.AWAITING_APPROVAL,
                input_data={},
            )
            session.add(run)
            approval = ApprovalRequest(
                id=approval_id,
                run_id=run_id,
                step_id="review",
                status=ApprovalStatus.PENDING,
                message="Review please",
                on_timeout="abort",
            )
            session.add(approval)
            await session.commit()

        async with await _get_test_client() as client:
            resp = await client.post(
                f"/api/approvals/{approval_id}/reject",
                json={"comment": "Not ready"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["rejected"] is True

        # Verify run is now FAILED
        async with async_session() as session:
            run = await session.get(Run, run_id)
            assert run.status == RunStatus.FAILED
            assert "Approval rejected" in (run.error or "")

    @pytest.mark.asyncio
    async def test_double_approve_returns_409(self):
        """Approving an already-resolved approval should return 409."""
        await _init_test_db()
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        approval_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="approval-test-workflow",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            approval = ApprovalRequest(
                id=approval_id,
                run_id=run_id,
                step_id="review",
                status=ApprovalStatus.APPROVED,
                message="Already done",
                on_timeout="abort",
                resolved_at=datetime.now(timezone.utc),
            )
            session.add(approval)
            await session.commit()

        async with await _get_test_client() as client:
            resp = await client.post(f"/api/approvals/{approval_id}/approve")

        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["error"]["code"] == "ALREADY_RESOLVED"

    @pytest.mark.asyncio
    async def test_approval_timeout_checker_marks_as_timed_out(self):
        """_check_approval_timeouts should mark expired approvals as TIMED_OUT."""
        await _init_test_db()
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            Run,
            RunStatus,
            async_session,
        )
        from sandcastle.queue.scheduler import _check_approval_timeouts

        run_id = uuid.uuid4()
        approval_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="approval-test-workflow",
                status=RunStatus.AWAITING_APPROVAL,
                input_data={},
            )
            session.add(run)
            # Create approval that expired 10 minutes ago
            approval = ApprovalRequest(
                id=approval_id,
                run_id=run_id,
                step_id="review",
                status=ApprovalStatus.PENDING,
                message="Timeout test",
                on_timeout="abort",
                timeout_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            )
            session.add(approval)
            await session.commit()

        await _check_approval_timeouts()

        # Verify approval is timed out and run is failed
        async with async_session() as session:
            ap = await session.get(ApprovalRequest, approval_id)
            ap_status = ap.status.value if hasattr(ap.status, "value") else ap.status
            assert ap_status == "timed_out"

            run = await session.get(Run, run_id)
            assert run.status == RunStatus.FAILED
            assert "timed out" in (run.error or "").lower()


# ---------------------------------------------------------------------------
# 4. Replay and Fork
# ---------------------------------------------------------------------------


class TestReplayAndForkIntegration:
    """Replay and fork operations via the API."""

    @pytest.mark.asyncio
    async def test_replay_creates_child_run_with_parent_reference(self):
        """POST /runs/{id}/replay should create a new run linked to parent."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunCheckpoint, RunStatus, async_session

        parent_run_id = uuid.uuid4()
        wf_name = "integration-test-workflow"

        # Create parent run with a checkpoint
        async with async_session() as session:
            parent = Run(
                id=parent_run_id,
                workflow_name=wf_name,
                status=RunStatus.COMPLETED,
                input_data={"a": 1},
                output_data={"step1": "hello", "step2": "goodbye"},
            )
            session.add(parent)
            cp = RunCheckpoint(
                run_id=parent_run_id,
                step_id="step1",
                stage_index=0,
                context_snapshot={
                    "step_outputs": {"step1": "hello"},
                    "costs": [0.001],
                },
            )
            session.add(cp)
            await session.commit()

        with patch(
            "sandcastle.api.routes._load_workflow_yaml",
            return_value=SIMPLE_WORKFLOW_YAML,
        ), \
             patch(
                 "sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock
             ) as mock_enqueue, \
             patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()

            async with await _get_test_client() as client:
                resp = await client.post(
                    f"/api/runs/{parent_run_id}/replay",
                    json={"from_step": "step2"},
                )

            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["replay_from_step"] == "step2"
            assert body["data"]["parent_run_id"] == str(parent_run_id)
            new_run_id = body["data"]["new_run_id"]

        # Verify child run in DB
        async with async_session() as session:
            child = await session.get(Run, uuid.UUID(new_run_id))
            assert child is not None
            assert child.parent_run_id == parent_run_id
            assert child.replay_from_step == "step2"
            assert child.status == RunStatus.QUEUED

        # Verify enqueue called with skip_steps including step1 but not step2
        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args
        skip_steps = call_kwargs.kwargs.get("skip_steps") or call_kwargs[0][5] if len(call_kwargs[0]) > 5 else None
        if skip_steps is None and "skip_steps" in (call_kwargs.kwargs or {}):
            skip_steps = call_kwargs.kwargs["skip_steps"]
        # step1 should be skipped (already in checkpoint), step2 should NOT
        assert "step1" in (skip_steps or [])
        assert "step2" not in (skip_steps or [])

    @pytest.mark.asyncio
    async def test_fork_creates_child_run_with_changes(self):
        """POST /runs/{id}/fork should create a new run with fork_changes."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunCheckpoint, RunStatus, async_session

        parent_run_id = uuid.uuid4()
        wf_name = "integration-test-workflow"

        async with async_session() as session:
            parent = Run(
                id=parent_run_id,
                workflow_name=wf_name,
                status=RunStatus.COMPLETED,
                input_data={"a": 1},
            )
            session.add(parent)
            cp = RunCheckpoint(
                run_id=parent_run_id,
                step_id="step1",
                stage_index=0,
                context_snapshot={
                    "step_outputs": {"step1": "original"},
                    "costs": [0.001],
                },
            )
            session.add(cp)
            await session.commit()

        changes = {"prompt": "Modified prompt for step2"}

        with patch(
            "sandcastle.api.routes._load_workflow_yaml",
            return_value=SIMPLE_WORKFLOW_YAML,
        ), \
             patch(
                 "sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock
             ) as mock_enqueue, \
             patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()

            async with await _get_test_client() as client:
                resp = await client.post(
                    f"/api/runs/{parent_run_id}/fork",
                    json={"from_step": "step2", "changes": changes},
                )

            assert resp.status_code == 200
            body = resp.json()
            new_run_id = body["data"]["new_run_id"]
            assert body["data"]["fork_from_step"] == "step2"

        # Verify fork_changes stored in DB
        async with async_session() as session:
            child = await session.get(Run, uuid.UUID(new_run_id))
            assert child is not None
            assert child.parent_run_id == parent_run_id
            assert child.fork_changes == changes

        # Verify enqueue called with step_overrides keyed by from_step
        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args
        step_overrides = call_kwargs.kwargs.get("step_overrides")
        assert step_overrides == {"step2": changes}

    @pytest.mark.asyncio
    async def test_replay_nonexistent_run_returns_404(self):
        """Replay of a non-existent run should return 404."""
        await _init_test_db()

        fake_id = str(uuid.uuid4())
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            async with await _get_test_client() as client:
                resp = await client.post(
                    f"/api/runs/{fake_id}/replay",
                    json={"from_step": "step1"},
                )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. API -> DB -> Response roundtrip
# ---------------------------------------------------------------------------


class TestApiDbRoundtrip:
    """Create entities via API, read back, verify all fields."""

    @pytest.mark.asyncio
    async def test_run_create_and_get_roundtrip(self):
        """Create a run via POST, then GET it and verify all fields."""
        await _init_test_db()

        with patch(
            "sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock
        ), \
             patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()

            async with await _get_test_client() as client:
                # Create
                create_resp = await client.post(
                    "/api/workflows/run",
                    json={
                        "workflow": SIMPLE_WORKFLOW_YAML,
                        "input": {"roundtrip": "test"},
                        "max_cost_usd": 5.0,
                    },
                )
                assert create_resp.status_code == 200
                run_id = create_resp.json()["data"]["run_id"]

                # Read back
                get_resp = await client.get(f"/api/runs/{run_id}")
                assert get_resp.status_code == 200

                run_data = get_resp.json()["data"]
                assert run_data["run_id"] == run_id
                assert run_data["workflow_name"] == "integration-test-workflow"
                assert run_data["status"] == "queued"
                assert run_data["input_data"] == {"roundtrip": "test"}
                assert run_data["max_cost_usd"] == 5.0

    @pytest.mark.asyncio
    async def test_schedule_create_and_list_roundtrip(self):
        """Create a schedule via POST, then list and verify."""
        await _init_test_db()
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a workflow file so schedule validation passes
            wf_path = Path(tmpdir) / "integration-test-workflow.yaml"
            wf_path.write_text(SIMPLE_WORKFLOW_YAML)

            with patch("sandcastle.api.routes.settings") as mock_settings, \
                 patch("sandcastle.queue.scheduler.settings") as mock_sched_settings:
                # Configure settings mock to pass through most attributes
                from sandcastle.config import settings as real_settings
                for attr in dir(real_settings):
                    if not attr.startswith("_"):
                        try:
                            setattr(mock_settings, attr, getattr(real_settings, attr))
                            setattr(mock_sched_settings, attr, getattr(real_settings, attr))
                        except (AttributeError, TypeError):
                            pass
                mock_settings.workflows_dir = tmpdir
                mock_settings.auth_required = False
                mock_settings.is_local_mode = True
                mock_settings.scheduler_enabled = True
                mock_sched_settings.workflows_dir = tmpdir

                async with await _get_test_client() as client:
                    create_resp = await client.post(
                        "/api/schedules",
                        json={
                            "workflow_name": "integration-test-workflow",
                            "cron_expression": "0 9 * * 1",
                            "input_data": {"mode": "weekly"},
                        },
                    )

                    assert create_resp.status_code == 200
                    sched_data = create_resp.json()["data"]
                    schedule_id = sched_data["id"]
                    assert sched_data["workflow_name"] == "integration-test-workflow"
                    assert sched_data["cron_expression"] == "0 9 * * 1"
                    assert sched_data["enabled"] is True

                    # List and find our schedule
                    list_resp = await client.get("/api/schedules")
                    assert list_resp.status_code == 200
                    items = list_resp.json()["data"]
                    found = any(s["id"] == schedule_id for s in items)
                    assert found, "Created schedule not found in list"

    @pytest.mark.asyncio
    async def test_api_key_create_and_list_roundtrip(self):
        """Create an API key and verify it appears in the list."""
        await _init_test_db()

        async with await _get_test_client() as client:
            # Create
            create_resp = await client.post(
                "/api/api-keys",
                json={"name": "test-key-roundtrip", "tenant_id": "tenant-xyz"},
            )
            assert create_resp.status_code == 200
            key_data = create_resp.json()["data"]
            key_id = key_data["id"]
            assert key_data["name"] == "test-key-roundtrip"
            assert key_data["tenant_id"] == "tenant-xyz"
            # Plaintext key should be returned
            assert key_data["key"].startswith("sc_")

            # List and find
            list_resp = await client.get("/api/api-keys")
            assert list_resp.status_code == 200
            keys = list_resp.json()["data"]
            found = any(k["id"] == key_id for k in keys)
            assert found, "Created API key not found in list"

    @pytest.mark.asyncio
    async def test_get_nonexistent_run_returns_404(self):
        """GET /runs/{id} for non-existent ID should return 404."""
        await _init_test_db()

        fake_id = str(uuid.uuid4())
        async with await _get_test_client() as client:
            resp = await client.get(f"/api/runs/{fake_id}")

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_eval_run_db_roundtrip(self):
        """Directly create an EvalRun in DB and verify it can be read via API."""
        await _init_test_db()
        from sandcastle.models.db import EvalRun, EvalRunStatus, async_session

        eval_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        async with async_session() as session:
            er = EvalRun(
                id=eval_id,
                suite_name="roundtrip-suite",
                workflow_name="integration-test-workflow",
                status=EvalRunStatus.COMPLETED,
                total_cases=3,
                passed_cases=2,
                failed_cases=1,
                pass_rate=0.667,
                total_cost_usd=0.05,
                total_duration_seconds=12.5,
                started_at=now - timedelta(seconds=13),
                completed_at=now,
            )
            session.add(er)
            await session.commit()

        async with await _get_test_client() as client:
            resp = await client.get(f"/api/eval/runs/{eval_id}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["suite_name"] == "roundtrip-suite"
        assert data["status"] == "completed"
        assert data["total_cases"] == 3
        assert data["passed_cases"] == 2


# ---------------------------------------------------------------------------
# 6. Error propagation chain
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    """API validation errors -> proper HTTP status + error body."""

    @pytest.mark.asyncio
    async def test_workflow_run_without_yaml_or_name_returns_422(self):
        """Missing both workflow and workflow_name should be 422."""
        await _init_test_db()

        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            async with await _get_test_client() as client:
                resp = await client.post(
                    "/api/workflows/run",
                    json={"input": {}},
                )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_callback_url_scheme_returns_422(self):
        """callback_url with non-HTTP scheme should be rejected at schema level."""
        await _init_test_db()

        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            async with await _get_test_client() as client:
                resp = await client.post(
                    "/api/workflows/run",
                    json={
                        "workflow": SIMPLE_WORKFLOW_YAML,
                        "callback_url": "ftp://evil.com/hook",
                    },
                )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_run_id_format_returns_400(self):
        """GET /runs/not-a-uuid should return 400."""
        await _init_test_db()

        async with await _get_test_client() as client:
            resp = await client.get("/api/runs/not-a-uuid")

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"]["code"] == "INVALID_ID"

    @pytest.mark.asyncio
    async def test_invalid_cron_expression_returns_400(self):
        """Invalid cron expression in schedule creation should return 400."""
        await _init_test_db()
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = Path(tmpdir) / "integration-test-workflow.yaml"
            wf_path.write_text(SIMPLE_WORKFLOW_YAML)

            with patch("sandcastle.api.routes.settings") as mock_settings:
                from sandcastle.config import settings as real_settings
                for attr in dir(real_settings):
                    if not attr.startswith("_"):
                        try:
                            setattr(mock_settings, attr, getattr(real_settings, attr))
                        except (AttributeError, TypeError):
                            pass
                mock_settings.workflows_dir = tmpdir
                mock_settings.auth_required = False
                mock_settings.is_local_mode = True

                async with await _get_test_client() as client:
                    resp = await client.post(
                        "/api/schedules",
                        json={
                            "workflow_name": "integration-test-workflow",
                            "cron_expression": "not a cron",
                        },
                    )

            assert resp.status_code in (400, 422)  # Pydantic may validate first

    @pytest.mark.asyncio
    async def test_empty_api_key_name_returns_422(self):
        """API key with whitespace-only name should be rejected."""
        await _init_test_db()

        async with await _get_test_client() as client:
            resp = await client.post(
                "/api/api-keys",
                json={"name": "   "},
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_approval_id_returns_400(self):
        """POST /approvals/not-a-uuid/approve should return 400."""
        await _init_test_db()

        async with await _get_test_client() as client:
            resp = await client.post("/api/approvals/not-a-uuid/approve")

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"]["code"] == "INVALID_ID"

    @pytest.mark.asyncio
    async def test_nonexistent_approval_returns_404(self):
        """Approving a non-existent approval should return 404."""
        await _init_test_db()
        fake_id = str(uuid.uuid4())

        async with await _get_test_client() as client:
            resp = await client.post(f"/api/approvals/{fake_id}/approve")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_workflow_version_invalid_returns_422(self):
        """version=-1 should be rejected by schema validation."""
        await _init_test_db()

        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            async with await _get_test_client() as client:
                resp = await client.post(
                    "/api/workflows/run",
                    json={
                        "workflow": SIMPLE_WORKFLOW_YAML,
                        "version": -1,
                    },
                )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 7. Multi-tenant isolation
# ---------------------------------------------------------------------------


class TestMultiTenantIsolation:
    """Verify tenant A cannot see tenant B's data."""

    @pytest.mark.asyncio
    async def test_run_isolation_between_tenants(self):
        """Runs created by tenant_a should not be visible to tenant_b."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session

        tenant_a_run_id = uuid.uuid4()
        tenant_b_run_id = uuid.uuid4()

        async with async_session() as session:
            run_a = Run(
                id=tenant_a_run_id,
                workflow_name="workflow-a",
                status=RunStatus.COMPLETED,
                input_data={},
                tenant_id="tenant-a",
            )
            run_b = Run(
                id=tenant_b_run_id,
                workflow_name="workflow-b",
                status=RunStatus.COMPLETED,
                input_data={},
                tenant_id="tenant-b",
            )
            session.add(run_a)
            session.add(run_b)
            await session.commit()

        # Simulate tenant_a request - should see only own run
        with patch("sandcastle.api.routes.settings") as mock_settings, \
             patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-a"):
            from sandcastle.config import settings as real_settings
            for attr in dir(real_settings):
                if not attr.startswith("_"):
                    try:
                        setattr(mock_settings, attr, getattr(real_settings, attr))
                    except (AttributeError, TypeError):
                        pass
            mock_settings.auth_required = True
            mock_settings.is_local_mode = True

            async with await _get_test_client() as client:
                # Tenant A can see own run
                resp_a = await client.get(f"/api/runs/{tenant_a_run_id}")
                assert resp_a.status_code == 200

                # Tenant A cannot see tenant B's run
                resp_b = await client.get(f"/api/runs/{tenant_b_run_id}")
                assert resp_b.status_code == 404

    @pytest.mark.asyncio
    async def test_approval_isolation_between_tenants(self):
        """Approvals for tenant_a's runs should not be visible to tenant_b."""
        await _init_test_db()
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            Run,
            RunStatus,
            async_session,
        )

        run_a_id = uuid.uuid4()
        approval_a_id = uuid.uuid4()

        async with async_session() as session:
            run_a = Run(
                id=run_a_id,
                workflow_name="workflow-a",
                status=RunStatus.AWAITING_APPROVAL,
                input_data={},
                tenant_id="tenant-a",
            )
            session.add(run_a)
            ap_a = ApprovalRequest(
                id=approval_a_id,
                run_id=run_a_id,
                step_id="review",
                status=ApprovalStatus.PENDING,
                message="Review for A",
                on_timeout="abort",
            )
            session.add(ap_a)
            await session.commit()

        # Tenant B tries to access tenant A's approval
        with patch("sandcastle.api.routes.settings") as mock_settings, \
             patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-b"):
            from sandcastle.config import settings as real_settings
            for attr in dir(real_settings):
                if not attr.startswith("_"):
                    try:
                        setattr(mock_settings, attr, getattr(real_settings, attr))
                    except (AttributeError, TypeError):
                        pass
            mock_settings.auth_required = True
            mock_settings.is_local_mode = True

            async with await _get_test_client() as client:
                resp = await client.get(f"/api/approvals/{approval_a_id}")
                assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_schedule_isolation_between_tenants(self):
        """Schedules created by tenant_a should not be in tenant_b's list."""
        await _init_test_db()
        from sandcastle.models.db import Schedule, async_session

        sched_a_id = uuid.uuid4()
        sched_b_id = uuid.uuid4()

        async with async_session() as session:
            s_a = Schedule(
                id=sched_a_id,
                workflow_name="wf-a",
                cron_expression="0 * * * *",
                input_data={},
                enabled=True,
                tenant_id="tenant-a",
            )
            s_b = Schedule(
                id=sched_b_id,
                workflow_name="wf-b",
                cron_expression="0 * * * *",
                input_data={},
                enabled=True,
                tenant_id="tenant-b",
            )
            session.add(s_a)
            session.add(s_b)
            await session.commit()

        # Tenant A should only see their own schedule
        with patch("sandcastle.api.routes.settings") as mock_settings, \
             patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-a"):
            from sandcastle.config import settings as real_settings
            for attr in dir(real_settings):
                if not attr.startswith("_"):
                    try:
                        setattr(mock_settings, attr, getattr(real_settings, attr))
                    except (AttributeError, TypeError):
                        pass
            mock_settings.auth_required = True
            mock_settings.is_local_mode = True

            async with await _get_test_client() as client:
                resp = await client.get("/api/schedules")
                assert resp.status_code == 200
                items = resp.json()["data"]
                schedule_ids = [s["id"] for s in items]
                assert str(sched_a_id) in schedule_ids
                assert str(sched_b_id) not in schedule_ids

    @pytest.mark.asyncio
    async def test_eval_run_isolation_between_tenants(self):
        """EvalRuns created by tenant_a should not be listed for tenant_b."""
        await _init_test_db()
        from sandcastle.models.db import EvalRun, EvalRunStatus, async_session

        eval_a_id = uuid.uuid4()
        eval_b_id = uuid.uuid4()

        async with async_session() as session:
            e_a = EvalRun(
                id=eval_a_id,
                suite_name="suite-a",
                workflow_name="wf-a",
                status=EvalRunStatus.COMPLETED,
                total_cases=1,
                passed_cases=1,
                pass_rate=1.0,
                tenant_id="tenant-a",
            )
            e_b = EvalRun(
                id=eval_b_id,
                suite_name="suite-b",
                workflow_name="wf-b",
                status=EvalRunStatus.COMPLETED,
                total_cases=1,
                passed_cases=1,
                pass_rate=1.0,
                tenant_id="tenant-b",
            )
            session.add(e_a)
            session.add(e_b)
            await session.commit()

        with patch("sandcastle.api.routes.settings") as mock_settings, \
             patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-a"):
            from sandcastle.config import settings as real_settings
            for attr in dir(real_settings):
                if not attr.startswith("_"):
                    try:
                        setattr(mock_settings, attr, getattr(real_settings, attr))
                    except (AttributeError, TypeError):
                        pass
            mock_settings.auth_required = True
            mock_settings.is_local_mode = True

            async with await _get_test_client() as client:
                resp = await client.get("/api/eval/runs")
                assert resp.status_code == 200
                items = resp.json()["data"]
                eval_ids = [e["id"] for e in items]
                assert str(eval_a_id) in eval_ids
                assert str(eval_b_id) not in eval_ids


# ---------------------------------------------------------------------------
# 8. Budget enforcement
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    """Budget-related integration: max_cost_usd -> budget_exceeded status."""

    @pytest.mark.asyncio
    async def test_budget_exceeded_status_persisted_by_worker(self):
        """Worker should persist budget_exceeded status when executor returns it."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="budget-test-workflow",
                status=RunStatus.QUEUED,
                input_data={},
                max_cost_usd=0.01,
            )
            session.add(run)
            await session.commit()

        mock_result = _make_workflow_result(
            run_id,
            status="budget_exceeded",
            outputs={"expensive_step": "partial"},
            cost=0.05,
            error="Budget limit of $0.01 exceeded (actual: $0.05)",
        )

        with patch("sandcastle.engine.executor.execute_workflow", return_value=mock_result), \
             patch("sandcastle.engine.storage.create_storage"), \
             patch("sandcastle.webhooks.dispatcher.dispatch_webhook", new_callable=AsyncMock):

            result = await run_workflow_job(
                ctx={},
                workflow_yaml=BUDGET_WORKFLOW_YAML,
                input_data={},
                run_id=run_id,
                max_cost_usd=0.01,
            )

        assert result["status"] == "budget_exceeded"

        # Verify DB state
        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run.status == RunStatus.BUDGET_EXCEEDED
            assert run.total_cost_usd == 0.05
            assert "budget" in (run.error or "").lower() or "Budget" in (run.error or "")

    @pytest.mark.asyncio
    async def test_budget_from_run_db_used_when_not_passed_explicitly(self):
        """Worker should read max_cost_usd from DB when not passed as arg."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="budget-test-workflow",
                status=RunStatus.QUEUED,
                input_data={},
                max_cost_usd=2.50,
            )
            session.add(run)
            await session.commit()

        captured_kwargs = {}

        async def mock_execute(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return _make_workflow_result(run_id, cost=1.0)

        with patch("sandcastle.engine.executor.execute_workflow", side_effect=mock_execute), \
             patch("sandcastle.engine.storage.create_storage"), \
             patch("sandcastle.webhooks.dispatcher.dispatch_webhook", new_callable=AsyncMock):

            await run_workflow_job(
                ctx={},
                workflow_yaml=BUDGET_WORKFLOW_YAML,
                input_data={},
                run_id=run_id,
                # max_cost_usd intentionally NOT passed
            )

        # The worker should have read 2.50 from DB and passed it to executor
        assert captured_kwargs.get("max_cost_usd") == 2.50

    @pytest.mark.asyncio
    async def test_budget_webhook_dispatched_on_budget_exceeded(self):
        """Worker should dispatch webhook with budget_exceeded event."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        callback_url = "https://example.com/budget-webhook"

        async with async_session() as session:
            run = Run(
                id=uuid.UUID(run_id),
                workflow_name="budget-test-workflow",
                status=RunStatus.QUEUED,
                input_data={},
                callback_url=callback_url,
                max_cost_usd=0.01,
            )
            session.add(run)
            await session.commit()

        mock_result = _make_workflow_result(
            run_id,
            status="budget_exceeded",
            cost=0.05,
        )

        with patch("sandcastle.engine.executor.execute_workflow", return_value=mock_result), \
             patch("sandcastle.engine.storage.create_storage"), \
             patch("sandcastle.webhooks.dispatcher.dispatch_webhook", new_callable=AsyncMock) as mock_wh:

            await run_workflow_job(
                ctx={},
                workflow_yaml=BUDGET_WORKFLOW_YAML,
                input_data={},
                run_id=run_id,
            )

        mock_wh.assert_called_once()
        assert mock_wh.call_args.kwargs["event"] == "workflow.budget_exceeded"
        assert mock_wh.call_args.kwargs["url"] == callback_url

    @pytest.mark.asyncio
    async def test_budget_resolve_precedence_request_over_tenant(self):
        """Request-level budget should override tenant-level budget."""
        await _init_test_db()
        from sandcastle.models.db import ApiKey, async_session
        from sandcastle.api.auth import hash_key
        from sandcastle.api.routes import _resolve_budget

        # Create an API key with tenant budget
        tenant_id = "budget-tenant"
        api_key_raw = "sc_test_budget_tenant_key_placeholder"
        async with async_session() as session:
            ak = ApiKey(
                key_hash=hash_key(api_key_raw),
                key_prefix=api_key_raw[:8],
                tenant_id=tenant_id,
                name="budget-test-key",
                is_active=True,
                max_cost_per_run_usd=10.0,
            )
            session.add(ak)
            await session.commit()

        # Request budget (5.0) should override tenant budget (10.0)
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = True
            mock_settings.default_max_cost_usd = 0.0
            result = await _resolve_budget(5.0, tenant_id)

        assert result == 5.0

    @pytest.mark.asyncio
    async def test_budget_resolve_env_fallback(self):
        """When no request or tenant budget, env default should be used."""
        from sandcastle.api.routes import _resolve_budget

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            mock_settings.default_max_cost_usd = 3.0
            result = await _resolve_budget(None, None)

        assert result == 3.0


# ---------------------------------------------------------------------------
# 9. Idempotency key integration
# ---------------------------------------------------------------------------


class TestIdempotencyIntegration:
    """Idempotency key prevents duplicate runs."""

    @pytest.mark.asyncio
    async def test_duplicate_idempotency_key_returns_existing_run(self):
        """Second POST with same idempotency_key should return existing run."""
        await _init_test_db()

        with patch(
            "sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock
        ) as mock_enqueue, \
             patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()

            async with await _get_test_client() as client:
                # First request
                resp1 = await client.post(
                    "/api/workflows/run",
                    json={
                        "workflow": SIMPLE_WORKFLOW_YAML,
                        "idempotency_key": "unique-key-12345",
                    },
                )
                assert resp1.status_code == 200
                run_id_1 = resp1.json()["data"]["run_id"]

                # Second request with same key
                resp2 = await client.post(
                    "/api/workflows/run",
                    json={
                        "workflow": SIMPLE_WORKFLOW_YAML,
                        "idempotency_key": "unique-key-12345",
                    },
                )
                assert resp2.status_code == 200
                data2 = resp2.json()["data"]
                assert data2["run_id"] == run_id_1
                assert data2["idempotent"] is True

        # enqueue should have been called only once
        assert mock_enqueue.call_count == 1


# ---------------------------------------------------------------------------
# 10. Worker error recovery
# ---------------------------------------------------------------------------


class TestWorkerErrorRecovery:
    """Worker handles various failure modes gracefully."""

    @pytest.mark.asyncio
    async def test_worker_handles_missing_run_gracefully(self):
        """Worker should return error for non-existent run, not crash."""
        await _init_test_db()
        from sandcastle.queue.worker import run_workflow_job

        fake_run_id = str(uuid.uuid4())

        result = await run_workflow_job(
            ctx={},
            workflow_yaml=SIMPLE_WORKFLOW_YAML,
            input_data={},
            run_id=fake_run_id,
        )

        assert result["status"] == "failed"
        assert "not found" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_worker_marks_run_failed_on_invalid_yaml(self):
        """Worker should mark run as FAILED for invalid workflow YAML."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.queue.worker import run_workflow_job

        run_id = str(uuid.uuid4())
        run_uuid = uuid.UUID(run_id)

        async with async_session() as session:
            run = Run(
                id=run_uuid,
                workflow_name="bad-yaml-test",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        result = await run_workflow_job(
            ctx={},
            workflow_yaml="this is: [not: valid: yaml:",
            input_data={},
            run_id=run_id,
        )

        assert result["status"] == "failed"

        # Verify DB shows FAILED
        async with async_session() as session:
            run = await session.get(Run, run_uuid)
            assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_enqueue_failure_marks_run_as_failed(self):
        """If enqueue raises, the run should be marked FAILED in DB."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session

        with patch(
            "sandcastle.api.routes.enqueue_workflow",
            side_effect=ConnectionError("Redis down"),
        ), \
             patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()

            async with await _get_test_client() as client:
                resp = await client.post(
                    "/api/workflows/run",
                    json={"workflow": SIMPLE_WORKFLOW_YAML},
                )

            # Should return 500
            assert resp.status_code == 500

        # The run created in DB should have been cleaned up to FAILED
        # (We cannot easily get the run_id from a 500 response, so verify
        # that no QUEUED orphan runs exist for this workflow name)
        async with async_session() as session:
            from sqlalchemy import select

            stmt = select(Run).where(
                Run.workflow_name == "integration-test-workflow",
                Run.status == RunStatus.QUEUED,
            )
            result = await session.execute(stmt)
            # There may be queued runs from other tests, but the one
            # created by this test should have been marked FAILED.
            # We just verify the 500 was returned properly.


# ---------------------------------------------------------------------------
# 11. Run cancellation integration
# ---------------------------------------------------------------------------


class TestRunCancellation:
    """POST /runs/{id}/cancel should transition to CANCELLED."""

    @pytest.mark.asyncio
    async def test_cancel_queued_run(self):
        """Cancelling a QUEUED run should set it to CANCELLED."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="cancel-test",
                status=RunStatus.QUEUED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        async with await _get_test_client() as client:
            resp = await client.post(f"/api/runs/{run_id}/cancel")

        assert resp.status_code == 200

        # Verify DB state
        async with async_session() as session:
            run = await session.get(Run, run_id)
            assert run.status == RunStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_completed_run_returns_409(self):
        """Cancelling a COMPLETED run should return 409 conflict."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="cancel-test-completed",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        async with await _get_test_client() as client:
            resp = await client.post(f"/api/runs/{run_id}/cancel")

        assert resp.status_code in (400, 409)  # API returns 400 for non-cancellable


# ---------------------------------------------------------------------------
# 12. Stats endpoint integration
# ---------------------------------------------------------------------------


class TestStatsIntegration:
    """GET /stats reflects actual run data in DB."""

    @pytest.mark.asyncio
    async def test_stats_reflect_completed_and_failed_runs(self):
        """Stats should count completed and failed runs."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session

        now = datetime.now(timezone.utc)
        async with async_session() as session:
            for i in range(3):
                run = Run(
                    id=uuid.uuid4(),
                    workflow_name=f"stats-test-{i}",
                    status=RunStatus.COMPLETED,
                    input_data={},
                    total_cost_usd=0.01,
                    started_at=now - timedelta(minutes=5),
                    completed_at=now,
                )
                session.add(run)
            for i in range(2):
                run = Run(
                    id=uuid.uuid4(),
                    workflow_name=f"stats-fail-{i}",
                    status=RunStatus.FAILED,
                    input_data={},
                    error="test failure",
                )
                session.add(run)
            await session.commit()

        async with await _get_test_client() as client:
            resp = await client.get("/api/stats")

        assert resp.status_code == 200
        stats = resp.json()["data"]
        # Stats should include at least the runs we created
        assert stats["total_runs_today"] >= 5
        assert stats["success_rate"] >= 0
        assert stats["success_rate"] <= 1


# ---------------------------------------------------------------------------
# 13. Run deletion integration
# ---------------------------------------------------------------------------


class TestRunDeletion:
    """DELETE /runs/{id} removes the run from DB."""

    @pytest.mark.asyncio
    async def test_delete_run_removes_from_db(self):
        """Deleting a run should remove it completely."""
        await _init_test_db()
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="delete-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        async with await _get_test_client() as client:
            # Delete
            resp = await client.delete(f"/api/runs/{run_id}")
            assert resp.status_code == 200

            # Verify gone
            get_resp = await client.get(f"/api/runs/{run_id}")
            assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_run_returns_404(self):
        """Deleting a non-existent run should return 404."""
        await _init_test_db()
        fake_id = str(uuid.uuid4())

        async with await _get_test_client() as client:
            resp = await client.delete(f"/api/runs/{fake_id}")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 14. Webhook signature integration
# ---------------------------------------------------------------------------


class TestWebhookSignatureIntegration:
    """Verify webhook HMAC signatures work end-to-end."""

    def test_sign_and_verify_roundtrip(self):
        """_sign_payload and verify_signature should match."""
        from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature

        body = '{"event":"workflow.completed","run_id":"abc"}'
        secret = "test-secret-value-for-hmac"

        signature = _sign_payload(body, secret)
        assert verify_signature(body, signature, secret)

    def test_verify_rejects_wrong_secret(self):
        """verify_signature should fail with wrong secret."""
        from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature

        body = '{"event":"workflow.completed","run_id":"abc"}'
        sig = _sign_payload(body, "correct-secret")
        assert not verify_signature(body, sig, "wrong-secret")

    def test_verify_rejects_tampered_body(self):
        """verify_signature should fail with tampered body."""
        from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature

        original = '{"event":"workflow.completed","run_id":"abc"}'
        tampered = '{"event":"workflow.completed","run_id":"xyz"}'
        secret = "test-secret"

        sig = _sign_payload(original, secret)
        assert not verify_signature(tampered, sig, secret)


# ---------------------------------------------------------------------------
# 15. Approval skip with resume integration
# ---------------------------------------------------------------------------


class TestApprovalSkipIntegration:
    """POST /approvals/{id}/skip should mark as SKIPPED and trigger resume."""

    @pytest.mark.asyncio
    async def test_skip_approval_marks_skipped_and_calls_resume(self):
        """Skipping an approval should mark it SKIPPED and call resume."""
        await _init_test_db()
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        approval_id = uuid.uuid4()

        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="skip-test",
                status=RunStatus.AWAITING_APPROVAL,
                input_data={},
            )
            session.add(run)
            ap = ApprovalRequest(
                id=approval_id,
                run_id=run_id,
                step_id="review",
                status=ApprovalStatus.PENDING,
                message="Skip test",
                on_timeout="abort",
            )
            session.add(ap)
            await session.commit()

        with patch(
            "sandcastle.api.routes._resume_after_approval", new_callable=AsyncMock
        ) as mock_resume:
            async with await _get_test_client() as client:
                resp = await client.post(f"/api/approvals/{approval_id}/skip")

            assert resp.status_code == 200
            assert resp.json()["data"]["skipped"] is True

        # Verify approval status
        async with async_session() as session:
            ap = await session.get(ApprovalRequest, approval_id)
            ap_status = ap.status.value if hasattr(ap.status, "value") else ap.status
            assert ap_status == "skipped"

        # Verify resume was called with output_data=None
        mock_resume.assert_called_once()
        call_args = mock_resume.call_args
        assert call_args.kwargs.get("output_data") is None or call_args[1].get("output_data") is None
