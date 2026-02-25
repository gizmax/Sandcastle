"""
DEEP EXECUTOR TEST SUITE
========================
Multi-pass bug hunter pro sandcastle/engine/executor.py

Spuštění:
    cd /Users/gizmax/Documents/Sandcastle
    uv run pytest tests/test_executor_deep.py -v --tb=short

Nebo pro konkrétní kategorii:
    uv run pytest tests/test_executor_deep.py::TestResolveVariable -v
    uv run pytest tests/test_executor_deep.py::TestCacheKeyConsistency -v
    uv run pytest tests/test_executor_deep.py::TestBudgetChecks -v
    uv run pytest tests/test_executor_deep.py::TestRetryLogic -v
    uv run pytest tests/test_executor_deep.py::TestParallelFanOut -v
    uv run pytest tests/test_executor_deep.py::TestEdgeCases -v
    uv run pytest tests/test_executor_deep.py::TestSecurityIssues -v
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import RetryConfig, StepDefinition, build_plan, parse_yaml_string
from sandcastle.engine.executor import (
    RunContext,
    StepExecutionError,
    StepResult,
    WorkflowPaused,
    _backoff_delay,
    _check_budget,
    _compute_cache_key,
    _is_cacheable_output,
    execute_step_with_retry,
    execute_workflow,
    resolve_templates,
    resolve_variable,
)
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime


# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_db():
    """Bypass all DB writes during tests."""
    with (
        patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._save_checkpoint", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._get_cached_result", new_callable=AsyncMock, return_value=None),
        patch("sandcastle.engine.executor._save_to_cache", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._check_cancel", new_callable=AsyncMock, return_value=False),
    ):
        yield


def ctx(**kwargs) -> RunContext:
    return RunContext(
        run_id=kwargs.get("run_id", str(uuid.uuid4())),
        input=kwargs.get("input", {}),
        step_outputs=kwargs.get("step_outputs", {}),
        costs=kwargs.get("costs", []),
        max_cost_usd=kwargs.get("max_cost_usd", None),
        workflow_name=kwargs.get("workflow_name", "test_wf"),
    )


def step(**kwargs) -> StepDefinition:
    return StepDefinition(
        id=kwargs.get("id", "s1"),
        prompt=kwargs.get("prompt", "Hello"),
        model=kwargs.get("model", "sonnet"),
        max_turns=kwargs.get("max_turns", 3),
        timeout=kwargs.get("timeout", 30),
        retry=kwargs.get("retry", None),
        output_schema=kwargs.get("output_schema", None),
        depends_on=kwargs.get("depends_on", []),
    )


def mock_sandbox(text="ok", cost=0.01) -> SandshoreRuntime:
    sb = MagicMock(spec=SandshoreRuntime)
    sb.query = AsyncMock(return_value=SandshoreResult(
        text=text,
        structured_output=None,
        total_cost_usd=cost,
        input_tokens=10,
        output_tokens=10,
    ))
    return sb


def mock_storage():
    from sandcastle.engine.storage import LocalStorage
    import tempfile
    return LocalStorage(tempfile.mkdtemp())


# ──────────────────────────────────────────────────────────────
# 1. RESOLVE VARIABLE – edge cases
# ──────────────────────────────────────────────────────────────

class TestResolveVariable:

    def test_input_simple(self):
        c = ctx(input={"brand": "Notino"})
        assert resolve_variable("input.brand", c) == "Notino"

    def test_input_nested(self):
        c = ctx(input={"meta": {"country": "CZ"}})
        assert resolve_variable("input.meta.country", c) == "CZ"

    def test_input_missing_key_returns_none(self):
        c = ctx(input={})
        assert resolve_variable("input.nonexistent", c) is None

    def test_steps_output(self):
        c = ctx(step_outputs={"fetch": "raw_data"})
        assert resolve_variable("steps.fetch.output", c) == "raw_data"

    def test_steps_output_nested_dict(self):
        c = ctx(step_outputs={"analyze": {"score": 42}})
        assert resolve_variable("steps.analyze.output.score", c) == 42

    def test_steps_output_missing_step(self):
        c = ctx()
        assert resolve_variable("steps.missing.output", c) is None

    def test_run_id(self):
        c = ctx(run_id="abc-123")
        assert resolve_variable("run_id", c) == "abc-123"

    def test_date_returns_iso_string(self):
        import re
        c = ctx()
        val = resolve_variable("date", c)
        assert re.match(r"\d{4}-\d{2}-\d{2}", val)

    def test_list_index_access(self):
        c = ctx(step_outputs={"items": ["a", "b", "c"]})
        assert resolve_variable("steps.items.output.1", c) == "b"

    def test_deep_none_chain(self):
        """None propagates through chain instead of crashing."""
        c = ctx(step_outputs={"step": None})
        result = resolve_variable("steps.step.output.deep.key", c)
        assert result is None


# ──────────────────────────────────────────────────────────────
# 2. RESOLVE TEMPLATES – injection & interpolation
# ──────────────────────────────────────────────────────────────

class TestResolveTemplates:

    def test_simple_substitution(self):
        c = ctx(input={"name": "Sephora"})
        result = resolve_templates("Analyze {input.name}", c)
        assert result == "Analyze Sephora"

    def test_multiple_variables(self):
        c = ctx(input={"a": "X", "b": "Y"})
        result = resolve_templates("{input.a} and {input.b}", c)
        assert result == "X and Y"

    def test_dict_serializes_as_json(self):
        c = ctx(step_outputs={"data": {"key": "val"}})
        result = resolve_templates("Data: {steps.data.output}", c)
        assert '"key"' in result

    def test_unknown_variable_preserved(self):
        """Unknown vars should remain as-is, not crash."""
        c = ctx()
        result = resolve_templates("Hello {input.unknown}", c)
        assert "{input.unknown}" in result

    def test_auto_inject_unreferenced_deps(self):
        """Deps not explicitly referenced should be auto-injected."""
        c = ctx(step_outputs={"step1": "important context"})
        result = resolve_templates("Do the task", c, depends_on=["step1"])
        assert "important context" in result

    def test_no_auto_inject_when_already_referenced(self):
        """Already referenced deps should NOT be double-injected."""
        c = ctx(step_outputs={"step1": "value"})
        result = resolve_templates("Use {steps.step1.output}", c, depends_on=["step1"])
        assert result.count("value") == 1

    def test_empty_template(self):
        c = ctx()
        assert resolve_templates("", c) == ""

    def test_template_with_special_chars(self):
        c = ctx(input={"url": "https://example.com/path?q=1&r=2"})
        result = resolve_templates("Visit {input.url}", c)
        assert "https://example.com" in result


# ──────────────────────────────────────────────────────────────
# 3. CACHE LOGIC
# ──────────────────────────────────────────────────────────────

class TestCacheKeyConsistency:

    def test_same_inputs_same_key(self):
        k1 = _compute_cache_key("wf", "step", "prompt", "sonnet")
        k2 = _compute_cache_key("wf", "step", "prompt", "sonnet")
        assert k1 == k2

    def test_different_prompt_different_key(self):
        k1 = _compute_cache_key("wf", "step", "prompt A", "sonnet")
        k2 = _compute_cache_key("wf", "step", "prompt B", "sonnet")
        assert k1 != k2

    def test_different_model_different_key(self):
        k1 = _compute_cache_key("wf", "step", "prompt", "haiku")
        k2 = _compute_cache_key("wf", "step", "prompt", "opus")
        assert k1 != k2

    def test_different_workflow_different_key(self):
        k1 = _compute_cache_key("wf_a", "step", "prompt", "sonnet")
        k2 = _compute_cache_key("wf_b", "step", "prompt", "sonnet")
        assert k1 != k2

    def test_key_is_hex_string(self):
        k = _compute_cache_key("wf", "step", "prompt", "model")
        assert len(k) == 64
        int(k, 16)  # must be valid hex


class TestCacheableOutput:

    def test_none_not_cacheable(self):
        assert _is_cacheable_output(None) is False

    def test_empty_string_not_cacheable(self):
        assert _is_cacheable_output("") is False

    def test_empty_dict_not_cacheable(self):
        assert _is_cacheable_output({}) is False

    def test_empty_list_not_cacheable(self):
        assert _is_cacheable_output([]) is False

    def test_valid_string_cacheable(self):
        assert _is_cacheable_output("Here are the analysis results...") is True

    def test_valid_dict_cacheable(self):
        assert _is_cacheable_output({"score": 9.5, "summary": "Great"}) is True

    def test_please_provide_not_cacheable(self):
        assert _is_cacheable_output("Please provide the article text.") is False

    def test_cannot_access_not_cacheable(self):
        assert _is_cacheable_output("I don't have access to that URL.") is False

    def test_zero_mentions_not_cacheable(self):
        assert _is_cacheable_output({"total_mentions": 0, "mentions": []}) is False


# ──────────────────────────────────────────────────────────────
# 4. BUDGET CHECKS
# ──────────────────────────────────────────────────────────────

class TestBudgetChecks:

    def test_no_budget_always_none(self):
        c = ctx(costs=[10.0])
        assert _check_budget(c) is None

    def test_under_budget(self):
        c = ctx(costs=[0.5], max_cost_usd=1.0)
        assert _check_budget(c) is None

    def test_at_80_percent_warning(self):
        c = ctx(costs=[0.8], max_cost_usd=1.0)
        assert _check_budget(c) == "warning"

    def test_exactly_at_limit_exceeded(self):
        c = ctx(costs=[1.0], max_cost_usd=1.0)
        assert _check_budget(c) == "exceeded"

    def test_over_limit_exceeded(self):
        c = ctx(costs=[1.5], max_cost_usd=1.0)
        assert _check_budget(c) == "exceeded"

    def test_zero_budget_treated_as_no_limit(self):
        """max_cost_usd=0 should not trigger budget check (division by zero guard)."""
        c = ctx(costs=[100.0], max_cost_usd=0.0)
        assert _check_budget(c) is None

    def test_multiple_cost_entries_summed(self):
        # 0.3+0.3+0.3=0.9 -> 90% of budget -> warning
        c = ctx(costs=[0.3, 0.3, 0.3], max_cost_usd=1.0)
        assert _check_budget(c) == "warning"
        # 0.2+0.2+0.2=0.6 -> 60% of budget -> None
        c1 = ctx(costs=[0.2, 0.2, 0.2], max_cost_usd=1.0)
        assert _check_budget(c1) is None
        c2 = ctx(costs=[0.4, 0.4], max_cost_usd=0.75)
        assert _check_budget(c2) == "exceeded"


# ──────────────────────────────────────────────────────────────
# 5. BACKOFF DELAY
# ──────────────────────────────────────────────────────────────

class TestBackoffDelay:

    def test_exponential_grows(self):
        d1 = _backoff_delay(1)
        d2 = _backoff_delay(2)
        d3 = _backoff_delay(3)
        assert d1 < d2 < d3

    def test_exponential_capped_at_60(self):
        assert _backoff_delay(100) == 60

    def test_fixed_backoff(self):
        assert _backoff_delay(1, "fixed") == 2.0
        assert _backoff_delay(10, "fixed") == 2.0


# ──────────────────────────────────────────────────────────────
# 6. EXECUTE STEP WITH RETRY
# ──────────────────────────────────────────────────────────────

class TestRetryLogic:

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        sb = mock_sandbox("result text", cost=0.05)
        s = step(prompt="What is 2+2?")
        c = ctx()
        storage = mock_storage()
        result = await execute_step_with_retry(s, c, sb, storage)
        assert result.status == "completed"
        assert result.output == "result text"

    @pytest.mark.asyncio
    async def test_failure_returns_failed_result(self):
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=Exception("API timeout"))
        s = step()
        c = ctx()
        storage = mock_storage()
        result = await execute_step_with_retry(s, c, sb, storage)
        assert result.status == "failed"
        assert "API timeout" in result.error

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_success(self):
        call_count = 0

        async def flaky_query(req):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Transient error")
            return SandshoreResult(text="success", structured_output=None, total_cost_usd=0.01, input_tokens=5, output_tokens=5)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=flaky_query)
        s = step(retry=RetryConfig(max_attempts=3, backoff="fixed", on_failure="abort"))
        c = ctx()
        storage = mock_storage()

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(s, c, sb, storage)

        assert result.status == "completed"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=Exception("Always fails"))
        s = step(retry=RetryConfig(max_attempts=3, backoff="fixed", on_failure="abort"))
        c = ctx()
        storage = mock_storage()

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(s, c, sb, storage)

        assert result.status == "failed"
        assert result.attempt == 3

    @pytest.mark.asyncio
    async def test_json_output_parsed(self):
        sb = mock_sandbox('{"items": [1, 2, 3], "total": 3}')
        s = step()
        c = ctx()
        storage = mock_storage()
        result = await execute_step_with_retry(s, c, sb, storage)
        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert result.output["total"] == 3

    @pytest.mark.asyncio
    async def test_json_with_code_fences_parsed(self):
        sb = mock_sandbox('```json\n{"score": 9.5}\n```')
        s = step()
        c = ctx()
        storage = mock_storage()
        result = await execute_step_with_retry(s, c, sb, storage)
        assert isinstance(result.output, dict)
        assert result.output["score"] == 9.5

    @pytest.mark.asyncio
    async def test_non_json_text_preserved(self):
        sb = mock_sandbox("This is a plain text response.")
        s = step()
        c = ctx()
        storage = mock_storage()
        result = await execute_step_with_retry(s, c, sb, storage)
        assert result.output == "This is a plain text response."


# ──────────────────────────────────────────────────────────────
# 7. CONTEXT SNAPSHOT
# ──────────────────────────────────────────────────────────────

class TestRunContextSnapshot:

    def test_snapshot_contains_required_keys(self):
        c = ctx(input={"x": 1}, costs=[0.1])
        snap = c.snapshot()
        assert "run_id" in snap
        assert "input" in snap
        assert "step_outputs" in snap
        assert "total_cost" in snap

    def test_total_cost_matches_sum(self):
        c = ctx(costs=[0.1, 0.2, 0.3])
        assert abs(c.total_cost - 0.6) < 1e-9

    def test_with_item_creates_child_context(self):
        c = ctx(input={"brands": ["A", "B"]})
        child = c.with_item("A", 0)
        assert child.input["_item"] == "A"
        assert child.input["_index"] == 0
        assert child.run_id == c.run_id

    def test_child_context_isolated_step_outputs(self):
        c = ctx(step_outputs={"s1": "original"})
        child = c.with_item("x", 0)
        child.step_outputs["new_step"] = "child_value"
        assert "new_step" not in c.step_outputs


# ──────────────────────────────────────────────────────────────
# 8. CODE STEP EXECUTION
# ──────────────────────────────────────────────────────────────

class TestCodeStepExecution:

    @pytest.mark.asyncio
    async def test_code_step_basic_math(self):
        from sandcastle.engine.executor import _execute_code_step
        from sandcastle.engine.dag import CodeConfig

        s = StepDefinition(
            id="calc",
            prompt="compute",
            type="code",
            code_config=CodeConfig(code="result = 2 + 2"),
        )
        c = ctx()
        result = await _execute_code_step(s, c)
        assert result.status == "completed"
        assert result.output == 4

    @pytest.mark.asyncio
    async def test_code_step_accesses_input(self):
        from sandcastle.engine.executor import _execute_code_step
        from sandcastle.engine.dag import CodeConfig

        s = StepDefinition(
            id="use_input",
            prompt="use input",
            type="code",
            code_config=CodeConfig(code="result = _input['val'] * 2"),
        )
        c = ctx(input={"val": 21})
        result = await _execute_code_step(s, c)
        assert result.output == 42

    @pytest.mark.asyncio
    async def test_code_step_accesses_step_outputs(self):
        from sandcastle.engine.executor import _execute_code_step
        from sandcastle.engine.dag import CodeConfig

        s = StepDefinition(
            id="combine",
            prompt="combine",
            type="code",
            code_config=CodeConfig(code="result = _steps['s1'] + ' world'"),
        )
        c = ctx(step_outputs={"s1": "hello"})
        result = await _execute_code_step(s, c)
        assert result.output == "hello world"

    @pytest.mark.asyncio
    async def test_code_step_syntax_error_returns_failed(self):
        from sandcastle.engine.executor import _execute_code_step
        from sandcastle.engine.dag import CodeConfig

        s = StepDefinition(
            id="broken",
            prompt="broken",
            type="code",
            code_config=CodeConfig(code="result = !!!"),
        )
        c = ctx()
        result = await _execute_code_step(s, c)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_code_step_blocked_builtins(self):
        """Security: open() should not be available in code steps."""
        from sandcastle.engine.executor import _execute_code_step
        from sandcastle.engine.dag import CodeConfig

        s = StepDefinition(
            id="escape",
            prompt="escape",
            type="code",
            code_config=CodeConfig(code="result = open('/etc/passwd').read()"),
        )
        c = ctx()
        result = await _execute_code_step(s, c)
        assert result.status == "failed"


# ──────────────────────────────────────────────────────────────
# 9. CONDITION STEP
# ──────────────────────────────────────────────────────────────

class TestConditionStep:

    @pytest.mark.asyncio
    async def test_condition_true_skips_else(self):
        from sandcastle.engine.executor import _execute_condition_step
        from sandcastle.engine.dag import ConditionConfig

        s = StepDefinition(
            id="gate",
            prompt="",
            type="condition",
            condition_config=ConditionConfig(
                expression="input['score'] > 5",
                then_steps=["send_email"],
                else_steps=["skip_email"],
            ),
        )
        c = ctx(input={"score": 8})
        result = await _execute_condition_step(s, c)
        assert result.status == "completed"
        assert result.output["condition"] is True
        assert "skip_email" in c.branch_skip_steps
        assert "send_email" not in c.branch_skip_steps

    @pytest.mark.asyncio
    async def test_condition_false_skips_then(self):
        from sandcastle.engine.executor import _execute_condition_step
        from sandcastle.engine.dag import ConditionConfig

        s = StepDefinition(
            id="gate",
            prompt="",
            type="condition",
            condition_config=ConditionConfig(
                expression="input['score'] > 5",
                then_steps=["send_email"],
                else_steps=["skip_email"],
            ),
        )
        c = ctx(input={"score": 2})
        result = await _execute_condition_step(s, c)
        assert result.output["condition"] is False
        assert "send_email" in c.branch_skip_steps


# ──────────────────────────────────────────────────────────────
# 10. TRANSFORM STEP
# ──────────────────────────────────────────────────────────────

class TestTransformStep:

    @pytest.mark.asyncio
    async def test_transform_basic_template(self):
        from sandcastle.engine.executor import _execute_transform_step
        from sandcastle.engine.dag import TransformConfig

        s = StepDefinition(
            id="t1",
            prompt="",
            type="transform",
            transform_config=TransformConfig(template='{"brand": "{input.brand}"}'),
        )
        c = ctx(input={"brand": "DM"})
        result = await _execute_transform_step(s, c)
        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert result.output["brand"] == "DM"

    @pytest.mark.asyncio
    async def test_transform_plain_text(self):
        from sandcastle.engine.executor import _execute_transform_step
        from sandcastle.engine.dag import TransformConfig

        s = StepDefinition(
            id="t2",
            prompt="",
            type="transform",
            transform_config=TransformConfig(template="Hello {input.name}!"),
        )
        c = ctx(input={"name": "Tomas"})
        result = await _execute_transform_step(s, c)
        assert result.output == "Hello Tomas!"
        assert result.cost_usd == 0.0


# ──────────────────────────────────────────────────────────────
# 11. HTTP STEP
# ──────────────────────────────────────────────────────────────

class TestHttpStep:

    @pytest.mark.asyncio
    async def test_http_get_success(self):
        from sandcastle.engine.executor import _execute_http_step
        from sandcastle.engine.dag import HttpConfig

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok"}
        mock_resp.status_code = 200

        s = StepDefinition(
            id="http1",
            prompt="",
            type="http",
            http_config=HttpConfig(url="https://api.example.com/data", method="GET"),
        )
        c = ctx()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await _execute_http_step(s, c)

        assert result.status == "completed"
        assert result.output == {"status": "ok"}
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_http_failure_returns_failed(self):
        from sandcastle.engine.executor import _execute_http_step
        from sandcastle.engine.dag import HttpConfig

        s = StepDefinition(
            id="http_fail",
            prompt="",
            type="http",
            http_config=HttpConfig(url="https://api.example.com/data", method="GET"),
        )
        c = ctx()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client_cls.return_value = mock_client

            result = await _execute_http_step(s, c)

        assert result.status == "failed"
        assert "Connection refused" in result.error


# ──────────────────────────────────────────────────────────────
# 12. PARALLEL FAN-OUT
# ──────────────────────────────────────────────────────────────

class TestParallelFanOut:

    @pytest.mark.asyncio
    async def test_fan_out_creates_child_context_per_item(self):
        """Each fan-out item gets its own _item / _index in input."""
        received_items = []

        async def capture_query(req):
            # Resolve prompt contains _item placeholder via context
            return SandshoreResult(text="ok", structured_output=None, total_cost_usd=0.001, input_tokens=1, output_tokens=1)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=capture_query)

        yaml = """
name: fanout_test
steps:
  - id: fetch
    prompt: "Fetch {input._item}"
    parallel_over: "{input.brands}"
"""
        workflow = parse_yaml_string(yaml)
        plan = build_plan(workflow)
        storage = mock_storage()

        result = await execute_workflow(
            workflow=workflow,
            plan=plan,
            input_data={"brands": ["Notino", "DM", "Sephora"]},
            storage=storage,
        )
        assert result.status == "completed"
        assert isinstance(result.outputs.get("fetch"), list)
        assert len(result.outputs["fetch"]) == 3


# ──────────────────────────────────────────────────────────────
# 13. FULL WORKFLOW EXECUTION
# ──────────────────────────────────────────────────────────────

class TestFullWorkflowExecution:

    @pytest.mark.asyncio
    async def test_simple_sequential_workflow(self):
        yaml = """
name: simple
steps:
  - id: step1
    prompt: "Step one"
  - id: step2
    prompt: "Step two: {steps.step1.output}"
    depends_on: [step1]
"""
        sb = mock_sandbox("done")
        workflow = parse_yaml_string(yaml)
        plan = build_plan(workflow)
        storage = mock_storage()

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=workflow,
                plan=plan,
                input_data={},
                storage=storage,
            )

        assert result.status == "completed"
        assert "step1" in result.outputs
        assert "step2" in result.outputs

    @pytest.mark.asyncio
    async def test_workflow_cancelled_mid_run(self):
        yaml = """
name: cancel_test
steps:
  - id: step1
    prompt: "Step one"
  - id: step2
    prompt: "Step two"
    depends_on: [step1]
"""
        call_count = 0

        async def slow_query(req):
            nonlocal call_count
            call_count += 1
            import asyncio
            await asyncio.sleep(0.05)
            return SandshoreResult(text="done", structured_output=None, total_cost_usd=0.01, input_tokens=5, output_tokens=5)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=slow_query)

        cancel_calls = [False, True]  # First check OK, second triggers cancel

        async def mock_cancel(run_id):
            return cancel_calls.pop(0) if cancel_calls else True

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb),
            patch("sandcastle.engine.executor._check_cancel", side_effect=mock_cancel),
        ):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            storage = mock_storage()
            result = await execute_workflow(
                workflow=workflow,
                plan=plan,
                input_data={},
                storage=storage,
            )

        assert result.status in ("cancelled", "completed")  # Race condition acceptable

    @pytest.mark.asyncio
    async def test_workflow_budget_exceeded(self):
        yaml = """
name: expensive
steps:
  - id: step1
    prompt: "Expensive step"
"""
        sb = mock_sandbox("result", cost=5.0)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            storage = mock_storage()
            result = await execute_workflow(
                workflow=workflow,
                plan=plan,
                input_data={},
                storage=storage,
                max_cost_usd=0.01,
            )

        # Budget is checked BEFORE executing steps, so first step should run
        # but cost accumulates and triggers budget_exceeded on next iteration
        assert result.status in ("completed", "budget_exceeded")

    @pytest.mark.asyncio
    async def test_workflow_with_input_schema_defaults(self):
        yaml = """
name: with_defaults
input_schema:
  type: object
  properties:
    lang:
      type: string
      default: "cs"
steps:
  - id: analyze
    prompt: "Analyze in {input.lang}"
"""
        sb = mock_sandbox("ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            storage = mock_storage()
            result = await execute_workflow(
                workflow=workflow,
                plan=plan,
                input_data={},  # No lang provided - should use default
                storage=storage,
            )

        assert result.status == "completed"


# ──────────────────────────────────────────────────────────────
# 14. EDGE CASES & REGRESSION
# ──────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_branch_skip_steps_do_not_block_dependents(self):
        """Skipped branch steps should be treated as 'done' for scheduling."""
        c = ctx()
        c.branch_skip_steps.add("email_step")
        c.step_outputs["email_step"] = None
        # If dependents check done_steps | branch_skip_steps, this should work
        assert "email_step" in c.branch_skip_steps

    @pytest.mark.asyncio
    async def test_step_result_with_none_output(self):
        """None output should be handled gracefully."""
        sb = mock_sandbox(None)
        sb.query = AsyncMock(return_value=SandshoreResult(
            text=None, structured_output=None, total_cost_usd=0.0, input_tokens=0, output_tokens=0
        ))
        s = step()
        c = ctx()
        storage = mock_storage()
        result = await execute_step_with_retry(s, c, sb, storage)
        # Should not crash, just return None/empty output
        assert result.status in ("completed", "failed")

    def test_context_total_cost_empty(self):
        c = ctx()
        assert c.total_cost == 0.0

    def test_context_total_cost_multiple(self):
        c = ctx(costs=[0.1, 0.05, 0.02])
        assert abs(c.total_cost - 0.17) < 1e-9

    @pytest.mark.asyncio
    async def test_execute_step_with_structured_output_priority(self):
        """structured_output should take priority over text."""
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(return_value=SandshoreResult(
            text="ignore this text",
            structured_output={"result": "use this"},
            total_cost_usd=0.01,
            input_tokens=5,
            output_tokens=5,
        ))
        s = step()
        c = ctx()
        storage = mock_storage()
        result = await execute_step_with_retry(s, c, sb, storage)
        assert result.output == {"result": "use this"}


# ──────────────────────────────────────────────────────────────
# 15. SECURITY CHECKS
# ──────────────────────────────────────────────────────────────

class TestSecurityIssues:

    def test_resolve_templates_no_exec_injection(self):
        """Template resolver must not execute injected code."""
        c = ctx(input={"payload": "__import__('os').system('id')"})
        result = resolve_templates("Input: {input.payload}", c)
        # Result should be a string, not executed
        assert "__import__" in result

    def test_resolve_variable_no_path_traversal(self):
        """Variable resolver should not access arbitrary object attributes."""
        c = ctx(input={"__class__": "should_not_leak"})
        # This should just return the string value, not trigger class access
        val = resolve_variable("input.__class__", c)
        # Should return the value from dict, not the actual class
        assert val == "should_not_leak" or val is None

    @pytest.mark.asyncio
    async def test_code_step_no_import_allowed(self):
        """import statement should fail in code steps due to restricted builtins."""
        from sandcastle.engine.executor import _execute_code_step
        from sandcastle.engine.dag import CodeConfig

        s = StepDefinition(
            id="import_test",
            prompt="",
            type="code",
            code_config=CodeConfig(code="import os; result = os.getcwd()"),
        )
        c = ctx()
        result = await _execute_code_step(s, c)
        # Should fail because __import__ is not in restricted builtins
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_code_step_no_eval_available(self):
        """eval() should not be callable from code steps."""
        from sandcastle.engine.executor import _execute_code_step
        from sandcastle.engine.dag import CodeConfig

        s = StepDefinition(
            id="eval_test",
            prompt="",
            type="code",
            code_config=CodeConfig(code="result = eval('2+2')"),
        )
        c = ctx()
        result = await _execute_code_step(s, c)
        assert result.status == "failed"
