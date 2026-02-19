"""Tests for hierarchical workflows (workflow-as-step)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.dag import (
    StepDefinition,
    SubWorkflowConfig,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import (
    RunContext,
    _execute_sub_workflow_step,
    execute_workflow,
)
from sandcastle.engine.sandshore import SandshoreResult

# --- DAG parsing ---


SUB_WORKFLOW_YAML = """
name: parent-workflow
description: Workflow that calls sub-workflows
sandstorm_url: http://localhost:8000
steps:
  - id: prepare
    prompt: "Prepare data"
  - id: sub_task
    type: sub_workflow
    depends_on: [prepare]
    sub_workflow:
      workflow: child-workflow
      input_mapping:
        company: steps.prepare.output.company
      output_mapping:
        result: enriched
      max_concurrent: 3
      timeout: 300
  - id: finalize
    depends_on: [sub_task]
    prompt: "Finalize with {steps.sub_task.output}"
"""


class TestSubWorkflowParsing:
    def test_parse_sub_workflow_step(self):
        workflow = parse_yaml_string(SUB_WORKFLOW_YAML)
        step = workflow.get_step("sub_task")
        assert step.type == "sub_workflow"
        assert step.sub_workflow is not None
        assert step.sub_workflow.workflow == "child-workflow"
        assert step.sub_workflow.max_concurrent == 3
        assert step.sub_workflow.timeout == 300

    def test_parse_input_mapping(self):
        workflow = parse_yaml_string(SUB_WORKFLOW_YAML)
        step = workflow.get_step("sub_task")
        assert step.sub_workflow.input_mapping == {
            "company": "steps.prepare.output.company"
        }

    def test_parse_output_mapping(self):
        workflow = parse_yaml_string(SUB_WORKFLOW_YAML)
        step = workflow.get_step("sub_task")
        assert step.sub_workflow.output_mapping == {"result": "enriched"}

    def test_sub_workflow_step_gets_auto_prompt(self):
        workflow = parse_yaml_string(SUB_WORKFLOW_YAML)
        step = workflow.get_step("sub_task")
        assert "child-workflow" in step.prompt

    def test_validate_sub_workflow_without_workflow_name(self):
        yaml_content = """
name: bad-sub
description: test
sandstorm_url: http://localhost:8000
steps:
  - id: sub
    type: sub_workflow
    sub_workflow:
      workflow: ""
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert any("sub_workflow.workflow" in e for e in errors)

    def test_validate_sub_workflow_without_config(self):
        yaml_content = """
name: bad-sub
description: test
sandstorm_url: http://localhost:8000
steps:
  - id: sub
    type: sub_workflow
    prompt: "placeholder"
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert any("sub_workflow.workflow" in e for e in errors)

    def test_valid_sub_workflow(self):
        workflow = parse_yaml_string(SUB_WORKFLOW_YAML)
        errors = validate(workflow)
        assert errors == []


# --- Execution ---


class TestSubWorkflowExecution:
    @pytest.mark.asyncio
    async def test_depth_limit(self):
        """Workflow exceeding max depth should fail."""
        from sandcastle.engine.dag import build_plan

        yaml_content = """
name: deep
description: test
sandstorm_url: http://localhost:8000
steps:
  - id: step1
    prompt: "Hello"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_get_client,
            patch("sandcastle.engine.storage.LocalStorage"),
            patch("sandcastle.config.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = ""
            mock_settings.e2b_api_key = ""
            mock_settings.max_workflow_depth = 3
            mock_settings.redis_url = "redis://localhost:6379/0"

            mock_sandbox = AsyncMock()
            mock_get_client.return_value = mock_sandbox

            result = await execute_workflow(
                workflow, plan, input_data={}, depth=10
            )

        assert result.status == "failed"
        assert "depth" in result.error.lower()

    @pytest.mark.asyncio
    async def test_sub_workflow_missing_file(self):
        """Sub-workflow step should fail if workflow file doesn't exist."""
        step = StepDefinition(
            id="sub",
            prompt="sub-wf",
            type="sub_workflow",
            sub_workflow=SubWorkflowConfig(workflow="nonexistent"),
        )
        ctx = RunContext(run_id="test-123", input={})
        storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 5
            mock_settings.workflows_dir = "/tmp/nonexistent_workflows_dir"
            result = await _execute_sub_workflow_step(step, ctx, storage, depth=0)

        assert result.status == "failed"
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_sub_workflow_no_config(self):
        """Sub-workflow step without config should fail gracefully."""
        step = StepDefinition(
            id="sub", prompt="sub-wf", type="sub_workflow"
        )
        ctx = RunContext(run_id="test-123", input={})
        storage = AsyncMock()

        result = await _execute_sub_workflow_step(step, ctx, storage, depth=0)
        assert result.status == "failed"
        assert "Missing" in result.error

    @pytest.mark.asyncio
    async def test_sub_workflow_success(self):
        """Sub-workflow should execute the child and return its outputs."""
        # Create a temp child workflow file
        child_yaml = """
name: child
description: test child
sandstorm_url: http://localhost:8000
steps:
  - id: child_step
    prompt: "Process {input.company}"
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = Path(tmpdir) / "child.yaml"
            child_path.write_text(child_yaml)

            step = StepDefinition(
                id="sub",
                prompt="sub-wf",
                type="sub_workflow",
                sub_workflow=SubWorkflowConfig(
                    workflow="child",
                    input_mapping={"company": "input.name"},
                ),
            )
            ctx = RunContext(
                run_id="parent-run-123",
                input={"name": "Acme"},
                step_outputs={},
            )

            mock_result = SandshoreResult(text="enriched Acme", total_cost_usd=0.01)

            with (
                patch("sandcastle.config.settings") as mock_settings,
                patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_get_client,
            ):
                mock_settings.max_workflow_depth = 5
                mock_settings.workflows_dir = tmpdir
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.redis_url = "redis://localhost:6379/0"

                mock_sandbox = AsyncMock()
                mock_sandbox.query.return_value = mock_result
                mock_get_client.return_value = mock_sandbox

                mock_storage = AsyncMock()
                mock_storage.read.return_value = None

                result = await _execute_sub_workflow_step(
                    step, ctx, mock_storage, depth=0
                )

            assert result.status == "completed"
            assert result.output is not None
            assert result.cost_usd > 0


# --- Config ---


class TestConfig:
    def test_max_workflow_depth_default(self):
        from sandcastle.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://localhost/test",
            redis_url="redis://localhost:6379/0",
        )
        assert s.max_workflow_depth == 5

    def test_max_workflow_depth_custom(self):
        from sandcastle.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://localhost/test",
            redis_url="redis://localhost:6379/0",
            max_workflow_depth=10,
        )
        assert s.max_workflow_depth == 10


# --- DB model fields ---


class TestDbFields:
    def test_run_has_depth_field(self):
        from sandcastle.models.db import Run

        assert hasattr(Run, "depth")
        assert hasattr(Run, "sub_workflow_of_step")

    def test_run_step_has_sub_run_ids(self):
        from sandcastle.models.db import RunStep

        assert hasattr(RunStep, "sub_run_ids")
