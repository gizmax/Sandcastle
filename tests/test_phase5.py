"""Tests for Phase 5 features: budget, cancel, replay, fork, idempotency."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.dag import (
    build_plan,
    parse_yaml_string,
)
from sandcastle.engine.executor import (
    RunContext,
    _check_budget,
    execute_workflow,
)
from sandcastle.engine.sandbox import SandstormResult

# --- Budget checks ---


class TestBudgetCheck:
    def test_no_budget_set(self):
        ctx = RunContext(run_id="r1", input={}, costs=[1.0], max_cost_usd=None)
        assert _check_budget(ctx) is None

    def test_zero_budget(self):
        ctx = RunContext(run_id="r1", input={}, costs=[1.0], max_cost_usd=0)
        assert _check_budget(ctx) is None

    def test_under_budget(self):
        ctx = RunContext(run_id="r1", input={}, costs=[0.01], max_cost_usd=1.0)
        assert _check_budget(ctx) is None

    def test_warning_at_80_percent(self):
        ctx = RunContext(run_id="r1", input={}, costs=[0.85], max_cost_usd=1.0)
        assert _check_budget(ctx) == "warning"

    def test_exceeded_at_100_percent(self):
        ctx = RunContext(run_id="r1", input={}, costs=[1.0], max_cost_usd=1.0)
        assert _check_budget(ctx) == "exceeded"

    def test_exceeded_over_budget(self):
        ctx = RunContext(run_id="r1", input={}, costs=[1.5], max_cost_usd=1.0)
        assert _check_budget(ctx) == "exceeded"


# --- Context snapshot ---


class TestRunContext:
    def test_snapshot(self):
        ctx = RunContext(
            run_id="r1",
            input={"name": "test"},
            step_outputs={"step1": "output1"},
            costs=[0.01, 0.02],
        )
        snapshot = ctx.snapshot()
        assert snapshot["run_id"] == "r1"
        assert snapshot["input"] == {"name": "test"}
        assert snapshot["step_outputs"] == {"step1": "output1"}
        assert snapshot["costs"] == [0.01, 0.02]
        assert snapshot["total_cost"] == pytest.approx(0.03)

    def test_total_cost(self):
        ctx = RunContext(run_id="r1", input={}, costs=[0.01, 0.02, 0.03])
        assert ctx.total_cost == pytest.approx(0.06)

    def test_with_item_inherits_budget(self):
        ctx = RunContext(run_id="r1", input={}, max_cost_usd=5.0)
        child = ctx.with_item({"key": "val"}, 0)
        assert child.max_cost_usd == 5.0


# --- Budget workflow execution ---


class TestBudgetExecution:
    @pytest.mark.asyncio
    async def test_budget_exceeded_stops_workflow(self):
        yaml_content = """
name: budget-test
description: test budget
sandstorm_url: http://localhost:8000
steps:
  - id: step1
    prompt: "Expensive step"
  - id: step2
    depends_on: [step1]
    prompt: "Should not run"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        with (
            patch("sandcastle.engine.executor.SandstormClient") as MockClient,
            patch("sandcastle.engine.executor._check_cancel", return_value=False),
        ):
            mock_sandbox = AsyncMock()
            # Step 1 costs 2.0 which exceeds budget of 1.0
            mock_sandbox.query.return_value = SandstormResult(
                text="expensive result", total_cost_usd=2.0
            )
            mock_sandbox.close = AsyncMock()
            MockClient.return_value = mock_sandbox

            result = await execute_workflow(
                workflow, plan, input_data={}, max_cost_usd=1.0
            )

        assert result.status == "budget_exceeded"
        assert "step1" in result.outputs
        assert "step2" not in result.outputs

    @pytest.mark.asyncio
    async def test_no_budget_runs_normally(self):
        yaml_content = """
name: no-budget
description: no budget limit
sandstorm_url: http://localhost:8000
steps:
  - id: step1
    prompt: "Cheap step"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        with (
            patch("sandcastle.engine.executor.SandstormClient") as MockClient,
            patch("sandcastle.engine.executor._check_cancel", return_value=False),
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.return_value = SandstormResult(
                text="result", total_cost_usd=0.01
            )
            mock_sandbox.close = AsyncMock()
            MockClient.return_value = mock_sandbox

            result = await execute_workflow(
                workflow, plan, input_data={}, max_cost_usd=None
            )

        assert result.status == "completed"


# --- Cancel ---


class TestCancelExecution:
    @pytest.mark.asyncio
    async def test_cancel_stops_workflow(self):
        yaml_content = """
name: cancel-test
description: cancel test
sandstorm_url: http://localhost:8000
steps:
  - id: step1
    prompt: "This runs"
  - id: step2
    depends_on: [step1]
    prompt: "This gets cancelled"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        cancel_calls = [False, True]  # Not cancelled at first, cancelled after step1

        with (
            patch("sandcastle.engine.executor.SandstormClient") as MockClient,
            patch(
                "sandcastle.engine.executor._check_cancel",
                side_effect=cancel_calls,
            ),
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.return_value = SandstormResult(
                text="step1 result", total_cost_usd=0.01
            )
            mock_sandbox.close = AsyncMock()
            MockClient.return_value = mock_sandbox

            result = await execute_workflow(
                workflow, plan, input_data={}
            )

        assert result.status == "cancelled"
        assert "step1" in result.outputs


# --- Replay context ---


class TestReplayExecution:
    @pytest.mark.asyncio
    async def test_skip_steps_in_replay(self):
        yaml_content = """
name: replay-test
description: replay test
sandstorm_url: http://localhost:8000
steps:
  - id: step1
    prompt: "Already completed"
  - id: step2
    depends_on: [step1]
    prompt: "Replay from here using {steps.step1.output}"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        with (
            patch("sandcastle.engine.executor.SandstormClient") as MockClient,
            patch("sandcastle.engine.executor._check_cancel", return_value=False),
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.return_value = SandstormResult(
                text="replayed result", total_cost_usd=0.01
            )
            mock_sandbox.close = AsyncMock()
            MockClient.return_value = mock_sandbox

            result = await execute_workflow(
                workflow,
                plan,
                input_data={},
                initial_context={
                    "step_outputs": {"step1": "cached output from original"},
                    "costs": [0.005],
                },
                skip_steps={"step1"},
            )

        assert result.status == "completed"
        # step1 was skipped but its output is in context
        assert result.outputs["step1"] == "cached output from original"
        # step2 was executed fresh
        assert result.outputs["step2"] == "replayed result"
        # Only 1 query call (step2), step1 was skipped
        assert mock_sandbox.query.call_count == 1


# --- API Idempotency ---


class TestIdempotency:
    def test_idempotency_key_in_request(self):
        from sandcastle.api.schemas import WorkflowRunRequest

        req = WorkflowRunRequest(
            workflow_name="test",
            input={},
            idempotency_key="unique-key-123",
        )
        assert req.idempotency_key == "unique-key-123"

    def test_max_cost_in_request(self):
        from sandcastle.api.schemas import WorkflowRunRequest

        req = WorkflowRunRequest(
            workflow_name="test",
            input={},
            max_cost_usd=5.0,
        )
        assert req.max_cost_usd == 5.0


# --- API Schemas ---


class TestNewSchemas:
    def test_replay_request(self):
        from sandcastle.api.schemas import ReplayRequest

        req = ReplayRequest(from_step="enrich")
        assert req.from_step == "enrich"

    def test_fork_request(self):
        from sandcastle.api.schemas import ForkRequest

        req = ForkRequest(from_step="score", changes={"model": "opus"})
        assert req.from_step == "score"
        assert req.changes == {"model": "opus"}

    def test_run_status_response_with_time_machine(self):
        from sandcastle.api.schemas import RunStatusResponse

        resp = RunStatusResponse(
            run_id="r1",
            workflow_name="test",
            status="completed",
            parent_run_id="parent-1",
            replay_from_step="enrich",
            fork_changes={"model": "opus"},
            max_cost_usd=5.0,
        )
        assert resp.parent_run_id == "parent-1"
        assert resp.replay_from_step == "enrich"
        assert resp.fork_changes == {"model": "opus"}
        assert resp.max_cost_usd == 5.0

    def test_api_key_create_with_budget(self):
        from sandcastle.api.schemas import ApiKeyCreateRequest

        req = ApiKeyCreateRequest(
            tenant_id="t1", name="test", max_cost_per_run_usd=10.0
        )
        assert req.max_cost_per_run_usd == 10.0

    def test_api_key_response_with_prefix(self):
        from sandcastle.api.schemas import ApiKeyResponse

        resp = ApiKeyResponse(
            id="k1", key_prefix="sc_abcde", tenant_id="t1",
            name="test", is_active=True,
        )
        assert resp.key_prefix == "sc_abcde"

    def test_dead_letter_with_parallel_index(self):
        from sandcastle.api.schemas import DeadLetterItemResponse

        resp = DeadLetterItemResponse(
            id="d1", run_id="r1", step_id="s1",
            parallel_index=3, error="failed",
        )
        assert resp.parallel_index == 3


# --- DB Models ---


class TestDbModels:
    def test_run_status_enum_has_new_values(self):
        from sandcastle.models.db import RunStatus

        assert RunStatus.CANCELLED.value == "cancelled"
        assert RunStatus.BUDGET_EXCEEDED.value == "budget_exceeded"

    def test_run_checkpoint_model_exists(self):
        from sandcastle.models.db import RunCheckpoint

        assert RunCheckpoint.__tablename__ == "run_checkpoints"


# --- Budget resolution ---


class TestBudgetResolution:
    async def test_request_budget_takes_priority(self):
        from sandcastle.api.routes import _resolve_budget

        result = await _resolve_budget(5.0, "tenant1")
        assert result == 5.0

    async def test_no_budget_returns_none(self):
        from sandcastle.api.routes import _resolve_budget

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.default_max_cost_usd = 0.0
            mock_settings.auth_required = False
            result = await _resolve_budget(None, "tenant1")
        assert result is None

    async def test_env_budget_fallback(self):
        from sandcastle.api.routes import _resolve_budget

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.default_max_cost_usd = 10.0
            mock_settings.auth_required = False
            result = await _resolve_budget(None, "tenant1")
        assert result == 10.0
