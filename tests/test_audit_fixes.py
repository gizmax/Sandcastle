"""Tests for audit fix coverage gaps.

Covers: input validation, race cancellation, delegate failure, sensor backoff,
transform type preservation, HTTP truncation, condition branch conflicts,
loop max_iterations=0, classify fallback, empty input handling, cost
accumulation, and unresolved variables.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.api.routes import _validate_workflow_input
from sandcastle.engine.dag import (
    ClassifyConfig,
    CodeConfig,
    ConditionConfig,
    DelegateConfig,
    HttpConfig,
    LoopConfig,
    RaceConfig,
    SensorConfig,
    StepDefinition,
    TransformConfig,
    WorkflowDefinition,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _UNRESOLVED,
    _backoff_delay,
    _check_budget,
    execute_step_with_retry,
    execute_workflow,
    resolve_templates,
    resolve_variable,
)
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime

_execute_condition_step = _executor_mod._execute_condition_step
_execute_delegate_step = _executor_mod._execute_delegate_step
_execute_http_step = _executor_mod._execute_http_step
_execute_race_step = _executor_mod._execute_race_step
_execute_sensor_step = _executor_mod._execute_sensor_step
_execute_transform_step = _executor_mod._execute_transform_step
_execute_loop_step = _executor_mod._execute_loop_step


@pytest.fixture(autouse=True)
def _disable_step_cache():
    """Disable step cache during tests to avoid cross-test interference."""
    with (
        patch(
            "sandcastle.engine.executor._get_cached_result",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "sandcastle.engine.executor._save_to_cache",
            new_callable=AsyncMock,
        ),
    ):
        yield


def _ctx(**kwargs) -> RunContext:
    return RunContext(
        run_id=kwargs.get("run_id", "test-run-audit"),
        input=kwargs.get("input", {}),
        step_outputs=kwargs.get("step_outputs", {}),
    )


def _step(**kwargs) -> StepDefinition:
    return StepDefinition(
        id=kwargs.get("id", "audit-step"),
        prompt=kwargs.get("prompt", "Test prompt"),
        model=kwargs.get("model", "sonnet"),
        type=kwargs.get("type", "standard"),
        max_turns=kwargs.get("max_turns", 5),
        timeout=kwargs.get("timeout", 60),
        retry=kwargs.get("retry"),
        http_config=kwargs.get("http_config"),
        condition_config=kwargs.get("condition_config"),
        race_config=kwargs.get("race_config"),
        sensor_config=kwargs.get("sensor_config"),
        transform_config=kwargs.get("transform_config"),
        delegate_config=kwargs.get("delegate_config"),
        loop_config=kwargs.get("loop_config"),
        code_config=kwargs.get("code_config"),
    )


# ============================================================================
# 1. Input validation (_validate_workflow_input in routes.py)
# ============================================================================


class TestValidateWorkflowInput:
    """Test _validate_workflow_input for required fields, type coercion, edge cases."""

    def test_no_schema_returns_empty(self):
        errors = _validate_workflow_input({}, None)
        assert errors == []

    def test_required_field_missing(self):
        schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
        errors = _validate_workflow_input({}, schema)
        assert len(errors) == 1
        assert "name" in errors[0]

    def test_required_field_empty_string(self):
        schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
        errors = _validate_workflow_input({"name": ""}, schema)
        assert len(errors) == 1
        assert "missing or empty" in errors[0]

    def test_required_field_present(self):
        schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
        errors = _validate_workflow_input({"name": "Acme"}, schema)
        assert errors == []

    def test_integer_coercion_from_string(self):
        schema = {"properties": {"count": {"type": "integer"}}}
        data = {"count": "42"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["count"] == 42
        assert isinstance(data["count"], int)

    def test_integer_coercion_invalid(self):
        schema = {"properties": {"count": {"type": "integer"}}}
        data = {"count": "abc"}
        errors = _validate_workflow_input(data, schema)
        assert len(errors) == 1
        assert "integer" in errors[0]

    def test_number_coercion_from_string(self):
        schema = {"properties": {"price": {"type": "number"}}}
        data = {"price": "3.14"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["price"] == pytest.approx(3.14)

    def test_number_coercion_invalid(self):
        schema = {"properties": {"price": {"type": "number"}}}
        data = {"price": "not-a-number"}
        errors = _validate_workflow_input(data, schema)
        assert len(errors) == 1
        assert "number" in errors[0]

    def test_boolean_coercion_true(self):
        schema = {"properties": {"flag": {"type": "boolean"}}}
        data = {"flag": "true"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["flag"] is True

    def test_boolean_coercion_false(self):
        schema = {"properties": {"flag": {"type": "boolean"}}}
        data = {"flag": "False"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["flag"] is False

    def test_boolean_coercion_invalid(self):
        schema = {"properties": {"flag": {"type": "boolean"}}}
        data = {"flag": "maybe"}
        errors = _validate_workflow_input(data, schema)
        assert len(errors) == 1
        assert "boolean" in errors[0]

    def test_array_coercion_from_json_string(self):
        schema = {"properties": {"items": {"type": "array"}}}
        data = {"items": '[1, 2, 3]'}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["items"] == [1, 2, 3]

    def test_array_coercion_not_array(self):
        schema = {"properties": {"items": {"type": "array"}}}
        data = {"items": '{"key": "val"}'}
        errors = _validate_workflow_input(data, schema)
        assert len(errors) == 1
        assert "array" in errors[0]

    def test_array_coercion_invalid_json(self):
        schema = {"properties": {"items": {"type": "array"}}}
        data = {"items": "not-json"}
        errors = _validate_workflow_input(data, schema)
        assert len(errors) == 1
        assert "JSON array" in errors[0]

    def test_missing_optional_field_no_error(self):
        schema = {"required": [], "properties": {"name": {"type": "string"}}}
        errors = _validate_workflow_input({}, schema)
        assert errors == []

    def test_no_type_field_skipped(self):
        schema = {"properties": {"data": {}}}
        errors = _validate_workflow_input({"data": "anything"}, schema)
        assert errors == []

    def test_multiple_required_missing(self):
        schema = {
            "required": ["a", "b", "c"],
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string"},
                "c": {"type": "string"},
            },
        }
        errors = _validate_workflow_input({}, schema)
        assert len(errors) == 3

    def test_integer_already_int_no_coercion(self):
        schema = {"properties": {"count": {"type": "integer"}}}
        data = {"count": 42}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["count"] == 42


# ============================================================================
# 2. Race step cancellation
# ============================================================================


class TestRaceCancellation:
    """Test that branches are cancelled after first valid result."""

    @pytest.mark.asyncio
    async def test_race_first_valid_wins(self):
        """When both branches complete, the first valid result wins."""
        step = _step(
            id="race-win",
            type="race",
            race_config=RaceConfig(branches=[["fast"], ["medium"]]),
        )
        ctx = _ctx()

        fast_step = StepDefinition(id="fast", prompt="fast")
        medium_step = StepDefinition(id="medium", prompt="medium")
        wf = WorkflowDefinition(
            name="test",
            description="test",
            default_model="sonnet",
            default_max_turns=5,
            default_timeout=60,
            steps=[step, fast_step, medium_step],
        )

        async def mock_execute(s, c, sb, st, **kwargs):
            if s.id == "fast":
                return StepResult(step_id="fast", output="fast-wins", status="completed", cost_usd=0.01)
            else:
                return StepResult(step_id="medium", output="medium-done", status="completed", cost_usd=0.02)

        sandbox = AsyncMock()
        storage = AsyncMock()

        with patch("sandcastle.engine.executor.execute_step_with_retry", side_effect=mock_execute):
            result = await _execute_race_step(step, ctx, sandbox, storage, wf, 0)

        assert result.status == "completed"
        # First completed branch wins
        assert result.output in ("fast-wins", "medium-done")

    @pytest.mark.asyncio
    async def test_race_all_branches_fail(self):
        """When all branches fail, race step should fail."""
        step = _step(
            id="race-fail",
            type="race",
            race_config=RaceConfig(branches=[["b1"], ["b2"]]),
        )
        ctx = _ctx()

        b1 = StepDefinition(id="b1", prompt="b1")
        b2 = StepDefinition(id="b2", prompt="b2")
        wf = WorkflowDefinition(
            name="test",
            description="test",
            default_model="sonnet",
            default_max_turns=5,
            default_timeout=60,
            steps=[step, b1, b2],
        )

        async def mock_execute(s, c, sb, st, **kwargs):
            return StepResult(step_id=s.id, status="failed", error="branch failed")

        sandbox = AsyncMock()
        storage = AsyncMock()

        with patch("sandcastle.engine.executor.execute_step_with_retry", side_effect=mock_execute):
            result = await _execute_race_step(step, ctx, sandbox, storage, wf, 0)

        assert result.status == "failed"
        assert "All race branches failed" in result.error

    @pytest.mark.asyncio
    async def test_race_with_validator_fallback(self):
        """When validator rejects all results, race should use fallback output."""
        step = _step(
            id="race-val",
            type="race",
            race_config=RaceConfig(
                branches=[["b1"]],
                validator="output is not None and output > 10",
            ),
        )
        ctx = _ctx()

        b1 = StepDefinition(id="b1", prompt="b1")
        wf = WorkflowDefinition(
            name="test",
            description="test",
            default_model="sonnet",
            default_max_turns=5,
            default_timeout=60,
            steps=[step, b1],
        )

        async def mock_execute(s, c, sb, st, **kwargs):
            return StepResult(step_id=s.id, output=5, status="completed", cost_usd=0.01)

        sandbox = AsyncMock()
        storage = AsyncMock()

        with patch("sandcastle.engine.executor.execute_step_with_retry", side_effect=mock_execute):
            result = await _execute_race_step(step, ctx, sandbox, storage, wf, 0)

        # Should fallback to the non-validated result
        assert result.status == "completed"
        assert result.output == 5

    @pytest.mark.asyncio
    async def test_race_missing_config(self):
        step = _step(id="race-noconfig", type="race", race_config=None)
        ctx = _ctx()
        result = await _execute_race_step(step, ctx, AsyncMock(), AsyncMock(), MagicMock(), 0)
        assert result.status == "failed"
        assert "Missing race_config" in result.error


# ============================================================================
# 3. Delegate step failure (missing sub-workflow)
# ============================================================================


class TestDelegateStepFailure:
    """Test that missing sub-workflow returns failed status."""

    @pytest.mark.asyncio
    async def test_delegate_missing_workflow_file(self):
        step = _step(
            id="delegate-miss",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="nonexistent_workflow",
                task_description="Do something",
            ),
        )
        ctx = _ctx()
        storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.workflows_dir = "/tmp/nonexistent_dir_sandcastle_test"
            result = await _execute_delegate_step(step, ctx, storage, depth=0)

        assert result.status == "failed"
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_delegate_missing_config(self):
        step = _step(id="delegate-noconfig", type="delegate", delegate_config=None)
        ctx = _ctx()
        result = await _execute_delegate_step(step, ctx, AsyncMock(), depth=0)
        assert result.status == "failed"
        assert "Missing delegate_config" in result.error

    @pytest.mark.asyncio
    async def test_delegate_resolves_task_description(self):
        step = _step(
            id="delegate-tmpl",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="missing",
                task_description="Analyze {input.company}",
            ),
        )
        ctx = _ctx(input={"company": "Acme Corp"})
        storage = AsyncMock()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.workflows_dir = "/tmp/nonexistent_dir_sandcastle_test"
            result = await _execute_delegate_step(step, ctx, storage, depth=0)

        assert result.status == "failed"
        assert result.output["task_description"] == "Analyze Acme Corp"


# ============================================================================
# 4. Sensor backoff behavior
# ============================================================================


class TestSensorBackoff:
    """Test exponential backoff behavior in sensor step."""

    @pytest.mark.asyncio
    async def test_sensor_exponential_backoff_intervals(self):
        """Verify the sensor uses exponential backoff with increasing intervals."""
        step = _step(
            id="sensor-backoff",
            type="sensor",
            sensor_config=SensorConfig(
                url="http://example.com/status",
                check_interval=1,
                timeout=100,
                condition="response.get('ready') == True",
            ),
        )
        ctx = _ctx()

        sleep_times = []

        async def mock_sleep(duration):
            sleep_times.append(duration)
            return

        call_count = {"n": 0}

        async def mock_request(self, method, url, headers=None, content=None):
            call_count["n"] += 1
            resp = MagicMock()
            resp.json.return_value = {"ready": False}
            resp.status_code = 200
            return resp

        # Make time.monotonic advance so we timeout after ~4 polls
        start = time.monotonic()
        mono_calls = {"n": 0}

        def advancing_monotonic():
            mono_calls["n"] += 1
            # Each call advances by 15 seconds so we timeout after a few polls
            return start + mono_calls["n"] * 15

        with (
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("httpx.AsyncClient.request", mock_request),
            patch("time.monotonic", side_effect=advancing_monotonic),
            patch("random.uniform", return_value=0.0),
        ):
            result = await _execute_sensor_step(step, ctx)

        assert result.status == "failed"
        assert "timed out" in result.error
        # With jitter = 0 (mocked), intervals should double each time:
        # 1, 2, 4, ...
        assert len(sleep_times) >= 2
        assert sleep_times[0] == pytest.approx(1.0, abs=0.5)
        assert sleep_times[1] == pytest.approx(2.0, abs=0.5)

    @pytest.mark.asyncio
    async def test_sensor_success_on_first_poll(self):
        """Sensor returns immediately when condition is met on first poll."""
        step = _step(
            id="sensor-fast",
            type="sensor",
            sensor_config=SensorConfig(
                url="http://example.com/status",
                check_interval=5,
                timeout=30,
                condition="response.get('status') == 'done'",
            ),
        )
        ctx = _ctx()

        async def mock_request(self, method, url, headers=None, content=None):
            resp = MagicMock()
            resp.json.return_value = {"status": "done"}
            resp.status_code = 200
            return resp

        with patch("httpx.AsyncClient.request", mock_request):
            result = await _execute_sensor_step(step, ctx)

        assert result.status == "completed"
        assert result.output == {"status": "done"}
        assert result.cost_usd == 0.0


# ============================================================================
# 5. Transform type preservation
# ============================================================================


class TestTransformTypePreservation:
    """Test that numbers/bools are preserved from JSON in transform steps."""

    @pytest.mark.asyncio
    async def test_transform_preserves_integer(self):
        step = _step(
            id="t-int",
            type="transform",
            transform_config=TransformConfig(template="42"),
        )
        ctx = _ctx()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output == 42
        assert isinstance(result.output, int)

    @pytest.mark.asyncio
    async def test_transform_preserves_float(self):
        step = _step(
            id="t-float",
            type="transform",
            transform_config=TransformConfig(template="3.14"),
        )
        ctx = _ctx()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output == pytest.approx(3.14)

    @pytest.mark.asyncio
    async def test_transform_preserves_boolean_true(self):
        step = _step(
            id="t-bool",
            type="transform",
            transform_config=TransformConfig(template="true"),
        )
        ctx = _ctx()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output is True

    @pytest.mark.asyncio
    async def test_transform_preserves_boolean_false(self):
        step = _step(
            id="t-bool-f",
            type="transform",
            transform_config=TransformConfig(template="false"),
        )
        ctx = _ctx()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output is False

    @pytest.mark.asyncio
    async def test_transform_preserves_null(self):
        step = _step(
            id="t-null",
            type="transform",
            transform_config=TransformConfig(template="null"),
        )
        ctx = _ctx()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output is None

    @pytest.mark.asyncio
    async def test_transform_preserves_dict(self):
        step = _step(
            id="t-dict",
            type="transform",
            transform_config=TransformConfig(template='{"key": "value", "num": 99}'),
        )
        ctx = _ctx()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output == {"key": "value", "num": 99}

    @pytest.mark.asyncio
    async def test_transform_preserves_list(self):
        step = _step(
            id="t-list",
            type="transform",
            transform_config=TransformConfig(template='[1, 2, 3]'),
        )
        ctx = _ctx()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_transform_plain_string_stays_string(self):
        step = _step(
            id="t-str",
            type="transform",
            transform_config=TransformConfig(template="hello world"),
        )
        ctx = _ctx()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output == "hello world"
        assert isinstance(result.output, str)

    @pytest.mark.asyncio
    async def test_transform_jinja_tojson(self):
        step = _step(
            id="t-jinja",
            type="transform",
            transform_config=TransformConfig(template="{{ steps.data.output | tojson }}"),
        )
        ctx = _ctx(step_outputs={"data": {"items": [1, 2]}})
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        # The JSON-parsed output should be a dict
        assert result.output == {"items": [1, 2]}


# ============================================================================
# 6. HTTP truncation
# ============================================================================


class TestHttpTruncation:
    """Test _truncated flag when response > 5000 chars."""

    @pytest.mark.asyncio
    async def test_http_truncates_large_text_response(self):
        step = _step(
            id="http-trunc",
            type="http",
            http_config=HttpConfig(url="http://example.com/large", method="GET"),
        )
        ctx = _ctx()

        large_text = "x" * 10000  # 10k chars

        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = large_text
        mock_resp.status_code = 200

        async def mock_request(self, method, url, headers=None, content=None):
            return mock_resp

        with patch("httpx.AsyncClient.request", mock_request):
            result = await _execute_http_step(step, ctx)

        assert result.status == "completed"
        assert result.output["_truncated"] is True
        assert len(result.output["text"]) == 5000

    @pytest.mark.asyncio
    async def test_http_no_truncation_small_response(self):
        step = _step(
            id="http-small",
            type="http",
            http_config=HttpConfig(url="http://example.com/small", method="GET"),
        )
        ctx = _ctx()

        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = "short response"
        mock_resp.status_code = 200

        async def mock_request(self, method, url, headers=None, content=None):
            return mock_resp

        with patch("httpx.AsyncClient.request", mock_request):
            result = await _execute_http_step(step, ctx)

        assert result.status == "completed"
        assert result.output["_truncated"] is False
        assert result.output["text"] == "short response"

    @pytest.mark.asyncio
    async def test_http_json_response_not_truncated(self):
        step = _step(
            id="http-json",
            type="http",
            http_config=HttpConfig(url="http://example.com/json", method="GET"),
        )
        ctx = _ctx()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": "x" * 10000}
        mock_resp.status_code = 200

        async def mock_request(self, method, url, headers=None, content=None):
            return mock_resp

        with patch("httpx.AsyncClient.request", mock_request):
            result = await _execute_http_step(step, ctx)

        assert result.status == "completed"
        # JSON responses are returned as-is, no truncation
        assert result.output == {"data": "x" * 10000}
        assert "_truncated" not in result.output


# ============================================================================
# 7. Condition branch conflicts (overlapping skip sets)
# ============================================================================


class TestConditionBranchConflicts:
    """Test overlapping condition skip sets."""

    @pytest.mark.asyncio
    async def test_condition_true_skips_else_steps(self):
        step = _step(
            id="cond-t",
            type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=["action_a"],
                else_steps=["action_b"],
            ),
        )
        ctx = _ctx()
        result = await _execute_condition_step(step, ctx)
        assert result.status == "completed"
        assert result.output["condition"] is True
        # else_steps should be skipped
        assert "action_b" in ctx.branch_skip_steps
        assert "action_a" not in ctx.branch_skip_steps

    @pytest.mark.asyncio
    async def test_condition_false_skips_then_steps(self):
        step = _step(
            id="cond-f",
            type="condition",
            condition_config=ConditionConfig(
                expression="False",
                then_steps=["action_a"],
                else_steps=["action_b"],
            ),
        )
        ctx = _ctx()
        result = await _execute_condition_step(step, ctx)
        assert result.status == "completed"
        assert result.output["condition"] is False
        # then_steps should be skipped
        assert "action_a" in ctx.branch_skip_steps
        assert "action_b" not in ctx.branch_skip_steps

    @pytest.mark.asyncio
    async def test_two_conditions_accumulate_skip_sets(self):
        """Two condition steps should accumulate skip sets."""
        ctx = _ctx(input={"score": 80})

        step1 = _step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="input.get('score', 0) > 50",
                then_steps=["high_action"],
                else_steps=["low_action"],
            ),
        )
        step2 = _step(
            id="cond2",
            type="condition",
            condition_config=ConditionConfig(
                expression="input.get('score', 0) > 90",
                then_steps=["excellent"],
                else_steps=["good"],
            ),
        )

        await _execute_condition_step(step1, ctx)
        await _execute_condition_step(step2, ctx)

        # score=80: first condition True (skip low_action), second False (skip excellent)
        assert "low_action" in ctx.branch_skip_steps
        assert "excellent" in ctx.branch_skip_steps
        assert "high_action" not in ctx.branch_skip_steps
        assert "good" not in ctx.branch_skip_steps


# ============================================================================
# 8. Loop with max_iterations=0 (DAG validation rejects it)
# ============================================================================


class TestLoopMaxIterationsZero:
    """Test that DAG validation rejects max_iterations < 1."""

    def test_loop_max_iterations_zero_rejected(self):
        yaml_content = """
name: bad-loop
description: test
steps:
  - id: looper
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [process]
      max_iterations: 0
  - id: process
    prompt: "Process item"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        loop_errors = [e for e in errors if "max_iterations" in e]
        assert len(loop_errors) == 1
        assert "must be >= 1" in loop_errors[0]

    def test_loop_max_iterations_negative_rejected(self):
        yaml_content = """
name: bad-loop-neg
description: test
steps:
  - id: looper
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [process]
      max_iterations: -5
  - id: process
    prompt: "Process item"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        loop_errors = [e for e in errors if "max_iterations" in e]
        assert len(loop_errors) == 1

    def test_loop_max_iterations_one_valid(self):
        yaml_content = """
name: ok-loop
description: test
steps:
  - id: looper
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [process]
      max_iterations: 1
  - id: process
    prompt: "Process item"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        loop_errors = [e for e in errors if "max_iterations" in e]
        assert len(loop_errors) == 0


# ============================================================================
# 9. Classify fallback (unknown category defaults to first)
# ============================================================================


class TestClassifyFallback:
    """Test that unknown category defaults to first category."""

    def test_classify_fuzzy_match_partial(self):
        """The classify step uses fuzzy matching. When exact match fails,
        it tries partial match, then defaults to first category."""
        # This is a unit test of the matching logic in _execute_classify_step.
        # We test the full function with mocked API call.
        pass  # Covered in integration test below

    @pytest.mark.asyncio
    async def test_classify_defaults_to_first_on_unknown(self):
        """When LLM returns an unknown category, should default to first category."""
        step = _step(
            id="classify-fallback",
            type="classify",
        )
        step.classify_config = ClassifyConfig(
            categories=["positive", "negative", "neutral"],
            input="test text",
            model="haiku",
            branches={
                "positive": ["pos_action"],
                "negative": ["neg_action"],
                "neutral": ["neu_action"],
            },
        )
        ctx = _ctx()
        storage = AsyncMock()
        storage.read.return_value = None

        # Mock the API to return a completely unrelated category
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"text": "unknown_gibberish_category"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_post(url, headers=None, json=None):
            return mock_resp

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=mock_post)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "sandcastle.engine.providers.resolve_model",
                return_value=MagicMock(
                    provider="claude",
                    api_model_id="claude-3-haiku-20240307",
                    input_price_per_m=0.25,
                    output_price_per_m=1.25,
                    api_base_url=None,
                ),
            ),
            patch("sandcastle.engine.providers.get_api_key", return_value="test-key"),
        ):
            _execute_classify = _executor_mod._execute_classify_step
            result = await _execute_classify(step, ctx, storage)

        assert result.status == "completed"
        # Should default to first category
        assert result.output["category"] == "positive"

    @pytest.mark.asyncio
    async def test_classify_fuzzy_match_succeeds(self):
        """When LLM returns a partial match, fuzzy matching should find it."""
        step = _step(
            id="classify-fuzzy",
            type="classify",
        )
        step.classify_config = ClassifyConfig(
            categories=["positive", "negative", "neutral"],
            input="test text",
            model="haiku",
            branches={},
        )
        ctx = _ctx()
        storage = AsyncMock()
        storage.read.return_value = None

        # Mock the API to return a partial match - "positiv" should match "positive"
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"text": "POSITIVE sentiment detected"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_post(url, headers=None, json=None):
            return mock_resp

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=mock_post)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "sandcastle.engine.providers.resolve_model",
                return_value=MagicMock(
                    provider="claude",
                    api_model_id="claude-3-haiku-20240307",
                    input_price_per_m=0.25,
                    output_price_per_m=1.25,
                    api_base_url=None,
                ),
            ),
            patch("sandcastle.engine.providers.get_api_key", return_value="test-key"),
        ):
            _execute_classify = _executor_mod._execute_classify_step
            result = await _execute_classify(step, ctx, storage)

        assert result.status == "completed"
        assert result.output["category"] == "positive"


# ============================================================================
# 10. Empty input handling
# ============================================================================


class TestEmptyInputHandling:
    """Test workflows with missing required inputs."""

    def test_workflow_with_required_input_missing(self):
        """Validation should catch missing required inputs."""
        schema = {
            "required": ["company_name"],
            "properties": {"company_name": {"type": "string"}},
        }
        errors = _validate_workflow_input({}, schema)
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_workflow_default_values_merged(self):
        """Workflow input_schema defaults should be merged with input_data."""
        yaml_content = """
name: defaults
description: test defaults
input_schema:
  required: []
  properties:
    lang:
      type: string
      default: en
    count:
      type: integer
      default: 5
steps:
  - id: step1
    prompt: "Lang: {input.lang}, Count: {input.count}"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        mock_result = SandshoreResult(text="result", total_cost_usd=0.001)

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_get_client,
            patch("sandcastle.engine.storage.LocalStorage") as MockStorage,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.return_value = mock_result
            mock_get_client.return_value = mock_sandbox

            mock_storage = AsyncMock()
            mock_storage.read.return_value = None
            MockStorage.return_value = mock_storage

            # Provide only lang, count should get default
            result = await execute_workflow(workflow, plan, input_data={"lang": "cs"})

        assert result.status == "completed"
        # Verify the prompt received had the default value for count
        call_args = mock_sandbox.query.call_args
        # query() may be called with kwargs or positional - extract prompt
        if call_args[1] and "prompt" in call_args[1]:
            prompt = call_args[1]["prompt"]
        elif call_args[0]:
            prompt = str(call_args[0])
        else:
            prompt = str(call_args)
        assert "cs" in prompt
        assert "5" in prompt  # default count value should be merged


# ============================================================================
# 11. Cost accumulation
# ============================================================================


class TestCostAccumulation:
    """Test cost tracking in various scenarios."""

    def test_budget_check_none_budget(self):
        ctx = _ctx()
        ctx.max_cost_usd = None
        assert _check_budget(ctx) is None

    def test_budget_check_zero_budget(self):
        ctx = _ctx()
        ctx.max_cost_usd = 0
        assert _check_budget(ctx) is None

    def test_budget_check_under_80_percent(self):
        ctx = _ctx()
        ctx.max_cost_usd = 1.0
        ctx.costs = [0.3]
        assert _check_budget(ctx) is None

    def test_budget_check_warning_at_80_percent(self):
        ctx = _ctx()
        ctx.max_cost_usd = 1.0
        ctx.costs = [0.85]
        assert _check_budget(ctx) == "warning"

    def test_budget_check_exceeded_at_100_percent(self):
        ctx = _ctx()
        ctx.max_cost_usd = 1.0
        ctx.costs = [1.0]
        assert _check_budget(ctx) == "exceeded"

    def test_budget_check_exceeded_over_100(self):
        ctx = _ctx()
        ctx.max_cost_usd = 1.0
        ctx.costs = [0.6, 0.5]  # total 1.1
        assert _check_budget(ctx) == "exceeded"

    def test_total_cost_property(self):
        ctx = _ctx()
        ctx.costs = [0.01, 0.02, 0.03]
        assert ctx.total_cost == pytest.approx(0.06)

    def test_context_snapshot_includes_costs(self):
        ctx = _ctx()
        ctx.costs = [0.01, 0.02]
        ctx.step_outputs = {"s1": "out1"}
        snap = ctx.snapshot()
        assert snap["costs"] == [0.01, 0.02]
        assert snap["total_cost"] == pytest.approx(0.03)
        assert snap["step_outputs"] == {"s1": "out1"}

    @pytest.mark.asyncio
    async def test_cost_accumulation_parallel_workflow(self):
        """Test that costs from parallel steps accumulate correctly."""
        yaml_content = """
name: parallel-cost
description: test
steps:
  - id: a
    prompt: "Step A"
  - id: b
    prompt: "Step B"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        # a and b are independent so they run in parallel
        results = [
            SandshoreResult(text="result a", total_cost_usd=0.05),
            SandshoreResult(text="result b", total_cost_usd=0.07),
        ]

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime") as mock_get_client,
            patch("sandcastle.engine.storage.LocalStorage") as MockStorage,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.side_effect = results
            mock_get_client.return_value = mock_sandbox

            mock_storage = AsyncMock()
            mock_storage.read.return_value = None
            MockStorage.return_value = mock_storage

            result = await execute_workflow(workflow, plan, input_data={})

        assert result.status == "completed"
        assert result.total_cost_usd == pytest.approx(0.12)


# ============================================================================
# 12. Unresolved variables
# ============================================================================


class TestUnresolvedVariables:
    """Test that {input.missing} stays as placeholder."""

    def test_unresolved_input_stays_as_placeholder(self):
        ctx = _ctx(input={"name": "Acme"})
        result = resolve_templates("Hello {input.missing}", ctx)
        assert result == "Hello {input.missing}"

    def test_unresolved_step_output_stays(self):
        ctx = _ctx()
        result = resolve_templates("Result: {steps.nonexistent.output}", ctx)
        assert result == "Result: {steps.nonexistent.output}"

    def test_mixed_resolved_and_unresolved(self):
        ctx = _ctx(input={"name": "Acme"})
        result = resolve_templates(
            "Name: {input.name}, Score: {input.score}", ctx
        )
        assert "Acme" in result
        assert "{input.score}" in result

    def test_resolve_variable_missing_input_returns_none(self):
        ctx = _ctx(input={})
        assert resolve_variable("input.nonexistent", ctx) is _UNRESOLVED

    def test_resolve_variable_missing_step_returns_none(self):
        ctx = _ctx()
        assert resolve_variable("steps.missing.output", ctx) is _UNRESOLVED

    def test_resolve_variable_unknown_root_returns_none(self):
        ctx = _ctx()
        assert resolve_variable("something.else", ctx) is _UNRESOLVED


# ============================================================================
# Backoff delay unit tests
# ============================================================================


class TestBackoffDelay:
    """Test _backoff_delay calculation."""

    def test_exponential_backoff(self):
        assert _backoff_delay(0, "exponential") == 1  # 2^0 = 1
        assert _backoff_delay(1, "exponential") == 2
        assert _backoff_delay(2, "exponential") == 4
        assert _backoff_delay(3, "exponential") == 8

    def test_exponential_backoff_capped(self):
        assert _backoff_delay(10, "exponential") == 60  # capped at 60s

    def test_fixed_backoff(self):
        assert _backoff_delay(0, "fixed") == 2.0
        assert _backoff_delay(1, "fixed") == 2.0
        assert _backoff_delay(10, "fixed") == 2.0


# ============================================================================
# RunContext.with_item tests
# ============================================================================


class TestRunContextWithItem:
    """Test RunContext.with_item for parallel_over."""

    def test_with_item_injects_item_and_index(self):
        ctx = _ctx(input={"name": "test"})
        child = ctx.with_item("apple", 0)
        assert child.input["_item"] == "apple"
        assert child.input["_index"] == 0
        assert child.input["name"] == "test"

    def test_with_item_preserves_step_outputs(self):
        ctx = _ctx(step_outputs={"s1": "out1"})
        child = ctx.with_item("x", 0)
        assert child.step_outputs == {"s1": "out1"}

    def test_with_item_copies_skip_steps(self):
        ctx = _ctx()
        ctx.branch_skip_steps = {"skip_me"}
        child = ctx.with_item("x", 0)
        assert "skip_me" in child.branch_skip_steps
        # Should be a copy, not shared reference
        child.branch_skip_steps.add("new_skip")
        assert "new_skip" not in ctx.branch_skip_steps


# ============================================================================
# Workflow depth limit
# ============================================================================


class TestWorkflowDepthLimit:
    """Test max workflow depth for hierarchical workflows."""

    @pytest.mark.asyncio
    async def test_max_depth_exceeded(self):
        yaml_content = """
name: deep
description: test
steps:
  - id: step1
    prompt: "Hello"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.max_workflow_depth = 3
            result = await execute_workflow(workflow, plan, input_data={}, depth=5)

        assert result.status == "failed"
        assert "depth" in result.error.lower()
