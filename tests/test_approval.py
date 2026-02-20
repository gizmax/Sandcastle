"""Tests for human approval gates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    ApprovalConfig,
    StepDefinition,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import (
    RunContext,
    WorkflowPaused,
    _execute_approval_step,
)

# --- DAG parsing ---


APPROVAL_WORKFLOW_YAML = """
name: approval-test
description: Workflow with approval gate
steps:
  - id: prepare
    prompt: "Prepare data"
  - id: review
    type: approval
    depends_on: [prepare]
    approval_config:
      message: "Please review the prepared data"
      show_data: steps.prepare.output
      timeout_hours: 24
      on_timeout: abort
      allow_edit: true
  - id: finalize
    depends_on: [review]
    prompt: "Finalize with {steps.review.output}"
"""


class TestApprovalParsing:
    def test_parse_approval_step(self):
        workflow = parse_yaml_string(APPROVAL_WORKFLOW_YAML)
        review = workflow.get_step("review")
        assert review.type == "approval"
        assert review.approval_config is not None
        assert review.approval_config.message == "Please review the prepared data"
        assert review.approval_config.show_data == "steps.prepare.output"
        assert review.approval_config.timeout_hours == 24
        assert review.approval_config.on_timeout == "abort"
        assert review.approval_config.allow_edit is True

    def test_approval_step_gets_prompt_from_message(self):
        workflow = parse_yaml_string(APPROVAL_WORKFLOW_YAML)
        review = workflow.get_step("review")
        # Approval steps without explicit prompt use the message
        assert review.prompt == "Please review the prepared data"

    def test_standard_step_type_default(self):
        workflow = parse_yaml_string(APPROVAL_WORKFLOW_YAML)
        prepare = workflow.get_step("prepare")
        assert prepare.type == "standard"
        assert prepare.approval_config is None

    def test_validate_approval_without_message(self):
        yaml_content = """
name: bad-approval
description: test
steps:
  - id: review
    type: approval
    approval_config:
      message: ""
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert any("approval_config with a message" in e for e in errors)

    def test_validate_approval_without_config(self):
        yaml_content = """
name: bad-approval
description: test
steps:
  - id: review
    type: approval
    prompt: "placeholder"
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert any("approval_config with a message" in e for e in errors)

    def test_valid_approval_workflow(self):
        workflow = parse_yaml_string(APPROVAL_WORKFLOW_YAML)
        errors = validate(workflow)
        assert errors == []


# --- Executor ---


class TestApprovalExecution:
    @pytest.mark.asyncio
    async def test_approval_step_raises_workflow_paused(self):
        import uuid as _uuid

        test_run_id = str(_uuid.uuid4())
        test_approval_id = _uuid.uuid4()

        step = StepDefinition(
            id="review",
            prompt="Review",
            type="approval",
            approval_config=ApprovalConfig(
                message="Please review",
                timeout_hours=24,
                on_timeout="abort",
            ),
        )
        ctx = RunContext(
            run_id=test_run_id,
            input={"data": "test"},
            step_outputs={"prepare": {"result": "done"}},
        )

        # Mock DB session and models
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_approval = MagicMock()
        mock_approval.id = test_approval_id

        async def fake_refresh(obj):
            obj.id = test_approval_id

        mock_session.refresh = AsyncMock(side_effect=fake_refresh)

        mock_run = MagicMock()
        mock_run.callback_url = None
        mock_run.workflow_name = "test-workflow"
        mock_session.get = AsyncMock(return_value=mock_run)

        with (
            patch("sandcastle.models.db.async_session") as mock_session_ctx,
            patch("sandcastle.engine.executor._save_checkpoint", new_callable=AsyncMock),
            patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
        ):
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(WorkflowPaused) as exc_info:
                await _execute_approval_step(step, ctx, stage_index=1)

            assert exc_info.value.run_id == test_run_id

    def test_workflow_paused_exception_attributes(self):
        exc = WorkflowPaused(approval_id="ap-123", run_id="run-456")
        assert exc.approval_id == "ap-123"
        assert exc.run_id == "run-456"
        assert "ap-123" in str(exc)


# --- Integration-style tests (with full workflow) ---


class TestApprovalWorkflow:
    @pytest.mark.asyncio
    async def test_approval_pauses_workflow(self):
        """Full workflow with approval step should return awaiting_approval status."""
        from sandcastle.engine.dag import build_plan
        from sandcastle.engine.executor import execute_workflow
        from sandcastle.engine.sandshore import SandshoreResult

        yaml_content = """
name: approval-flow
description: test
steps:
  - id: prepare
    prompt: "Prepare"
  - id: review
    type: approval
    depends_on: [prepare]
    approval_config:
      message: "Review this"
  - id: finalize
    depends_on: [review]
    prompt: "Finalize"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        mock_result = SandshoreResult(text="prepared data", total_cost_usd=0.01)

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_get_client,
            patch("sandcastle.engine.storage.LocalStorage") as MockStorage,
            patch("sandcastle.engine.executor._execute_approval_step") as mock_approval,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.return_value = mock_result
            mock_get_client.return_value = mock_sandbox

            mock_storage = AsyncMock()
            mock_storage.read.return_value = None
            MockStorage.return_value = mock_storage

            # Simulate the approval step raising WorkflowPaused
            mock_approval.side_effect = WorkflowPaused(
                approval_id="ap-123", run_id="test-run"
            )

            result = await execute_workflow(workflow, plan, input_data={})

        assert result.status == "awaiting_approval"
        assert result.outputs.get("prepare") == "prepared data"
        assert result.completed_at is None
