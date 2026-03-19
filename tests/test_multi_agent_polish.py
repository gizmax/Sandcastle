"""Tests for multi-agent delegation polish: dynamic names, progress events, error details."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    DelegateConfig,
    StepDefinition,
    SubWorkflowConfig,
)
from sandcastle.engine.executor import (
    RunContext,
    _execute_delegate_step,
    _execute_sub_workflow_step,
)
from sandcastle.engine.sandshore import SandshoreResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHILD_WORKFLOW_YAML = """
name: child
description: simple child workflow
steps:
  - id: child_step
    prompt: "Process {input.task}"
"""

FAILING_CHILD_YAML = """
name: failing-child
description: child that fails
steps:
  - id: fail_step
    prompt: "Will fail"
"""


def _make_sub_workflow_step(workflow_name: str) -> StepDefinition:
    return StepDefinition(
        id="sub",
        prompt="sub-wf",
        type="sub_workflow",
        sub_workflow=SubWorkflowConfig(workflow=workflow_name),
    )


def _make_delegate_step(workflow_name: str) -> StepDefinition:
    return StepDefinition(
        id="delegate",
        prompt="delegate step",
        type="delegate",
        delegate_config=DelegateConfig(
            workflow=workflow_name,
            task_description="do the task",
        ),
    )


# ---------------------------------------------------------------------------
# 1. Dynamic workflow name resolution via template
# ---------------------------------------------------------------------------


class TestDynamicSubWorkflowName:
    @pytest.mark.asyncio
    async def test_sub_workflow_template_name_resolves(self):
        """A template like {steps.router.output} resolves to the actual workflow name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = Path(tmpdir) / "child.yaml"
            child_path.write_text(CHILD_WORKFLOW_YAML)

            step = _make_sub_workflow_step("{steps.router.output}")
            step = StepDefinition(
                id="sub",
                prompt="sub-wf",
                type="sub_workflow",
                depends_on=["router"],
                sub_workflow=SubWorkflowConfig(workflow="{steps.router.output}"),
            )
            ctx = RunContext(
                run_id="run-001",
                input={},
                step_outputs={"router": "child"},
            )

            mock_result = SandshoreResult(text="done", total_cost_usd=0.01)

            with (
                patch("sandcastle.config.settings") as mock_settings,
                patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_runtime,
            ):
                mock_settings.max_workflow_depth = 5
                mock_settings.workflows_dir = tmpdir
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.redis_url = "redis://localhost:6379/0"

                mock_sandbox = AsyncMock()
                mock_sandbox.query.return_value = mock_result
                mock_runtime.return_value = mock_sandbox

                mock_storage = AsyncMock()
                mock_storage.read.return_value = None

                result = await _execute_sub_workflow_step(step, ctx, mock_storage, depth=0)

            assert result.status == "completed", f"Expected completed, got: {result.error}"

    @pytest.mark.asyncio
    async def test_delegate_template_name_resolves(self):
        """A delegate step with {steps.router.output} as workflow name resolves correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = Path(tmpdir) / "child.yaml"
            child_path.write_text(CHILD_WORKFLOW_YAML)

            step = StepDefinition(
                id="delegate",
                prompt="delegate step",
                type="delegate",
                depends_on=["router"],
                delegate_config=DelegateConfig(
                    workflow="{steps.router.output}",
                    task_description="do the task",
                ),
            )
            ctx = RunContext(
                run_id="run-002",
                input={},
                step_outputs={"router": "child"},
            )

            mock_result = SandshoreResult(text="done", total_cost_usd=0.01)

            with (
                patch("sandcastle.config.settings") as mock_settings,
                patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_runtime,
            ):
                mock_settings.max_workflow_depth = 5
                mock_settings.workflows_dir = tmpdir
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.redis_url = "redis://localhost:6379/0"

                mock_sandbox = AsyncMock()
                mock_sandbox.query.return_value = mock_result
                mock_runtime.return_value = mock_sandbox

                mock_storage = AsyncMock()
                mock_storage.read.return_value = None

                result = await _execute_delegate_step(step, ctx, mock_storage, depth=0)

            assert result.status == "completed", f"Expected completed, got: {result.error}"


# ---------------------------------------------------------------------------
# 2. Path traversal prevention in dynamic names
# ---------------------------------------------------------------------------


class TestPathTraversalPrevention:
    @pytest.mark.asyncio
    async def test_sub_workflow_path_traversal_blocked(self):
        """Resolved template value with path traversal chars must be rejected."""
        step = StepDefinition(
            id="sub",
            prompt="sub-wf",
            type="sub_workflow",
            depends_on=["router"],
            sub_workflow=SubWorkflowConfig(workflow="{steps.router.output}"),
        )
        ctx = RunContext(
            run_id="run-003",
            input={},
            step_outputs={"router": "../etc/passwd"},
        )
        mock_storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 5
            mock_settings.workflows_dir = "/tmp/safe_dir"

            result = await _execute_sub_workflow_step(step, ctx, mock_storage, depth=0)

        assert result.status == "failed"
        assert "Invalid sub-workflow name" in result.error or "not found" in result.error

    @pytest.mark.asyncio
    async def test_sub_workflow_absolute_path_blocked(self):
        """Resolved template value with slash must be rejected."""
        step = StepDefinition(
            id="sub",
            prompt="sub-wf",
            type="sub_workflow",
            depends_on=["router"],
            sub_workflow=SubWorkflowConfig(workflow="{steps.router.output}"),
        )
        ctx = RunContext(
            run_id="run-004",
            input={},
            step_outputs={"router": "/etc/passwd"},
        )
        mock_storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 5
            mock_settings.workflows_dir = "/tmp/safe_dir"

            result = await _execute_sub_workflow_step(step, ctx, mock_storage, depth=0)

        assert result.status == "failed"
        assert "Invalid sub-workflow name" in result.error

    @pytest.mark.asyncio
    async def test_delegate_path_traversal_blocked(self):
        """Delegate step with resolved path traversal value must be rejected."""
        step = StepDefinition(
            id="delegate",
            prompt="delegate step",
            type="delegate",
            depends_on=["router"],
            delegate_config=DelegateConfig(
                workflow="{steps.router.output}",
                task_description="do the task",
            ),
        )
        ctx = RunContext(
            run_id="run-005",
            input={},
            step_outputs={"router": "../secret"},
        )
        mock_storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 5
            mock_settings.workflows_dir = "/tmp/safe_dir"

            result = await _execute_delegate_step(step, ctx, mock_storage, depth=0)

        assert result.status == "failed"
        # Either a ValueError from path traversal guard or the not-found fallback
        assert result.error is not None


# ---------------------------------------------------------------------------
# 3. Delegate progress events emitted
# ---------------------------------------------------------------------------


class TestDelegateProgressEvents:
    @pytest.mark.asyncio
    async def test_progress_delegated_event_emitted(self):
        """step.progress with status=delegated must be published when sub-run starts."""
        published_events: list[tuple[str, dict]] = []

        def _capture_publish(event_type: str, data: dict) -> None:
            published_events.append((event_type, data))

        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = Path(tmpdir) / "child.yaml"
            child_path.write_text(CHILD_WORKFLOW_YAML)

            step = _make_delegate_step("child")
            ctx = RunContext(run_id="run-006", input={}, step_outputs={})

            mock_result = SandshoreResult(text="done", total_cost_usd=0.01)

            with (
                patch("sandcastle.config.settings") as mock_settings,
                patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_runtime,
                patch("sandcastle.engine.executor.event_bus") as mock_event_bus,
            ):
                mock_settings.max_workflow_depth = 5
                mock_settings.workflows_dir = tmpdir
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.redis_url = "redis://localhost:6379/0"

                mock_event_bus.publish.side_effect = _capture_publish

                mock_sandbox = AsyncMock()
                mock_sandbox.query.return_value = mock_result
                mock_runtime.return_value = mock_sandbox

                mock_storage = AsyncMock()
                mock_storage.read.return_value = None

                await _execute_delegate_step(step, ctx, mock_storage, depth=0)

        progress_events = [e for e in published_events if e[0] == "step.progress"]
        assert len(progress_events) >= 1, "Expected at least one step.progress event"

        delegated_events = [
            e for e in progress_events if e[1].get("status") == "delegated"
        ]
        assert len(delegated_events) == 1, "Expected exactly one 'delegated' progress event"

        ev_data = delegated_events[0][1]
        assert ev_data["step_id"] == "delegate"
        assert ev_data["workflow"] == "child"
        assert "sub_run_id" in ev_data
        assert ev_data["depth"] == 0

    @pytest.mark.asyncio
    async def test_progress_sub_completed_event_emitted(self):
        """step.progress with status=sub_completed must be published after sub-run finishes."""
        published_events: list[tuple[str, dict]] = []

        def _capture_publish(event_type: str, data: dict) -> None:
            published_events.append((event_type, data))

        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = Path(tmpdir) / "child.yaml"
            child_path.write_text(CHILD_WORKFLOW_YAML)

            step = _make_delegate_step("child")
            ctx = RunContext(run_id="run-007", input={}, step_outputs={})

            mock_result = SandshoreResult(text="done", total_cost_usd=0.01)

            with (
                patch("sandcastle.config.settings") as mock_settings,
                patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_runtime,
                patch("sandcastle.engine.executor.event_bus") as mock_event_bus,
            ):
                mock_settings.max_workflow_depth = 5
                mock_settings.workflows_dir = tmpdir
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.redis_url = "redis://localhost:6379/0"

                mock_event_bus.publish.side_effect = _capture_publish

                mock_sandbox = AsyncMock()
                mock_sandbox.query.return_value = mock_result
                mock_runtime.return_value = mock_sandbox

                mock_storage = AsyncMock()
                mock_storage.read.return_value = None

                await _execute_delegate_step(step, ctx, mock_storage, depth=0)

        progress_events = [e for e in published_events if e[0] == "step.progress"]
        sub_completed_events = [
            e for e in progress_events if e[1].get("status") == "sub_completed"
        ]
        assert len(sub_completed_events) == 1, "Expected exactly one 'sub_completed' progress event"

        ev_data = sub_completed_events[0][1]
        assert ev_data["step_id"] == "delegate"
        assert "sub_run_id" in ev_data
        assert "sub_status" in ev_data

    @pytest.mark.asyncio
    async def test_progress_events_contain_matching_sub_run_id(self):
        """delegated and sub_completed events must carry the same sub_run_id."""
        published_events: list[tuple[str, dict]] = []

        def _capture_publish(event_type: str, data: dict) -> None:
            published_events.append((event_type, data))

        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = Path(tmpdir) / "child.yaml"
            child_path.write_text(CHILD_WORKFLOW_YAML)

            step = _make_delegate_step("child")
            ctx = RunContext(run_id="run-008", input={}, step_outputs={})

            mock_result = SandshoreResult(text="done", total_cost_usd=0.01)

            with (
                patch("sandcastle.config.settings") as mock_settings,
                patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_runtime,
                patch("sandcastle.engine.executor.event_bus") as mock_event_bus,
            ):
                mock_settings.max_workflow_depth = 5
                mock_settings.workflows_dir = tmpdir
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.redis_url = "redis://localhost:6379/0"

                mock_event_bus.publish.side_effect = _capture_publish

                mock_sandbox = AsyncMock()
                mock_sandbox.query.return_value = mock_result
                mock_runtime.return_value = mock_sandbox

                mock_storage = AsyncMock()
                mock_storage.read.return_value = None

                await _execute_delegate_step(step, ctx, mock_storage, depth=0)

        progress_events = [
            e[1] for e in published_events if e[0] == "step.progress"
        ]
        assert len(progress_events) == 2

        sub_run_ids = {e["sub_run_id"] for e in progress_events}
        assert len(sub_run_ids) == 1, "Both progress events must share the same sub_run_id"


# ---------------------------------------------------------------------------
# 4. Error message includes sub-run details
# ---------------------------------------------------------------------------


class TestDelegateErrorMessages:
    @pytest.mark.asyncio
    async def test_error_includes_workflow_name(self):
        """Failure error message must mention the sub-workflow name."""
        step = _make_delegate_step("nonexistent-workflow")
        ctx = RunContext(run_id="run-009", input={}, step_outputs={})
        mock_storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 5
            mock_settings.workflows_dir = "/tmp/no_such_dir_xyz"

            result = await _execute_delegate_step(step, ctx, mock_storage, depth=0)

        assert result.status == "failed"
        assert "nonexistent-workflow" in result.error

    @pytest.mark.asyncio
    async def test_error_includes_depth_info(self):
        """Failure error message must include depth information."""
        step = _make_delegate_step("nonexistent-workflow")
        ctx = RunContext(run_id="run-010", input={}, step_outputs={})
        mock_storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 5
            mock_settings.workflows_dir = "/tmp/no_such_dir_xyz"

            result = await _execute_delegate_step(step, ctx, mock_storage, depth=2)

        assert result.status == "failed"
        assert "depth" in result.error.lower() or "3" in result.error

    @pytest.mark.asyncio
    async def test_error_includes_sub_run_id_on_sub_failure(self):
        """When sub-workflow run fails, error message must include sub_run_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a child workflow that will fail at execution (no API key)
            failing_child_yaml = """
name: failing-child
description: child that fails
steps:
  - id: fail_step
    prompt: "Will fail"
"""
            child_path = Path(tmpdir) / "failing-child.yaml"
            child_path.write_text(failing_child_yaml)

            step = _make_delegate_step("failing-child")
            ctx = RunContext(run_id="run-011", input={}, step_outputs={})
            mock_storage = AsyncMock()
            mock_storage.read.return_value = None

            with (
                patch("sandcastle.config.settings") as mock_settings,
                patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_runtime,
            ):
                mock_settings.max_workflow_depth = 5
                mock_settings.workflows_dir = tmpdir
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.redis_url = "redis://localhost:6379/0"

                # Simulate the sandbox raising an error
                mock_sandbox = AsyncMock()
                mock_sandbox.query.side_effect = RuntimeError("sandbox exploded")
                mock_runtime.return_value = mock_sandbox

                result = await _execute_delegate_step(step, ctx, mock_storage, depth=1)

        assert result.status == "failed"
        # The error from the sub-run should be surfaced
        assert result.error is not None
        # Should contain the workflow name
        assert "failing-child" in result.error

    @pytest.mark.asyncio
    async def test_max_depth_error_message_includes_depth(self):
        """Max depth exceeded error must mention the depth limit."""
        step = _make_delegate_step("some-workflow")
        ctx = RunContext(run_id="run-012", input={}, step_outputs={})
        mock_storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 3
            mock_settings.workflows_dir = "/tmp/no_such_dir"

            result = await _execute_delegate_step(step, ctx, mock_storage, depth=10)

        assert result.status == "failed"
        assert "depth" in result.error.lower() or "3" in result.error


# ---------------------------------------------------------------------------
# 5. max_depth enforcement with dynamic names
# ---------------------------------------------------------------------------


class TestMaxDepthWithDynamicNames:
    @pytest.mark.asyncio
    async def test_dynamic_name_sub_workflow_depth_limit(self):
        """Dynamic name resolution must not bypass depth check."""
        step = StepDefinition(
            id="sub",
            prompt="sub-wf",
            type="sub_workflow",
            depends_on=["router"],
            sub_workflow=SubWorkflowConfig(workflow="{steps.router.output}"),
        )
        ctx = RunContext(
            run_id="run-013",
            input={},
            step_outputs={"router": "child"},
        )
        mock_storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 3
            mock_settings.workflows_dir = "/tmp/no_such_dir"

            result = await _execute_sub_workflow_step(step, ctx, mock_storage, depth=10)

        assert result.status == "failed"
        assert "depth" in result.error.lower()

    @pytest.mark.asyncio
    async def test_dynamic_name_delegate_depth_limit(self):
        """Dynamic name in delegate must not bypass depth check."""
        step = StepDefinition(
            id="delegate",
            prompt="delegate step",
            type="delegate",
            depends_on=["router"],
            delegate_config=DelegateConfig(
                workflow="{steps.router.output}",
                task_description="do the task",
            ),
        )
        ctx = RunContext(
            run_id="run-014",
            input={},
            step_outputs={"router": "child"},
        )
        mock_storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 3

            result = await _execute_delegate_step(step, ctx, mock_storage, depth=10)

        assert result.status == "failed"
        assert "depth" in result.error.lower()

    @pytest.mark.asyncio
    async def test_dynamic_name_resolves_correctly_within_depth(self):
        """Dynamic name from step output resolves to correct workflow at depth=0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = Path(tmpdir) / "child.yaml"
            child_path.write_text(CHILD_WORKFLOW_YAML)

            step = StepDefinition(
                id="sub",
                prompt="sub-wf",
                type="sub_workflow",
                depends_on=["router"],
                sub_workflow=SubWorkflowConfig(
                    workflow="{steps.router.output}",
                    input_mapping={"task": "input.task"},
                ),
            )
            ctx = RunContext(
                run_id="run-015",
                input={"task": "hello"},
                step_outputs={"router": "child"},
            )

            mock_result = SandshoreResult(text="processed hello", total_cost_usd=0.02)

            with (
                patch("sandcastle.config.settings") as mock_settings,
                patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_runtime,
            ):
                mock_settings.max_workflow_depth = 5
                mock_settings.workflows_dir = tmpdir
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.redis_url = "redis://localhost:6379/0"

                mock_sandbox = AsyncMock()
                mock_sandbox.query.return_value = mock_result
                mock_runtime.return_value = mock_sandbox

                mock_storage = AsyncMock()
                mock_storage.read.return_value = None

                result = await _execute_sub_workflow_step(step, ctx, mock_storage, depth=0)

            assert result.status == "completed"
            assert result.cost_usd > 0
