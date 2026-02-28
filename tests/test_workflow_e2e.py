"""
END-TO-END WORKFLOW EXECUTION TESTS
====================================
Tests complete workflow execution paths through the executor,
covering all 15 step types and their interactions.

Run:
    cd /Users/gizmax/Documents/Sandcastle
    uv run pytest tests/test_workflow_e2e.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    ExecutionPlan,
    StepDefinition,
    WorkflowDefinition,
    build_plan,
    parse_yaml_string,
)
from sandcastle.engine.executor import (
    RunContext,
    StepExecutionError,
    StepResult,
    WorkflowPaused,
    WorkflowResult,
    _truncate_output,
    cancel_run_local,
    execute_step_with_retry,
    execute_workflow,
    resolve_templates,
    resolve_variable,
)
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime
from sandcastle.engine.storage import LocalStorage


# ──────────────────────────────────────────────────────────────
# FIXTURES AND HELPERS
# ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_db():
    """Bypass all DB writes and external calls during tests."""
    with (
        patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._save_checkpoint", new_callable=AsyncMock),
        patch(
            "sandcastle.engine.executor._get_cached_result",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("sandcastle.engine.executor._save_to_cache", new_callable=AsyncMock),
        patch(
            "sandcastle.engine.executor._check_cancel",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "sandcastle.engine.executor._save_routing_decision",
            new_callable=AsyncMock,
        ),
        patch(
            "sandcastle.engine.executor._save_policy_violations",
            new_callable=AsyncMock,
        ),
        patch("sandcastle.engine.telemetry.set_workflow_context"),
        patch("sandcastle.engine.telemetry.capture_step_error"),
    ):
        yield


def _make_sandbox(responses: list[str | dict] | None = None, cost: float = 0.01):
    """Create a mock SandshoreRuntime that returns predictable outputs.

    If responses is provided, each successive call returns the next item.
    If it's a string, it becomes text output. If dict, structured_output.
    """
    sb = MagicMock(spec=SandshoreRuntime)
    if responses is None:
        responses = ["mock output"]

    call_count = {"n": 0}

    async def _query(request):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        resp = responses[idx]
        if isinstance(resp, dict):
            return SandshoreResult(
                text=json.dumps(resp),
                structured_output=resp,
                total_cost_usd=cost,
                input_tokens=10,
                output_tokens=10,
            )
        if isinstance(resp, Exception):
            raise resp
        return SandshoreResult(
            text=str(resp),
            structured_output=None,
            total_cost_usd=cost,
            input_tokens=10,
            output_tokens=10,
        )

    sb.query = AsyncMock(side_effect=_query)
    return sb


def _make_storage():
    """Create a real LocalStorage in a temp directory."""
    return LocalStorage(tempfile.mkdtemp())


def _parse_and_plan(yaml_str: str):
    """Parse YAML and build execution plan."""
    wf = parse_yaml_string(yaml_str)
    plan = build_plan(wf)
    return wf, plan


async def _run_workflow(
    yaml_str: str,
    input_data: dict | None = None,
    sandbox_responses: list | None = None,
    sandbox_cost: float = 0.01,
    max_cost_usd: float | None = None,
):
    """Parse YAML, create mock sandbox, execute workflow and return result."""
    wf, plan = _parse_and_plan(yaml_str)
    sandbox = _make_sandbox(sandbox_responses or ["mock output"], sandbox_cost)
    storage = _make_storage()

    with patch(
        "sandcastle.engine.executor.get_sandshore_runtime",
        return_value=sandbox,
    ):
        result = await execute_workflow(
            workflow=wf,
            plan=plan,
            input_data=input_data or {},
            storage=storage,
            max_cost_usd=max_cost_usd,
        )
    return result, sandbox


# ──────────────────────────────────────────────────────────────
# 1. SIMPLE LINEAR WORKFLOWS
# ──────────────────────────────────────────────────────────────


class TestLinearWorkflows:
    """Test simple sequential workflow execution."""

    @pytest.mark.asyncio
    async def test_single_step_standard(self):
        """Single standard step workflow completes successfully."""
        yaml = """
name: single-step
description: One step workflow
steps:
  - id: step1
    prompt: "Analyze this"
"""
        result, sandbox = await _run_workflow(yaml)
        assert result.status == "completed"
        assert "step1" in result.outputs
        assert sandbox.query.call_count == 1

    @pytest.mark.asyncio
    async def test_three_step_chain(self):
        """Three step chain with dependencies executes in order."""
        yaml = """
name: three-step-chain
description: Chain workflow
steps:
  - id: step1
    prompt: "Step 1"
  - id: step2
    prompt: "Step 2 using {steps.step1.output}"
    depends_on: [step1]
  - id: step3
    prompt: "Step 3 using {steps.step2.output}"
    depends_on: [step2]
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=["output-1", "output-2", "output-3"],
        )
        assert result.status == "completed"
        assert result.outputs["step1"] == "output-1"
        assert result.outputs["step2"] == "output-2"
        assert result.outputs["step3"] == "output-3"
        assert sandbox.query.call_count == 3

    @pytest.mark.asyncio
    async def test_template_variable_resolution(self):
        """Template variables {steps.X.output} resolve in prompts."""
        yaml = """
name: template-test
description: Template variable test
steps:
  - id: gen
    prompt: "Generate data"
  - id: use
    prompt: "Use this data: {steps.gen.output}"
    depends_on: [gen]
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=["generated-data", "processed-data"],
        )
        assert result.status == "completed"
        # Verify second call received resolved template
        second_call_prompt = sandbox.query.call_args_list[1][0][0]["prompt"]
        assert "generated-data" in second_call_prompt

    @pytest.mark.asyncio
    async def test_input_variable_resolution(self):
        """Template variables {input.X} resolve from workflow input."""
        yaml = """
name: input-test
description: Input variable test
steps:
  - id: step1
    prompt: "Analyze {input.topic} for {input.audience}"
"""
        result, sandbox = await _run_workflow(
            yaml,
            input_data={"topic": "AI safety", "audience": "researchers"},
        )
        assert result.status == "completed"
        call_prompt = sandbox.query.call_args_list[0][0][0]["prompt"]
        assert "AI safety" in call_prompt
        assert "researchers" in call_prompt

    @pytest.mark.asyncio
    async def test_memory_variable_injection(self):
        """Template variable {memory} resolves via memory module."""
        yaml = """
name: memory-test
description: Memory injection test
steps:
  - id: step1
    prompt: "Use memories: {memory}"
"""
        with patch(
            "sandcastle.engine.executor.resolve_variable",
            wraps=resolve_variable,
        ):
            result, _ = await _run_workflow(yaml)
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_parallel_independent_steps(self):
        """Steps without dependencies run in parallel."""
        yaml = """
name: parallel-test
description: Parallel independent steps
steps:
  - id: a
    prompt: "Step A"
  - id: b
    prompt: "Step B"
  - id: c
    prompt: "Step C"
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=["output-a", "output-b", "output-c"],
        )
        assert result.status == "completed"
        assert len(result.outputs) == 3

    @pytest.mark.asyncio
    async def test_json_output_auto_parse(self):
        """JSON string output is automatically parsed to dict."""
        yaml = """
name: json-parse-test
description: JSON auto-parse test
steps:
  - id: step1
    prompt: "Return JSON"
"""
        json_response = '{"key": "value", "count": 42}'
        result, _ = await _run_workflow(
            yaml,
            sandbox_responses=[json_response],
        )
        assert result.status == "completed"
        assert result.outputs["step1"] == {"key": "value", "count": 42}


# ──────────────────────────────────────────────────────────────
# 2. BRANCHING WORKFLOWS
# ──────────────────────────────────────────────────────────────


class TestConditionWorkflows:
    """Test condition-based branching."""

    @pytest.mark.asyncio
    async def test_condition_true_branch(self):
        """Condition=true executes then_steps, skips else_steps."""
        yaml = """
name: condition-true
description: Condition test
steps:
  - id: data
    type: code
    code_config:
      code: "result = 'some data'"
  - id: check
    type: condition
    condition_config:
      expression: "True"
      then: [process]
      else: [skip_step]
  - id: process
    prompt: "Process data"
    depends_on: [data]
  - id: skip_step
    prompt: "This should be skipped"
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=["processed"],
        )
        assert result.status == "completed"
        assert result.outputs.get("process") is not None
        # skip_step should be None (skipped)
        assert result.outputs.get("skip_step") is None

    @pytest.mark.asyncio
    async def test_condition_false_branch(self):
        """Condition=false executes else_steps, skips then_steps."""
        yaml = """
name: condition-false
description: Condition false test
steps:
  - id: check
    type: condition
    condition_config:
      expression: "False"
      then: [true_step]
      else: [false_step]
  - id: true_step
    prompt: "Should not run"
  - id: false_step
    prompt: "Should run"
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=["false branch output"],
        )
        assert result.status == "completed"
        assert result.outputs.get("true_step") is None
        assert result.outputs.get("false_step") is not None

    @pytest.mark.asyncio
    async def test_nested_conditions(self):
        """Nested condition (condition inside condition's then_steps)."""
        yaml = """
name: nested-condition
description: Nested condition test
steps:
  - id: outer_check
    type: condition
    condition_config:
      expression: "True"
      then: [inner_check, deep_step]
      else: [outer_else]
  - id: inner_check
    type: condition
    condition_config:
      expression: "True"
      then: [deep_step]
      else: [inner_else]
  - id: deep_step
    prompt: "Deepest step"
  - id: inner_else
    prompt: "Inner else"
  - id: outer_else
    prompt: "Outer else"
"""
        result, _ = await _run_workflow(
            yaml,
            sandbox_responses=["deep result"],
        )
        assert result.status == "completed"
        assert result.outputs.get("deep_step") is not None
        assert result.outputs.get("inner_else") is None
        assert result.outputs.get("outer_else") is None

    @pytest.mark.asyncio
    async def test_condition_with_input_expression(self):
        """Condition expression referencing input variables."""
        yaml = """
name: input-condition
description: Condition with input reference
steps:
  - id: check
    type: condition
    condition_config:
      expression: "input.get('mode') == 'verbose'"
      then: [verbose_step]
      else: [brief_step]
  - id: verbose_step
    prompt: "Verbose output"
  - id: brief_step
    prompt: "Brief output"
"""
        result, _ = await _run_workflow(
            yaml,
            input_data={"mode": "verbose"},
            sandbox_responses=["verbose result"],
        )
        assert result.status == "completed"
        assert result.outputs.get("verbose_step") is not None
        assert result.outputs.get("brief_step") is None

    @pytest.mark.asyncio
    async def test_condition_with_step_output(self):
        """Condition expression references previous step output."""
        yaml = """
name: step-output-condition
description: Condition references step output
steps:
  - id: data
    type: code
    code_config:
      code: "result = 'hello world long string content'"
  - id: check
    type: condition
    depends_on: [data]
    condition_config:
      expression: "len('{steps.data.output}') > 5"
      then: [long_handler]
      else: [short_handler]
  - id: long_handler
    prompt: "Handle long data"
  - id: short_handler
    prompt: "Handle short data"
"""
        result, _ = await _run_workflow(
            yaml,
            sandbox_responses=["long result"],
        )
        assert result.status == "completed"
        assert result.outputs.get("long_handler") is not None
        assert result.outputs.get("short_handler") is None


# ──────────────────────────────────────────────────────────────
# 3. LOOP WORKFLOWS
# ──────────────────────────────────────────────────────────────


class TestLoopWorkflows:
    """Test loop step execution."""

    @pytest.mark.asyncio
    async def test_loop_over_list(self):
        """Loop iterates over a list of items."""
        yaml = """
name: loop-test
description: Loop over list
steps:
  - id: data
    type: code
    code_config:
      code: "result = ['apple', 'banana', 'cherry', 'date', 'elderberry']"
  - id: looper
    type: loop
    depends_on: [data]
    loop_config:
      over: "steps.data.output"
      step_ids: [process_item]
  - id: process_item
    type: code
    code_config:
      code: "result = 'processed: ' + str(_input.get('_item', ''))"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        loop_output = result.outputs.get("looper")
        assert isinstance(loop_output, list)
        assert len(loop_output) == 5

    @pytest.mark.asyncio
    async def test_loop_with_max_iterations(self):
        """Loop respects max_iterations limit."""
        yaml = """
name: loop-max-test
description: Loop with max_iterations
steps:
  - id: data
    type: code
    code_config:
      code: "result = list(range(100))"
  - id: looper
    type: loop
    depends_on: [data]
    loop_config:
      over: "steps.data.output"
      step_ids: [process_item]
      max_iterations: 3
  - id: process_item
    type: code
    code_config:
      code: "result = _input.get('_item', 0) * 2"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        loop_output = result.outputs.get("looper")
        assert isinstance(loop_output, list)
        assert len(loop_output) == 3

    @pytest.mark.asyncio
    async def test_loop_with_until(self):
        """Loop breaks when until condition is met."""
        yaml = """
name: loop-until-test
description: Loop with until condition
steps:
  - id: data
    type: code
    code_config:
      code: "result = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]"
  - id: looper
    type: loop
    depends_on: [data]
    loop_config:
      over: "steps.data.output"
      step_ids: [process_item]
      until: "index >= 2"
  - id: process_item
    type: code
    code_config:
      code: "result = _input.get('_item', 0) * 10"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        loop_output = result.outputs.get("looper")
        assert isinstance(loop_output, list)
        # until triggers after index=2 (3 iterations: 0, 1, 2)
        assert len(loop_output) == 3

    @pytest.mark.asyncio
    async def test_loop_empty_items(self):
        """Loop with empty items list produces 0 iterations."""
        yaml = """
name: loop-empty-test
description: Loop over empty list
steps:
  - id: data
    type: code
    code_config:
      code: "result = []"
  - id: looper
    type: loop
    depends_on: [data]
    loop_config:
      over: "steps.data.output"
      step_ids: [process_item]
  - id: process_item
    type: code
    code_config:
      code: "result = 'should not run'"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        loop_output = result.outputs.get("looper")
        assert isinstance(loop_output, list)
        assert len(loop_output) == 0

    @pytest.mark.asyncio
    async def test_loop_substep_failure(self):
        """Loop continues after sub-step failure (collecting partial results)."""
        yaml = """
name: loop-fail-test
description: Loop with failing sub-step
steps:
  - id: data
    type: code
    code_config:
      code: "result = [1, 2, 3, 4, 5]"
  - id: looper
    type: loop
    depends_on: [data]
    loop_config:
      over: "steps.data.output"
      step_ids: [process_item]
  - id: process_item
    type: code
    code_config:
      code: |
        item = _input.get('_item', 0)
        if item == 3:
            result = None
        else:
            result = item * 10
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        loop_output = result.outputs.get("looper")
        assert isinstance(loop_output, list)
        assert len(loop_output) == 5


# ──────────────────────────────────────────────────────────────
# 4. PARALLEL / RACE WORKFLOWS
# ──────────────────────────────────────────────────────────────


class TestRaceWorkflows:
    """Test race step execution - parallel branches, first valid wins."""

    @pytest.mark.asyncio
    async def test_race_first_wins(self):
        """Race: first completed branch wins."""
        yaml = """
name: race-test
description: Race three branches
steps:
  - id: branch_a
    prompt: "Branch A"
  - id: branch_b
    prompt: "Branch B"
  - id: branch_c
    prompt: "Branch C"
  - id: racer
    type: race
    race_config:
      branches:
        - [branch_a]
        - [branch_b]
        - [branch_c]
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=["result-a", "result-b", "result-c"],
        )
        assert result.status == "completed"
        racer_output = result.outputs.get("racer")
        assert racer_output is not None

    @pytest.mark.asyncio
    async def test_race_with_validator(self):
        """Race with validator - only valid results win."""
        yaml = """
name: race-validator-test
description: Race with validator expression
steps:
  - id: branch_a
    type: code
    code_config:
      code: "result = 'short'"
  - id: branch_b
    type: code
    code_config:
      code: "result = 'this is a longer valid result that passes'"
  - id: racer
    type: race
    race_config:
      branches:
        - [branch_a]
        - [branch_b]
      validator: "len(str(output)) > 10"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        racer_output = result.outputs.get("racer")
        # branch_b should win since its output is longer than 10 chars
        assert racer_output is not None
        assert len(str(racer_output)) > 10

    @pytest.mark.asyncio
    async def test_race_all_fail_fallback(self):
        """Race where all branches fail returns fallback or fails."""
        yaml = """
name: race-all-fail
description: Race all branches fail
steps:
  - id: fail_a
    type: code
    code_config:
      code: "raise ValueError('fail a')"
  - id: fail_b
    type: code
    code_config:
      code: "raise ValueError('fail b')"
  - id: racer
    type: race
    race_config:
      branches:
        - [fail_a]
        - [fail_b]
"""
        result, _ = await _run_workflow(yaml)
        # The racer step should fail since all branches failed
        assert result.status == "failed"


# ──────────────────────────────────────────────────────────────
# 5. ASYNC / POLLING WORKFLOWS
# ──────────────────────────────────────────────────────────────


class TestGateWorkflows:
    """Test gate step execution with various strategies."""

    @pytest.mark.asyncio
    async def test_gate_auto_approve_timeout(self):
        """Gate with timeout strategy auto-approves."""
        yaml = """
name: gate-timeout-test
description: Gate with timeout auto-approve
steps:
  - id: gate
    type: gate
    gate_config:
      strategies:
        - type: timeout
          config:
            seconds: 0
            action: approve
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        gate_output = result.outputs.get("gate")
        assert gate_output is not None
        assert gate_output["decision"] == "approved"
        assert gate_output["strategy"] == "timeout"

    @pytest.mark.asyncio
    async def test_gate_auto_reject_timeout(self):
        """Gate with timeout strategy auto-rejects."""
        yaml = """
name: gate-reject-test
description: Gate with timeout auto-reject
steps:
  - id: gate
    type: gate
    gate_config:
      strategies:
        - type: timeout
          config:
            seconds: 0
            action: reject
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        gate_output = result.outputs.get("gate")
        assert gate_output is not None
        assert gate_output["decision"] == "rejected"


# ──────────────────────────────────────────────────────────────
# 6. COMPOSITION WORKFLOWS
# ──────────────────────────────────────────────────────────────


class TestTransformWorkflows:
    """Test transform step execution."""

    @pytest.mark.asyncio
    async def test_transform_basic(self):
        """Transform step with simple template."""
        yaml = """
name: transform-test
description: Transform step test
steps:
  - id: data
    type: code
    code_config:
      code: "result = {'name': 'Alice', 'age': 30}"
  - id: transform
    type: transform
    depends_on: [data]
    transform_config:
      template: '{"greeting": "Hello {steps.data.output.name}", "doubled_age": 60}'
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        transform_output = result.outputs.get("transform")
        assert transform_output is not None
        # Template renders to JSON string, auto-parsed
        assert isinstance(transform_output, dict)

    @pytest.mark.asyncio
    async def test_transform_jinja2_syntax(self):
        """Transform step supports {{ var }} syntax."""
        yaml = """
name: transform-jinja2
description: Jinja2-like transform test
steps:
  - id: data
    type: code
    code_config:
      code: "result = 'world'"
  - id: transform
    type: transform
    depends_on: [data]
    transform_config:
      template: "Hello {{ steps.data.output }}"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        transform_output = result.outputs.get("transform")
        assert "Hello" in str(transform_output)
        assert "world" in str(transform_output)

    @pytest.mark.asyncio
    async def test_transform_with_tojson_filter(self):
        """Transform step supports {{ var | tojson }} filter."""
        yaml = """
name: transform-tojson
description: Transform with tojson filter
steps:
  - id: data
    type: code
    code_config:
      code: "result = {'key': 'value'}"
  - id: transform
    type: transform
    depends_on: [data]
    transform_config:
      template: "Data is: {{ steps.data.output | tojson }}"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        transform_output = result.outputs.get("transform")
        assert "key" in str(transform_output)
        assert "value" in str(transform_output)


class TestNotifyWorkflows:
    """Test notify step execution."""

    @pytest.mark.asyncio
    async def test_notify_basic(self):
        """Notify step resolves message and records notification."""
        yaml = """
name: notify-test
description: Notify step test
steps:
  - id: data
    type: code
    code_config:
      code: "result = 'Important finding'"
  - id: notify
    type: notify
    depends_on: [data]
    notify_config:
      service: slack
      channel: "#alerts"
      message: "Alert: {steps.data.output}"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        notify_output = result.outputs.get("notify")
        assert notify_output is not None
        assert notify_output["service"] == "slack"
        # Channel may have auto-injected context from depends_on
        assert "#alerts" in notify_output["channel"]
        assert "Important finding" in notify_output["message"]
        assert notify_output["status"] == "logged"

    @pytest.mark.asyncio
    async def test_notify_with_input_vars(self):
        """Notify step resolves input variables in message."""
        yaml = """
name: notify-input-test
description: Notify with input vars
steps:
  - id: notify
    type: notify
    notify_config:
      service: teams
      channel: "{input.channel}"
      message: "Build {input.build_id} completed"
"""
        result, _ = await _run_workflow(
            yaml,
            input_data={"channel": "#deploy", "build_id": "v1.2.3"},
        )
        assert result.status == "completed"
        notify_output = result.outputs.get("notify")
        assert notify_output["channel"] == "#deploy"
        assert "v1.2.3" in notify_output["message"]


class TestCodeWorkflows:
    """Test code step execution."""

    @pytest.mark.asyncio
    async def test_code_step_basic(self):
        """Code step executes Python code and returns result."""
        yaml = """
name: code-test
description: Code step test
steps:
  - id: compute
    type: code
    code_config:
      code: "result = sum(range(10))"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs.get("compute") == 45

    @pytest.mark.asyncio
    async def test_code_step_with_context(self):
        """Code step can access _input and _steps context."""
        yaml = """
name: code-context-test
description: Code step with context
steps:
  - id: first
    type: code
    code_config:
      code: "result = 42"
  - id: second
    type: code
    depends_on: [first]
    code_config:
      code: "result = _steps.get('first', 0) * 2"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs.get("first") == 42
        assert result.outputs.get("second") == 84

    @pytest.mark.asyncio
    async def test_code_step_blocked_pattern(self):
        """Code step blocks dangerous patterns."""
        yaml = """
name: code-blocked
description: Code step blocks dangerous code
steps:
  - id: evil
    type: code
    code_config:
      code: "x = __import__('os')"
    retry:
      max_attempts: 1
      on_failure: skip
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        # Step should be skipped/failed due to blocked pattern
        assert result.outputs.get("evil") is None

    @pytest.mark.asyncio
    async def test_code_step_json_output(self):
        """Code step returning a dict preserves the structure."""
        yaml = """
name: code-json-output
description: Code step with JSON output
steps:
  - id: compute
    type: code
    code_config:
      code: "result = {'total': 100, 'items': [1, 2, 3]}"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        output = result.outputs.get("compute")
        assert isinstance(output, dict)
        assert output["total"] == 100
        assert output["items"] == [1, 2, 3]


# ──────────────────────────────────────────────────────────────
# 7. ERROR HANDLING WORKFLOWS
# ──────────────────────────────────────────────────────────────


class TestErrorHandling:
    """Test error handling, retries, and failure modes."""

    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        """Step retries up to max_attempts on failure."""
        yaml = """
name: retry-test
description: Retry test
steps:
  - id: flaky
    prompt: "Flaky step"
    retry:
      max_attempts: 3
      backoff: fixed
      on_failure: abort
"""
        sb = _make_sandbox()
        # Fail first 2, succeed on 3rd
        call_count = {"n": 0}

        async def _query(request):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise Exception("transient error")
            return SandshoreResult(
                text="success",
                structured_output=None,
                total_cost_usd=0.01,
                input_tokens=10,
                output_tokens=10,
            )

        sb.query = AsyncMock(side_effect=_query)
        wf, plan = _parse_and_plan(yaml)
        storage = _make_storage()

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb),
            patch("sandcastle.engine.executor._backoff_delay", return_value=0),
        ):
            result = await execute_workflow(
                workflow=wf,
                plan=plan,
                input_data={},
                storage=storage,
            )
        assert result.status == "completed"
        assert result.outputs.get("flaky") == "success"
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_failure_on_failure_skip(self):
        """Step with on_failure=skip continues workflow after failure."""
        yaml = """
name: skip-on-fail
description: Skip on failure
steps:
  - id: flaky
    type: code
    code_config:
      code: "result = 1 / 0"
    retry:
      max_attempts: 1
      on_failure: skip
  - id: after
    prompt: "After flaky"
    depends_on: [flaky]
"""
        result, _ = await _run_workflow(
            yaml,
            sandbox_responses=["after result"],
        )
        assert result.status == "completed"
        assert result.outputs.get("flaky") is None
        assert result.outputs.get("after") is not None

    @pytest.mark.asyncio
    async def test_failure_on_failure_abort(self):
        """Step with on_failure=abort stops the whole workflow."""
        yaml = """
name: abort-on-fail
description: Abort on failure
steps:
  - id: failing
    type: code
    code_config:
      code: "result = 1 / 0"
    retry:
      max_attempts: 1
      on_failure: abort
  - id: after
    prompt: "Should not run"
    depends_on: [failing]
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "failed"
        assert "division by zero" in (result.error or "").lower()
        assert "after" not in result.outputs

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self):
        """Step failure with fallback uses fallback prompt."""
        yaml = """
name: fallback-test
description: Fallback test
steps:
  - id: primary
    prompt: "Primary prompt"
    retry:
      max_attempts: 1
      on_failure: fallback
    fallback:
      prompt: "Fallback prompt"
      model: haiku
"""
        sb = _make_sandbox()
        call_count = {"n": 0}

        async def _query(request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("primary failed")
            return SandshoreResult(
                text="fallback result",
                structured_output=None,
                total_cost_usd=0.005,
                input_tokens=5,
                output_tokens=5,
            )

        sb.query = AsyncMock(side_effect=_query)
        wf, plan = _parse_and_plan(yaml)
        storage = _make_storage()

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=wf,
                plan=plan,
                input_data={},
                storage=storage,
            )
        assert result.status == "completed"
        assert result.outputs.get("primary") == "fallback result"

    @pytest.mark.asyncio
    async def test_budget_exceeded_mid_workflow(self):
        """Workflow stops when budget is exceeded."""
        yaml = """
name: budget-test
description: Budget exceeded mid-workflow
steps:
  - id: expensive1
    prompt: "Expensive step 1"
  - id: expensive2
    prompt: "Expensive step 2"
    depends_on: [expensive1]
"""
        result, _ = await _run_workflow(
            yaml,
            sandbox_responses=["output1", "output2"],
            sandbox_cost=0.10,
            max_cost_usd=0.05,
        )
        # First step costs 0.10 which exceeds budget of 0.05
        assert result.status == "budget_exceeded"

    @pytest.mark.asyncio
    async def test_cancel_during_execution(self):
        """Workflow can be cancelled during execution."""
        yaml = """
name: cancel-test
description: Cancel during execution
steps:
  - id: step1
    prompt: "Step 1"
  - id: step2
    prompt: "Step 2"
    depends_on: [step1]
"""
        wf, plan = _parse_and_plan(yaml)
        sb = _make_sandbox(["output1", "output2"])
        storage = _make_storage()

        cancel_after_first = {"called": False}

        async def _cancel_check(run_id):
            if cancel_after_first["called"]:
                return True
            cancel_after_first["called"] = True
            return False

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb),
            patch(
                "sandcastle.engine.executor._check_cancel",
                new_callable=AsyncMock,
                side_effect=_cancel_check,
            ),
        ):
            result = await execute_workflow(
                workflow=wf,
                plan=plan,
                input_data={},
                storage=storage,
            )
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_max_attempts_exhausted(self):
        """Step fails after exhausting max_attempts."""
        yaml = """
name: retry-exhaust-test
description: Max retries exhausted
steps:
  - id: flaky
    prompt: "Always fails"
    retry:
      max_attempts: 2
      backoff: fixed
      on_failure: abort
"""
        sb = _make_sandbox()
        sb.query = AsyncMock(side_effect=Exception("persistent error"))
        wf, plan = _parse_and_plan(yaml)
        storage = _make_storage()

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb),
            patch("sandcastle.engine.executor._backoff_delay", return_value=0),
        ):
            result = await execute_workflow(
                workflow=wf,
                plan=plan,
                input_data={},
                storage=storage,
            )
        assert result.status == "failed"
        assert sb.query.call_count == 2


# ──────────────────────────────────────────────────────────────
# 8. COST TRACKING
# ──────────────────────────────────────────────────────────────


class TestCostTracking:
    """Test cost accumulation and budget enforcement."""

    @pytest.mark.asyncio
    async def test_cost_accumulation_across_steps(self):
        """Total cost accumulates across all steps."""
        yaml = """
name: cost-track
description: Cost tracking test
steps:
  - id: step1
    prompt: "Step 1"
  - id: step2
    prompt: "Step 2"
    depends_on: [step1]
  - id: step3
    prompt: "Step 3"
    depends_on: [step2]
"""
        result, _ = await _run_workflow(
            yaml,
            sandbox_responses=["a", "b", "c"],
            sandbox_cost=0.05,
        )
        assert result.status == "completed"
        assert result.total_cost_usd == pytest.approx(0.15, abs=0.01)

    @pytest.mark.asyncio
    async def test_code_steps_zero_cost(self):
        """Code steps have zero cost."""
        yaml = """
name: code-cost
description: Code step cost test
steps:
  - id: compute
    type: code
    code_config:
      code: "result = 42"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.total_cost_usd == pytest.approx(0.0, abs=0.001)

    @pytest.mark.asyncio
    async def test_transform_steps_zero_cost(self):
        """Transform steps have zero cost."""
        yaml = """
name: transform-cost
description: Transform step cost test
steps:
  - id: transform
    type: transform
    transform_config:
      template: '{"result": "free"}'
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.total_cost_usd == pytest.approx(0.0, abs=0.001)

    @pytest.mark.asyncio
    async def test_notify_steps_zero_cost(self):
        """Notify steps have zero cost."""
        yaml = """
name: notify-cost
description: Notify step cost test
steps:
  - id: notify
    type: notify
    notify_config:
      service: slack
      channel: "#test"
      message: "test"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.total_cost_usd == pytest.approx(0.0, abs=0.001)

    @pytest.mark.asyncio
    async def test_budget_warning_at_80_percent(self):
        """Budget warning is logged at 80% usage but execution continues."""
        yaml = """
name: budget-warn
description: Budget warning test
steps:
  - id: step1
    prompt: "Step 1"
"""
        # Cost of 0.09 is 90% of 0.10 budget - should warn but not stop
        # Since the budget check happens BEFORE launching new steps (at the
        # top of the while loop), a single step that costs 0.09 completes
        # before the next budget check sees it over 80%.
        result, _ = await _run_workflow(
            yaml,
            sandbox_responses=["output"],
            sandbox_cost=0.09,
            max_cost_usd=0.10,
        )
        assert result.status == "completed"


# ──────────────────────────────────────────────────────────────
# 9. OUTPUT HANDLING
# ──────────────────────────────────────────────────────────────


class TestOutputHandling:
    """Test various output types and edge cases."""

    @pytest.mark.asyncio
    async def test_null_output_from_step(self):
        """Step returning None is handled gracefully."""
        yaml = """
name: null-output
description: Null output test
steps:
  - id: step1
    type: code
    code_config:
      code: "result = None"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs.get("step1") is None

    @pytest.mark.asyncio
    async def test_large_output_truncation(self):
        """Large outputs are truncated."""
        large = "x" * (11 * 1024 * 1024)  # 11MB
        truncated = _truncate_output(large)
        assert isinstance(truncated, str)
        assert len(truncated) < len(large)
        assert "[TRUNCATED]" in truncated

    @pytest.mark.asyncio
    async def test_dict_output_preservation(self):
        """Dict output is preserved through the pipeline."""
        yaml = """
name: dict-output
description: Dict output test
steps:
  - id: step1
    type: code
    code_config:
      code: "result = {'key': 'value', 'nested': {'a': 1}}"
  - id: step2
    type: transform
    depends_on: [step1]
    transform_config:
      template: "Got key: {steps.step1.output.key}"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs["step1"]["key"] == "value"
        assert "value" in str(result.outputs.get("step2", ""))

    @pytest.mark.asyncio
    async def test_list_output(self):
        """List output is preserved and accessible."""
        yaml = """
name: list-output
description: List output test
steps:
  - id: step1
    type: code
    code_config:
      code: "result = [1, 2, 3, 4, 5]"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs["step1"] == [1, 2, 3, 4, 5]


# ──────────────────────────────────────────────────────────────
# 10. COMPLEX MULTI-STEP WORKFLOWS
# ──────────────────────────────────────────────────────────────


class TestComplexWorkflows:
    """Test complex multi-step workflows combining multiple step types."""

    @pytest.mark.asyncio
    async def test_code_to_condition_to_transform(self):
        """Code -> Condition -> Transform pipeline."""
        yaml = """
name: complex-pipeline
description: Complex pipeline test
steps:
  - id: generate
    type: code
    code_config:
      code: "result = {'score': 85, 'items': ['a', 'b', 'c']}"
  - id: check
    type: condition
    depends_on: [generate]
    condition_config:
      expression: "int('{steps.generate.output.score}') > 50"
      then: [format_result]
      else: [low_score]
  - id: format_result
    type: transform
    depends_on: [generate]
    transform_config:
      template: '{"status": "high", "data": {steps.generate.output.score}}'
  - id: low_score
    type: transform
    transform_config:
      template: '{"status": "low"}'
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs.get("format_result") is not None
        assert result.outputs.get("low_score") is None

    @pytest.mark.asyncio
    async def test_loop_with_transform_and_notify(self):
        """Loop -> Transform -> Notify pipeline.

        Note: Loop sub-steps use execute_step_with_retry (sandbox path),
        so code steps inside loops go through the mock sandbox.
        """
        yaml = """
name: loop-transform-notify
description: Loop then transform then notify
steps:
  - id: data
    type: code
    code_config:
      code: "result = [10, 20, 30]"
  - id: looper
    type: loop
    depends_on: [data]
    loop_config:
      over: "steps.data.output"
      step_ids: [doubler]
  - id: doubler
    prompt: "Double the input item"
  - id: summarize
    type: transform
    depends_on: [looper]
    transform_config:
      template: "Loop results: {steps.looper.output}"
  - id: alert
    type: notify
    depends_on: [summarize]
    notify_config:
      service: slack
      channel: "#results"
      message: "{steps.summarize.output}"
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=["doubled-1", "doubled-2", "doubled-3"],
        )
        assert result.status == "completed"
        looper_output = result.outputs.get("looper")
        assert isinstance(looper_output, list)
        assert len(looper_output) == 3
        assert result.outputs.get("alert") is not None
        assert result.outputs["alert"]["status"] == "logged"

    @pytest.mark.asyncio
    async def test_diamond_dependency(self):
        """Diamond dependency pattern: A -> B, A -> C, B+C -> D."""
        yaml = """
name: diamond
description: Diamond dependency test
steps:
  - id: source
    type: code
    code_config:
      code: "result = 100"
  - id: branch_a
    type: code
    depends_on: [source]
    code_config:
      code: "result = _steps.get('source', 0) + 1"
  - id: branch_b
    type: code
    depends_on: [source]
    code_config:
      code: "result = _steps.get('source', 0) + 2"
  - id: merge
    type: code
    depends_on: [branch_a, branch_b]
    code_config:
      code: "result = _steps.get('branch_a', 0) + _steps.get('branch_b', 0)"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs["source"] == 100
        assert result.outputs["branch_a"] == 101
        assert result.outputs["branch_b"] == 102
        assert result.outputs["merge"] == 203

    @pytest.mark.asyncio
    async def test_conditional_notify(self):
        """Conditional notification based on processing result."""
        yaml = """
name: conditional-notify
description: Conditional notification
steps:
  - id: analyze
    type: code
    code_config:
      code: "result = {'severity': 'critical', 'message': 'System overload'}"
  - id: check_severity
    type: condition
    depends_on: [analyze]
    condition_config:
      expression: "'{steps.analyze.output.severity}' == 'critical'"
      then: [urgent_notify]
      else: [log_only]
  - id: urgent_notify
    type: notify
    depends_on: [analyze]
    notify_config:
      service: slack
      channel: "#urgent"
      message: "CRITICAL: {steps.analyze.output.message}"
  - id: log_only
    type: notify
    notify_config:
      service: slack
      channel: "#logs"
      message: "Normal event"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs.get("urgent_notify") is not None
        assert "System overload" in result.outputs["urgent_notify"]["message"]
        assert result.outputs.get("log_only") is None

    @pytest.mark.asyncio
    async def test_parallel_processing_with_merge(self):
        """Parallel steps that feed into a merge step."""
        yaml = """
name: parallel-merge
description: Parallel processing with merge
steps:
  - id: task_a
    type: code
    code_config:
      code: "result = [1, 2, 3]"
  - id: task_b
    type: code
    code_config:
      code: "result = [4, 5, 6]"
  - id: task_c
    type: code
    code_config:
      code: "result = [7, 8, 9]"
  - id: merge
    type: code
    depends_on: [task_a, task_b, task_c]
    code_config:
      code: |
        a = _steps.get('task_a', [])
        b = _steps.get('task_b', [])
        c = _steps.get('task_c', [])
        result = sorted(a + b + c)
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs["merge"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]

    @pytest.mark.asyncio
    async def test_input_schema_defaults(self):
        """Workflow input_schema defaults are merged with input_data."""
        yaml = """
name: defaults-test
description: Input schema defaults test
input_schema:
  properties:
    topic:
      type: string
      default: "default-topic"
    language:
      type: string
      default: "en"
steps:
  - id: step1
    type: code
    code_config:
      code: |
        result = {
          'topic': _input.get('topic', ''),
          'language': _input.get('language', ''),
        }
"""
        # Provide only topic, language should use default
        result, _ = await _run_workflow(
            yaml,
            input_data={"topic": "custom-topic"},
        )
        assert result.status == "completed"
        output = result.outputs["step1"]
        assert output["topic"] == "custom-topic"
        assert output["language"] == "en"


# ──────────────────────────────────────────────────────────────
# 11. STEP TYPE SPECIFIC TESTS
# ──────────────────────────────────────────────────────────────


class TestCodeStepEdgeCases:
    """Test code step edge cases."""

    @pytest.mark.asyncio
    async def test_code_step_with_json_module(self):
        """Code step has access to the json module."""
        yaml = """
name: code-json-module
description: Code step with json module
steps:
  - id: step1
    type: code
    code_config:
      code: |
        data = json.loads('{"key": "value"}')
        result = data['key']
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs["step1"] == "value"

    @pytest.mark.asyncio
    async def test_code_step_with_builtins(self):
        """Code step has access to safe builtins."""
        yaml = """
name: code-builtins
description: Code step with safe builtins
steps:
  - id: step1
    type: code
    code_config:
      code: |
        data = [3, 1, 4, 1, 5, 9]
        result = {
            'sorted': sorted(data),
            'sum': sum(data),
            'min': min(data),
            'max': max(data),
            'len': len(data),
        }
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        output = result.outputs["step1"]
        assert output["sorted"] == [1, 1, 3, 4, 5, 9]
        assert output["sum"] == 23
        assert output["min"] == 1
        assert output["max"] == 9
        assert output["len"] == 6

    @pytest.mark.asyncio
    async def test_code_step_oversized_code(self):
        """Code step rejects oversized code."""
        yaml = f"""
name: oversized-code
description: Oversized code test
steps:
  - id: step1
    type: code
    code_config:
      code: "{'x = 1\\n' * 60000}"
    retry:
      on_failure: skip
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        # The step should have failed/skipped
        assert result.outputs.get("step1") is None


class TestConditionStepEdgeCases:
    """Test condition step edge cases."""

    @pytest.mark.asyncio
    async def test_condition_expression_with_none(self):
        """Condition handles None in step outputs gracefully."""
        yaml = """
name: condition-none
description: Condition with None test
steps:
  - id: data
    type: code
    code_config:
      code: "result = None"
  - id: check
    type: condition
    depends_on: [data]
    condition_config:
      expression: "'{steps.data.output}' == 'None'"
      then: [handle_none]
      else: [handle_data]
  - id: handle_none
    type: code
    code_config:
      code: "result = 'was none'"
  - id: handle_data
    type: code
    code_config:
      code: "result = 'had data'"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs.get("handle_none") == "was none"
        assert result.outputs.get("handle_data") is None


class TestTransformStepEdgeCases:
    """Test transform step edge cases."""

    @pytest.mark.asyncio
    async def test_transform_unresolved_variable(self):
        """Transform handles unresolved variables by returning empty string."""
        yaml = """
name: transform-unresolved
description: Transform with unresolved var
steps:
  - id: transform
    type: transform
    transform_config:
      template: "Value: {{ steps.nonexistent.output }}"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        # Unresolved jinja2 vars should produce empty string
        assert result.outputs.get("transform") is not None


class TestLoopStepEdgeCases:
    """Test loop step edge cases."""

    @pytest.mark.asyncio
    async def test_loop_over_unresolved_variable(self):
        """Loop over unresolved variable produces empty list."""
        yaml = """
name: loop-unresolved
description: Loop over unresolved variable
steps:
  - id: looper
    type: loop
    loop_config:
      over: "steps.nonexistent.output"
      step_ids: [process]
  - id: process
    type: code
    code_config:
      code: "result = 'processed'"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs.get("looper") == []

    @pytest.mark.asyncio
    async def test_loop_over_single_item(self):
        """Loop over non-list item wraps in list."""
        yaml = """
name: loop-single
description: Loop over single item
steps:
  - id: data
    type: code
    code_config:
      code: "result = 'single_item'"
  - id: looper
    type: loop
    depends_on: [data]
    loop_config:
      over: "steps.data.output"
      step_ids: [process]
  - id: process
    type: code
    code_config:
      code: "result = 'got: ' + str(_input.get('_item', ''))"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        loop_output = result.outputs.get("looper")
        assert isinstance(loop_output, list)
        assert len(loop_output) == 1


# ──────────────────────────────────────────────────────────────
# 12. WORKFLOW PARSING AND VALIDATION
# ──────────────────────────────────────────────────────────────


class TestWorkflowParsing:
    """Test workflow YAML parsing and plan building."""

    def test_parse_simple_workflow(self):
        """Parse a simple workflow YAML."""
        yaml = """
name: simple
description: Simple test
steps:
  - id: step1
    prompt: "Hello"
"""
        wf = parse_yaml_string(yaml)
        assert wf.name == "simple"
        assert len(wf.steps) == 1
        assert wf.steps[0].id == "step1"

    def test_parse_all_step_types(self):
        """Parse workflow with all step types."""
        yaml = """
name: all-types
description: All step types
steps:
  - id: std
    prompt: "Standard"
    type: standard
  - id: llm
    prompt: "LLM"
    type: llm
  - id: http_step
    type: http
    http_config:
      url: "https://api.example.com"
  - id: code_step
    type: code
    code_config:
      code: "result = 1"
  - id: cond
    type: condition
    condition_config:
      expression: "True"
      then: [std]
      else: [llm]
  - id: cls
    type: classify
    classify_config:
      categories: [a, b]
      input: "text"
  - id: lp
    type: loop
    loop_config:
      over: "input.items"
      step_ids: [code_step]
  - id: rc
    type: race
    race_config:
      branches: [[std], [llm]]
  - id: tf
    type: transform
    transform_config:
      template: "hello"
  - id: nt
    type: notify
    notify_config:
      service: slack
      channel: "#test"
      message: "msg"
  - id: dl
    type: delegate
    delegate_config:
      workflow: other
      task_description: "do something"
  - id: sn
    type: sensor
    sensor_config:
      url: "https://api.example.com/status"
      condition: "status_code == 200"
  - id: gt
    type: gate
    gate_config:
      strategies:
        - type: timeout
          config:
            seconds: 0
"""
        wf = parse_yaml_string(yaml)
        assert len(wf.steps) == 13
        types = {s.id: s.type for s in wf.steps}
        assert types["std"] == "standard"
        assert types["llm"] == "llm"
        assert types["http_step"] == "http"
        assert types["code_step"] == "code"
        assert types["cond"] == "condition"
        assert types["cls"] == "classify"
        assert types["lp"] == "loop"
        assert types["rc"] == "race"
        assert types["tf"] == "transform"
        assert types["nt"] == "notify"
        assert types["dl"] == "delegate"
        assert types["sn"] == "sensor"
        assert types["gt"] == "gate"

    def test_build_plan_topological_sort(self):
        """Build plan produces correct topological sort."""
        yaml = """
name: topo-test
description: Topological sort test
steps:
  - id: a
    prompt: "A"
  - id: b
    prompt: "B"
    depends_on: [a]
  - id: c
    prompt: "C"
    depends_on: [a]
  - id: d
    prompt: "D"
    depends_on: [b, c]
"""
        wf = parse_yaml_string(yaml)
        plan = build_plan(wf)
        # Stage 0: [a], Stage 1: [b, c], Stage 2: [d]
        assert plan.stages[0] == ["a"]
        assert sorted(plan.stages[1]) == ["b", "c"]
        assert plan.stages[2] == ["d"]

    def test_parse_with_retry_config(self):
        """Parse retry configuration."""
        yaml = """
name: retry-parse
description: Retry config test
steps:
  - id: step1
    prompt: "Hello"
    retry:
      max_attempts: 5
      backoff: fixed
      on_failure: skip
"""
        wf = parse_yaml_string(yaml)
        assert wf.steps[0].retry is not None
        assert wf.steps[0].retry.max_attempts == 5
        assert wf.steps[0].retry.backoff == "fixed"
        assert wf.steps[0].retry.on_failure == "skip"

    def test_parse_empty_yaml_raises(self):
        """Empty YAML raises ValueError."""
        with pytest.raises(ValueError):
            parse_yaml_string("")

    def test_parse_missing_name_raises(self):
        """Missing name field raises ValueError."""
        with pytest.raises(ValueError, match="name"):
            parse_yaml_string("description: no name\nsteps: []")


# ──────────────────────────────────────────────────────────────
# 13. RESOLVE VARIABLE AND TEMPLATE TESTS
# ──────────────────────────────────────────────────────────────


class TestResolveVariable:
    """Test variable resolution."""

    def test_input_simple(self):
        c = RunContext(run_id="r1", input={"name": "test"})
        assert resolve_variable("input.name", c) == "test"

    def test_input_nested(self):
        c = RunContext(run_id="r1", input={"meta": {"country": "CZ"}})
        assert resolve_variable("input.meta.country", c) == "CZ"

    def test_steps_output(self):
        c = RunContext(run_id="r1", input={}, step_outputs={"s1": "hello"})
        assert resolve_variable("steps.s1.output", c) == "hello"

    def test_steps_output_field(self):
        c = RunContext(
            run_id="r1",
            input={},
            step_outputs={"s1": {"key": "value"}},
        )
        assert resolve_variable("steps.s1.output.key", c) == "value"

    def test_run_id(self):
        c = RunContext(run_id="abc-123", input={})
        assert resolve_variable("run_id", c) == "abc-123"

    def test_date(self):
        c = RunContext(run_id="r1", input={})
        result = resolve_variable("date", c)
        assert isinstance(result, str)
        assert len(result) == 10  # YYYY-MM-DD

    def test_unresolved(self):
        from sandcastle.engine.executor import _UNRESOLVED

        c = RunContext(run_id="r1", input={})
        assert resolve_variable("unknown.path", c) is _UNRESOLVED


class TestResolveTemplates:
    """Test template string resolution."""

    def test_basic_template(self):
        c = RunContext(run_id="r1", input={"name": "Alice"})
        result = resolve_templates("Hello {input.name}", c)
        assert result == "Hello Alice"

    def test_step_output_template(self):
        c = RunContext(
            run_id="r1",
            input={},
            step_outputs={"s1": "world"},
        )
        result = resolve_templates("Hello {steps.s1.output}", c)
        assert result == "Hello world"

    def test_multiple_variables(self):
        c = RunContext(
            run_id="r1",
            input={"x": "A"},
            step_outputs={"s1": "B"},
        )
        result = resolve_templates("{input.x} and {steps.s1.output}", c)
        assert result == "A and B"

    def test_auto_inject_dependency_outputs(self):
        """Unreferenced dependency outputs are auto-injected."""
        c = RunContext(
            run_id="r1",
            input={},
            step_outputs={"dep1": "data1"},
        )
        result = resolve_templates("Do something", c, depends_on=["dep1"])
        assert "data1" in result

    def test_dict_output_as_json(self):
        c = RunContext(
            run_id="r1",
            input={},
            step_outputs={"s1": {"key": "value"}},
        )
        result = resolve_templates("{steps.s1.output}", c)
        assert '"key"' in result
        assert '"value"' in result


# ──────────────────────────────────────────────────────────────
# 14. DEPTH LIMIT TESTS
# ──────────────────────────────────────────────────────────────


class TestDepthLimits:
    """Test hierarchical workflow depth limits."""

    @pytest.mark.asyncio
    async def test_max_depth_exceeded(self):
        """Workflow fails when max depth is exceeded."""
        yaml = """
name: depth-test
description: Depth limit test
steps:
  - id: step1
    prompt: "Hello"
"""
        wf, plan = _parse_and_plan(yaml)
        sb = _make_sandbox()
        storage = _make_storage()

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=wf,
                plan=plan,
                input_data={},
                storage=storage,
                depth=100,  # Exceeds default max_workflow_depth=5
            )
        assert result.status == "failed"
        assert "depth" in result.error.lower()


# ──────────────────────────────────────────────────────────────
# 15. FAN-OUT / PARALLEL_OVER TESTS
# ──────────────────────────────────────────────────────────────


class TestFanOut:
    """Test parallel_over fan-out execution."""

    @pytest.mark.asyncio
    async def test_fan_out_basic(self):
        """Fan-out over a list produces parallel results."""
        yaml = """
name: fanout-test
description: Fan-out test
steps:
  - id: data
    type: code
    code_config:
      code: "result = ['a', 'b', 'c']"
  - id: process
    prompt: "Process {input._item}"
    depends_on: [data]
    parallel_over: "{steps.data.output}"
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=["r-a", "r-b", "r-c"],
        )
        assert result.status == "completed"
        fan_out_output = result.outputs.get("process")
        assert isinstance(fan_out_output, list)
        assert len(fan_out_output) == 3

    @pytest.mark.asyncio
    async def test_fan_out_empty_list(self):
        """Fan-out over empty list produces empty results."""
        yaml = """
name: fanout-empty
description: Fan-out empty test
steps:
  - id: data
    type: code
    code_config:
      code: "result = []"
  - id: process
    prompt: "Process"
    depends_on: [data]
    parallel_over: "{steps.data.output}"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        assert result.outputs.get("process") == []


# ──────────────────────────────────────────────────────────────
# 16. WORKFLOW RESULT STRUCTURE TESTS
# ──────────────────────────────────────────────────────────────


class TestWorkflowResultStructure:
    """Test the structure and fields of WorkflowResult."""

    @pytest.mark.asyncio
    async def test_result_has_run_id(self):
        """WorkflowResult includes a run_id."""
        yaml = """
name: result-test
description: Result structure test
steps:
  - id: step1
    type: code
    code_config:
      code: "result = 1"
"""
        result, _ = await _run_workflow(yaml)
        assert result.run_id is not None
        assert len(result.run_id) > 0

    @pytest.mark.asyncio
    async def test_result_has_timestamps(self):
        """WorkflowResult includes started_at and completed_at."""
        yaml = """
name: timestamp-test
description: Timestamp test
steps:
  - id: step1
    type: code
    code_config:
      code: "result = 1"
"""
        result, _ = await _run_workflow(yaml)
        assert result.started_at is not None
        assert result.completed_at is not None
        assert result.completed_at >= result.started_at

    @pytest.mark.asyncio
    async def test_failed_result_has_error(self):
        """Failed WorkflowResult includes error message."""
        yaml = """
name: error-test
description: Error result test
steps:
  - id: step1
    type: code
    code_config:
      code: "result = 1 / 0"
    retry:
      max_attempts: 1
      on_failure: abort
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "failed"
        assert result.error is not None
        assert "division by zero" in result.error.lower()


# ──────────────────────────────────────────────────────────────
# 17. ADDITIONAL EDGE CASES
# ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test miscellaneous edge cases."""

    @pytest.mark.asyncio
    async def test_step_with_output_schema(self):
        """Step with output_schema sends format to sandbox."""
        yaml = """
name: schema-test
description: Output schema test
steps:
  - id: step1
    prompt: "Generate structured data"
    output_schema:
      type: object
      properties:
        name:
          type: string
        age:
          type: integer
"""
        result, sandbox = await _run_workflow(
            yaml,
            sandbox_responses=[{"name": "Alice", "age": 30}],
        )
        assert result.status == "completed"
        call_args = sandbox.query.call_args_list[0][0][0]
        assert "output_format" in call_args

    @pytest.mark.asyncio
    async def test_workflow_with_no_steps(self):
        """Workflow with no steps fails validation."""
        yaml = """
name: empty
description: Empty workflow
steps: []
"""
        from sandcastle.engine.dag import validate

        wf = parse_yaml_string(yaml)
        errors = validate(wf)
        assert any("at least one step" in e for e in errors)

    @pytest.mark.asyncio
    async def test_code_step_input_access(self):
        """Code step can access workflow input via _input."""
        yaml = """
name: input-access
description: Code step input access
steps:
  - id: step1
    type: code
    code_config:
      code: "result = _input.get('value', 0) * 3"
"""
        result, _ = await _run_workflow(
            yaml,
            input_data={"value": 7},
        )
        assert result.status == "completed"
        assert result.outputs["step1"] == 21

    @pytest.mark.asyncio
    async def test_multiple_conditions_same_branch(self):
        """Multiple conditions can target the same branch step."""
        yaml = """
name: multi-condition
description: Multiple conditions
steps:
  - id: check1
    type: condition
    condition_config:
      expression: "True"
      then: [shared_step]
      else: []
  - id: check2
    type: condition
    condition_config:
      expression: "False"
      then: []
      else: [shared_step]
  - id: shared_step
    type: code
    depends_on: [check1, check2]
    code_config:
      code: "result = 'ran'"
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        # shared_step should run because at least one condition adds it to branch_run_steps
        assert result.outputs.get("shared_step") == "ran"

    @pytest.mark.asyncio
    async def test_standard_step_with_tools(self):
        """Standard step with tools list passes tools to sandbox."""
        yaml = """
name: tools-test
description: Tools test
steps:
  - id: step1
    prompt: "Use tools"
    tools: [slack, jira]
"""
        result, sandbox = await _run_workflow(yaml)
        assert result.status == "completed"
        call_args = sandbox.query.call_args_list[0][0][0]
        assert call_args.get("tools") == ["slack", "jira"]

    @pytest.mark.asyncio
    async def test_workflow_default_tools(self):
        """Workflow-level default_tools are inherited by steps."""
        yaml = """
name: default-tools-test
description: Default tools test
default_tools: [github, slack]
steps:
  - id: step1
    prompt: "Use default tools"
"""
        result, sandbox = await _run_workflow(yaml)
        assert result.status == "completed"
        call_args = sandbox.query.call_args_list[0][0][0]
        assert call_args.get("tools") == ["github", "slack"]

    @pytest.mark.asyncio
    async def test_step_tools_override_defaults(self):
        """Step-level tools override workflow default_tools."""
        yaml = """
name: override-tools-test
description: Tools override test
default_tools: [github, slack]
steps:
  - id: step1
    prompt: "Use specific tools"
    tools: [jira]
"""
        result, sandbox = await _run_workflow(yaml)
        assert result.status == "completed"
        call_args = sandbox.query.call_args_list[0][0][0]
        assert call_args.get("tools") == ["jira"]

    @pytest.mark.asyncio
    async def test_code_markdown_fence_stripped(self):
        """Sandbox response with markdown code fences is parsed."""
        yaml = """
name: markdown-test
description: Markdown fence test
steps:
  - id: step1
    prompt: "Return JSON"
"""
        result, _ = await _run_workflow(
            yaml,
            sandbox_responses=['```json\n{"key": "value"}\n```'],
        )
        assert result.status == "completed"
        assert result.outputs["step1"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_replay_skip_steps(self):
        """Replay mode skips already-completed steps."""
        yaml = """
name: replay-test
description: Replay test
steps:
  - id: step1
    prompt: "Step 1"
  - id: step2
    prompt: "Step 2"
    depends_on: [step1]
"""
        wf, plan = _parse_and_plan(yaml)
        sb = _make_sandbox(["step2-output"])
        storage = _make_storage()

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=wf,
                plan=plan,
                input_data={},
                storage=storage,
                skip_steps={"step1"},
                initial_context={"step_outputs": {"step1": "step1-cached"}, "costs": [0.01]},
            )
        assert result.status == "completed"
        # step1 should use cached value, step2 should execute
        assert result.outputs["step1"] == "step1-cached"
        assert result.outputs.get("step2") is not None

    @pytest.mark.asyncio
    async def test_condition_with_empty_branches(self):
        """Condition with empty then/else branches is valid."""
        yaml = """
name: empty-branch
description: Empty branch test
steps:
  - id: check
    type: condition
    condition_config:
      expression: "True"
      then: []
      else: []
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_gate_no_matching_strategy(self):
        """Gate with no matching strategy fails."""
        yaml = """
name: gate-no-match
description: Gate no match test
steps:
  - id: gate
    type: gate
    gate_config:
      strategies: []
    retry:
      on_failure: skip
"""
        result, _ = await _run_workflow(yaml)
        assert result.status == "completed"
        # Gate should fail but on_failure=skip so workflow continues
        assert result.outputs.get("gate") is None
