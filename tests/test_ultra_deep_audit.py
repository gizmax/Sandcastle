"""Ultra-deep audit test suite for Sandcastle.

Targets the most critical untested code paths across:
- Executor: variable resolution edge cases, cancel logic, caching,
  output truncation, JS escaping, browser action cache, CSV/PDF output
- DAG: validation boundaries, cycle detection, env var resolution,
  all step type config parsing, build_plan edge cases
- Webhook dispatcher: SSRF edge cases, retry timing, signature verification
- Storage: path traversal, read/write/list/delete operations
- API routes: input validation edge cases, workflow loading
- Auth: key hashing, generation, middleware logic
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    ApprovalConfig,
    AutoPilotConfig,
    BrowserConfig,
    ClassifyConfig,
    CodeConfig,
    CompletionConfig,
    ConditionConfig,
    CsvOutputConfig,
    DelegateConfig,
    EvaluationConfig,
    ExecutionPlan,
    FailureConfig,
    FallbackConfig,
    GateConfig,
    HttpConfig,
    LlmConfig,
    LoopConfig,
    MemoryConfig,
    NotifyConfig,
    PdfReportConfig,
    RaceConfig,
    RetryConfig,
    SensorConfig,
    SLOConfig,
    StepDefinition,
    StepMemoryConfig,
    SubWorkflowConfig,
    TransformConfig,
    VariantConfig,
    WorkflowDefinition,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    WorkflowResult,
    _backoff_delay,
    _check_budget,
    _escape_js_string,
    _is_cacheable_output,
    _truncate_output,
    _UNRESOLVED,
    _write_csv_output,
    cancel_run_local,
    resolve_templates,
    resolve_variable,
)
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime


# Private function references
_execute_condition_step = _executor_mod._execute_condition_step
_execute_transform_step = _executor_mod._execute_transform_step
_execute_notify_step = _executor_mod._execute_notify_step
_execute_code_step = _executor_mod._execute_code_step
_execute_http_step = _executor_mod._execute_http_step
_execute_loop_step = _executor_mod._execute_loop_step
_execute_race_step = _executor_mod._execute_race_step
_execute_sensor_step = _executor_mod._execute_sensor_step
_execute_delegate_step = _executor_mod._execute_delegate_step
_execute_gate_step = _executor_mod._execute_gate_step
_get_pdf_report_instruction = _executor_mod._get_pdf_report_instruction
_compute_cache_key = _executor_mod._compute_cache_key
_cache_key = _executor_mod._cache_key
_get_cached_actions = _executor_mod._get_cached_actions
_save_cached_actions = _executor_mod._save_cached_actions
_browser_action_cache = _executor_mod._browser_action_cache
_cancel_flags = _executor_mod._cancel_flags
_BROWSER_CACHE_MAX = _executor_mod._BROWSER_CACHE_MAX


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
        run_id=kwargs.get("run_id", "test-run-deep-audit"),
        input=kwargs.get("input", {}),
        step_outputs=kwargs.get("step_outputs", {}),
        costs=kwargs.get("costs", []),
        max_cost_usd=kwargs.get("max_cost_usd", None),
        workflow_name=kwargs.get("workflow_name", "test-workflow"),
        default_tools=kwargs.get("default_tools", []),
    )


def _step(**kwargs) -> StepDefinition:
    return StepDefinition(
        id=kwargs.get("id", "test-step"),
        prompt=kwargs.get("prompt", "Test prompt"),
        model=kwargs.get("model", "sonnet"),
        max_turns=kwargs.get("max_turns", 5),
        timeout=kwargs.get("timeout", 60),
        type=kwargs.get("type", "standard"),
        depends_on=kwargs.get("depends_on", []),
        retry=kwargs.get("retry"),
        output_schema=kwargs.get("output_schema"),
        http_config=kwargs.get("http_config"),
        code_config=kwargs.get("code_config"),
        condition_config=kwargs.get("condition_config"),
        transform_config=kwargs.get("transform_config"),
        notify_config=kwargs.get("notify_config"),
        loop_config=kwargs.get("loop_config"),
        race_config=kwargs.get("race_config"),
        sensor_config=kwargs.get("sensor_config"),
        gate_config=kwargs.get("gate_config"),
        delegate_config=kwargs.get("delegate_config"),
        csv_output=kwargs.get("csv_output"),
        pdf_report=kwargs.get("pdf_report"),
    )


# =============================================================================
# Section 1: resolve_variable edge cases
# =============================================================================

class TestResolveVariableEdgeCases:
    """Test variable resolution with unusual inputs."""

    def test_empty_var_path_returns_unresolved(self):
        ctx = _ctx()
        assert resolve_variable("", ctx) is _UNRESOLVED

    def test_input_only_returns_full_input(self):
        ctx = _ctx(input={"a": 1})
        result = resolve_variable("input", ctx)
        assert result == {"a": 1}

    def test_input_deeply_nested(self):
        ctx = _ctx(input={"a": {"b": {"c": {"d": 42}}}})
        assert resolve_variable("input.a.b.c.d", ctx) == 42

    def test_input_nested_none_value(self):
        ctx = _ctx(input={"a": {"b": None}})
        assert resolve_variable("input.a.b.c", ctx) is None

    def test_input_list_out_of_bounds(self):
        ctx = _ctx(input={"items": [1, 2, 3]})
        assert resolve_variable("input.items.5", ctx) is _UNRESOLVED

    def test_input_negative_list_index(self):
        ctx = _ctx(input={"items": [1, 2, 3]})
        assert resolve_variable("input.items.-1", ctx) is _UNRESOLVED

    def test_input_list_non_numeric_index(self):
        ctx = _ctx(input={"items": [1, 2, 3]})
        assert resolve_variable("input.items.abc", ctx) is _UNRESOLVED

    def test_input_traverse_scalar(self):
        ctx = _ctx(input={"a": 42})
        assert resolve_variable("input.a.b", ctx) is _UNRESOLVED

    def test_step_output_nonexistent_step(self):
        ctx = _ctx(step_outputs={})
        assert resolve_variable("steps.nonexistent.output", ctx) is _UNRESOLVED

    def test_step_output_non_output_field(self):
        ctx = _ctx(step_outputs={"s1": {"data": "hello"}})
        result = resolve_variable("steps.s1.status", ctx)
        # "status" is not "output", so the steps.X.Y check only handles "output"
        # and falls through to the end returning _UNRESOLVED
        assert result is _UNRESOLVED

    def test_step_output_too_few_parts(self):
        ctx = _ctx(step_outputs={"s1": "result"})
        # "steps.s1" has only 2 parts, needs >= 3 (steps.X.output)
        assert resolve_variable("steps.s1", ctx) is _UNRESOLVED

    def test_step_output_deeply_nested_field(self):
        ctx = _ctx(step_outputs={"s1": {"report": {"metrics": [10, 20, 30]}}})
        assert resolve_variable("steps.s1.output.report.metrics.1", ctx) == 20

    def test_date_variable(self):
        ctx = _ctx()
        result = resolve_variable("date", ctx)
        assert re.match(r"\d{4}-\d{2}-\d{2}", result)

    def test_run_id_variable(self):
        ctx = _ctx(run_id="custom-run-999")
        assert resolve_variable("run_id", ctx) == "custom-run-999"

    def test_unknown_root_returns_unresolved(self):
        ctx = _ctx()
        assert resolve_variable("unknown.path.here", ctx) is _UNRESOLVED

    def test_empty_list_in_input(self):
        ctx = _ctx(input={"items": []})
        assert resolve_variable("input.items.0", ctx) is _UNRESOLVED


# =============================================================================
# Section 2: resolve_templates edge cases
# =============================================================================

class TestResolveTemplatesEdgeCases:

    def test_no_templates_returns_unchanged(self):
        ctx = _ctx()
        assert resolve_templates("Hello world", ctx) == "Hello world"

    def test_multiple_variables_in_one_string(self):
        ctx = _ctx(
            input={"name": "Alice", "role": "admin"},
            step_outputs={"s1": "data"},
        )
        result = resolve_templates(
            "User {input.name} is {input.role}, step output: {steps.s1.output}",
            ctx,
        )
        assert result == "User Alice is admin, step output: data"

    def test_dict_value_serialized_as_json(self):
        ctx = _ctx(input={"data": {"key": "value"}})
        result = resolve_templates("{input.data}", ctx)
        assert result == '{"key": "value"}'

    def test_list_value_serialized_as_json(self):
        ctx = _ctx(input={"items": [1, 2, 3]})
        result = resolve_templates("{input.items}", ctx)
        assert result == "[1, 2, 3]"

    def test_unresolved_variable_stays_as_placeholder(self):
        ctx = _ctx()
        result = resolve_templates("{input.missing}", ctx)
        assert result == "{input.missing}"

    def test_auto_inject_unreferenced_dependency(self):
        ctx = _ctx(step_outputs={"dep1": "dependency data"})
        result = resolve_templates("Do something", ctx, depends_on=["dep1"])
        assert "dep1" in result
        assert "dependency data" in result
        assert "Context from previous steps:" in result

    def test_auto_inject_skips_referenced_dependency(self):
        ctx = _ctx(step_outputs={"dep1": "dependency data"})
        result = resolve_templates(
            "Use {steps.dep1.output}", ctx, depends_on=["dep1"]
        )
        assert "Context from previous steps:" not in result

    def test_auto_inject_dependency_not_in_outputs(self):
        ctx = _ctx(step_outputs={})
        result = resolve_templates("Do something", ctx, depends_on=["future_step"])
        assert "Context from previous steps:" not in result

    def test_run_id_template(self):
        ctx = _ctx(run_id="unique-id-42")
        result = resolve_templates("Run: {run_id}", ctx)
        assert result == "Run: unique-id-42"

    def test_date_template(self):
        ctx = _ctx()
        result = resolve_templates("Today: {date}", ctx)
        assert re.search(r"\d{4}-\d{2}-\d{2}", result)

    def test_integer_value_converted_to_string(self):
        ctx = _ctx(input={"count": 42})
        result = resolve_templates("Count: {input.count}", ctx)
        assert result == "Count: 42"

    def test_boolean_value_converted_to_string(self):
        ctx = _ctx(input={"active": True})
        result = resolve_templates("Active: {input.active}", ctx)
        assert result == "Active: True"

    def test_none_value_resolves_to_none_string(self):
        """When input.field is None, traversing sub returns None (not _UNRESOLVED),
        which resolve_templates converts to the string "None"."""
        ctx = _ctx(input={"field": None})
        result = resolve_templates("{input.field.sub}", ctx)
        assert result == "None"


# =============================================================================
# Section 3: _escape_js_string security
# =============================================================================

class TestEscapeJsString:

    def test_single_quote(self):
        assert _escape_js_string("it's") == "it\\'s"

    def test_double_quote(self):
        assert _escape_js_string('say "hello"') == 'say \\"hello\\"'

    def test_backslash(self):
        assert _escape_js_string("path\\to\\file") == "path\\\\to\\\\file"

    def test_newline(self):
        assert _escape_js_string("line1\nline2") == "line1\\nline2"

    def test_carriage_return(self):
        assert _escape_js_string("line1\rline2") == "line1\\rline2"

    def test_tab(self):
        assert _escape_js_string("col1\tcol2") == "col1\\tcol2"

    def test_combined_special_chars(self):
        result = _escape_js_string("it's a \"test\"\nwith\\slash")
        assert "\n" not in result
        assert "\\'" in result
        assert '\\"' in result

    def test_empty_string(self):
        assert _escape_js_string("") == ""

    def test_unicode_passthrough(self):
        assert _escape_js_string("Hello") == "Hello"

    def test_already_escaped_backslash(self):
        result = _escape_js_string("\\n")
        assert result == "\\\\n"


# =============================================================================
# Section 4: _is_cacheable_output edge cases
# =============================================================================

class TestIsCacheableOutput:

    def test_none_not_cacheable(self):
        assert _is_cacheable_output(None) is False

    def test_empty_string_not_cacheable(self):
        assert _is_cacheable_output("") is False

    def test_empty_list_not_cacheable(self):
        assert _is_cacheable_output([]) is False

    def test_empty_dict_not_cacheable(self):
        assert _is_cacheable_output({}) is False

    def test_real_string_cacheable(self):
        assert _is_cacheable_output("Real data output") is True

    def test_real_dict_cacheable(self):
        assert _is_cacheable_output({"result": "meaningful data " * 20}) is True

    def test_failed_keyword_not_cacheable(self):
        assert _is_cacheable_output("please provide the URL") is False

    def test_failed_keyword_case_insensitive(self):
        assert _is_cacheable_output("I Don't Have Access to that") is False

    def test_long_string_with_keyword_still_cacheable(self):
        long_text = "I don't have access " + "x" * 200
        assert _is_cacheable_output(long_text) is True

    def test_dict_with_failed_result(self):
        assert _is_cacheable_output({"result": "please provide the data"}) is False

    def test_dict_with_long_result_cacheable(self):
        assert _is_cacheable_output({"result": "data " * 50}) is True

    def test_dict_with_zero_mentions_not_cacheable(self):
        assert _is_cacheable_output({"total_mentions": 0, "mentions": []}) is False

    def test_integer_cacheable(self):
        assert _is_cacheable_output(42) is True

    def test_list_with_data_cacheable(self):
        assert _is_cacheable_output([1, 2, 3]) is True


# =============================================================================
# Section 5: _truncate_output
# =============================================================================

class TestTruncateOutput:

    def test_none_passthrough(self):
        assert _truncate_output(None) is None

    def test_small_string_passthrough(self):
        assert _truncate_output("hello") == "hello"

    def test_small_dict_passthrough(self):
        data = {"key": "value"}
        assert _truncate_output(data) == data

    def test_large_string_truncated(self):
        big = "x" * 20_000_000
        result = _truncate_output(big, max_size=1000)
        assert isinstance(result, str)
        assert len(result) <= 1100
        assert "TRUNCATED" in result

    def test_large_dict_truncated(self):
        big = {"data": "x" * 20_000_000}
        result = _truncate_output(big, max_size=1000)
        assert isinstance(result, dict)
        assert result["_truncated"] is True
        assert "_original_size" in result

    def test_exactly_at_limit_not_truncated(self):
        s = "x" * 100
        assert _truncate_output(s, max_size=100) == s


# =============================================================================
# Section 6: _compute_cache_key determinism
# =============================================================================

class TestComputeCacheKey:

    def test_deterministic(self):
        k1 = _compute_cache_key("wf", "step", "prompt", "sonnet")
        k2 = _compute_cache_key("wf", "step", "prompt", "sonnet")
        assert k1 == k2

    def test_different_prompts(self):
        k1 = _compute_cache_key("wf", "step", "prompt1", "sonnet")
        k2 = _compute_cache_key("wf", "step", "prompt2", "sonnet")
        assert k1 != k2

    def test_different_models(self):
        k1 = _compute_cache_key("wf", "step", "prompt", "sonnet")
        k2 = _compute_cache_key("wf", "step", "prompt", "opus")
        assert k1 != k2

    def test_returns_hex_string(self):
        k = _compute_cache_key("wf", "step", "prompt", "sonnet")
        assert isinstance(k, str)
        assert len(k) == 64
        int(k, 16)


# =============================================================================
# Section 7: Browser action cache
# =============================================================================

class TestBrowserActionCache:

    def setup_method(self):
        _browser_action_cache.clear()

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        assert await _get_cached_actions("https://example.com", "click button") is None

    @pytest.mark.asyncio
    async def test_cache_hit_returns_actions(self):
        actions = [{"type": "click", "selector": "#btn"}]
        await _save_cached_actions("https://example.com", "click button", actions)
        result = await _get_cached_actions("https://example.com", "click button")
        assert result == actions

    def test_cache_key_uses_host_and_path(self):
        key1 = _cache_key("https://example.com/page1", "task")
        key2 = _cache_key("https://example.com/page2", "task")
        assert key1 != key2

    def test_cache_key_truncates_long_intent(self):
        long_intent = "a" * 200
        key = _cache_key("https://example.com", long_intent)
        assert len(key.split(":")[1]) == 100

    @pytest.mark.asyncio
    async def test_cache_eviction_at_max(self):
        for i in range(_BROWSER_CACHE_MAX + 10):
            await _save_cached_actions(f"https://example.com/{i}", "task", [{"i": i}])
        assert len(_browser_action_cache) == _BROWSER_CACHE_MAX

    def teardown_method(self):
        _browser_action_cache.clear()


# =============================================================================
# Section 8: Cancel flags (local mode)
# =============================================================================

class TestCancelFlags:

    def setup_method(self):
        _cancel_flags.clear()

    @pytest.mark.asyncio
    async def test_cancel_run_local_adds_flag(self):
        await cancel_run_local("run-1")
        assert "run-1" in _cancel_flags

    @pytest.mark.asyncio
    async def test_cancel_flags_overflow_evicts_half(self):
        """When cancel flags reach 10000, adding one more evicts oldest half (5000),
        then adds the new entry, resulting in 5001 entries."""
        for i in range(10001):
            await cancel_run_local(f"run-{i}")
        # 10000 entries -> evict 5000 oldest -> 5000 remain -> add 1 = 5001
        assert len(_cancel_flags) == 5001

    @pytest.mark.asyncio
    async def test_check_cancel_local_mode(self):
        await cancel_run_local("run-cancel-test")
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = ""
            result = await _executor_mod._check_cancel("run-cancel-test")
            assert result is True
            # Flag persists until cleanup (not consumed on check)
            result2 = await _executor_mod._check_cancel("run-cancel-test")
            assert result2 is True

    @pytest.mark.asyncio
    async def test_check_cancel_not_cancelled(self):
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = ""
            result = await _executor_mod._check_cancel("nonexistent-run")
            assert result is False

    def teardown_method(self):
        _cancel_flags.clear()


# =============================================================================
# Section 9: _check_budget edge cases
# =============================================================================

class TestCheckBudgetEdgeCases:

    def test_none_budget_always_ok(self):
        ctx = _ctx(max_cost_usd=None, costs=[100.0])
        assert _check_budget(ctx) is None

    def test_zero_budget_always_ok(self):
        ctx = _ctx(max_cost_usd=0.0, costs=[100.0])
        assert _check_budget(ctx) is None

    def test_negative_budget_always_ok(self):
        ctx = _ctx(max_cost_usd=-1.0, costs=[100.0])
        assert _check_budget(ctx) is None

    def test_exactly_at_80_percent(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[0.8])
        assert _check_budget(ctx) == "warning"

    def test_at_79_percent(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[0.79])
        assert _check_budget(ctx) is None

    def test_exactly_at_100_percent(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[1.0])
        assert _check_budget(ctx) == "exceeded"

    def test_over_100_percent(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[1.5])
        assert _check_budget(ctx) == "exceeded"

    def test_zero_costs(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[])
        assert _check_budget(ctx) is None

    def test_many_small_costs_sum_to_warning(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[0.1] * 9)
        assert _check_budget(ctx) == "warning"


# =============================================================================
# Section 10: _backoff_delay
# =============================================================================

class TestBackoffDelay:

    def test_exponential_attempt_1(self):
        assert _backoff_delay(1, "exponential") == 2.0

    def test_exponential_attempt_2(self):
        assert _backoff_delay(2, "exponential") == 4.0

    def test_exponential_capped_at_60(self):
        assert _backoff_delay(10, "exponential") == 60.0

    def test_fixed_always_2(self):
        assert _backoff_delay(1, "fixed") == 2.0
        assert _backoff_delay(5, "fixed") == 2.0
        assert _backoff_delay(100, "fixed") == 2.0

    def test_unknown_backoff_treated_as_fixed(self):
        assert _backoff_delay(3, "unknown") == 2.0


# =============================================================================
# Section 11: RunContext methods
# =============================================================================

class TestRunContext:

    def test_total_cost_empty(self):
        ctx = _ctx()
        assert ctx.total_cost == 0.0

    def test_total_cost_multiple(self):
        ctx = _ctx(costs=[0.01, 0.02, 0.03])
        assert ctx.total_cost == pytest.approx(0.06)

    def test_snapshot_structure(self):
        ctx = _ctx(
            run_id="r-1",
            input={"key": "val"},
            step_outputs={"s1": "out"},
            costs=[0.01],
        )
        snap = ctx.snapshot()
        assert snap["run_id"] == "r-1"
        assert snap["input"] == {"key": "val"}
        assert snap["step_outputs"] == {"s1": "out"}
        assert snap["total_cost"] == pytest.approx(0.01)

    def test_with_item_isolation(self):
        ctx = _ctx(input={"name": "parent"}, step_outputs={"s1": "data"})
        child = ctx.with_item("item_val", 0)
        assert child.input["_item"] == "item_val"
        assert child.input["_index"] == 0
        assert child.input["name"] == "parent"
        child.step_outputs["new_step"] = "child_data"
        assert "new_step" not in ctx.step_outputs

    def test_with_item_isolates_costs_list(self):
        """Child contexts get their own costs list to avoid concurrent
        appends. The parent aggregates child costs after join."""
        ctx = _ctx(costs=[0.01])
        child = ctx.with_item("item", 0)
        child.costs.append(0.02)
        # Parent costs should NOT be affected by child appends
        assert ctx.total_cost == pytest.approx(0.01)
        assert child.total_cost == pytest.approx(0.02)

    def test_with_item_independent_branch_skip(self):
        ctx = _ctx()
        ctx.branch_skip_steps.add("skip-me")
        child = ctx.with_item("item", 0)
        child.branch_skip_steps.add("skip-child-only")
        assert "skip-child-only" not in ctx.branch_skip_steps


# =============================================================================
# Section 12: CSV output
# =============================================================================

class TestWriteCsvOutput:

    def test_dict_output_to_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = _step(
                id="csv-test",
                csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file", filename="test"),
            )
            _write_csv_output(step, {"col1": "a", "col2": "b"}, "run-1")
            files = list(Path(tmpdir).glob("*.csv"))
            assert len(files) == 1
            with open(files[0]) as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == 1
            assert rows[0]["col1"] == "a"

    def test_list_of_dicts_to_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = _step(
                id="csv-list",
                csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file", filename="list"),
            )
            _write_csv_output(step, [{"x": 1}, {"x": 2}], "run-1")
            files = list(Path(tmpdir).glob("*.csv"))
            assert len(files) == 1
            with open(files[0]) as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == 2

    def test_string_json_output_to_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = _step(
                id="csv-json-str",
                csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file", filename="jsonstr"),
            )
            _write_csv_output(step, '[{"a": 1}, {"a": 2}]', "run-1")
            files = list(Path(tmpdir).glob("*.csv"))
            assert len(files) == 1

    def test_plain_string_output_to_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = _step(
                id="csv-plain",
                csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file", filename="plain"),
            )
            _write_csv_output(step, "just text", "run-1")
            files = list(Path(tmpdir).glob("*.csv"))
            assert len(files) == 1
            with open(files[0]) as f:
                rows = list(csv.DictReader(f))
            assert rows[0]["value"] == "just text"

    def test_append_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = _step(
                id="csv-append",
                csv_output=CsvOutputConfig(directory=tmpdir, mode="append", filename="append_test"),
            )
            _write_csv_output(step, {"x": "1"}, "run-1")
            _write_csv_output(step, {"x": "2"}, "run-2")
            filepath = Path(tmpdir) / "append_test.csv"
            assert filepath.exists()
            with open(filepath) as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == 2

    def test_sandbox_root_blocks_outside_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox_root = Path(tmpdir) / "sandbox"
            sandbox_root.mkdir()
            step = _step(
                id="csv-sandbox",
                csv_output=CsvOutputConfig(directory="/tmp/evil", mode="new_file", filename="evil"),
            )
            with patch("sandcastle.config.settings") as mock_settings:
                mock_settings.sandbox_root = str(sandbox_root)
                _write_csv_output(step, {"a": 1}, "run-1")
            assert not Path("/tmp/evil/evil.csv").exists()

    def test_no_csv_config_noop(self):
        step = _step(id="no-csv")
        _write_csv_output(step, {"a": 1}, "run-1")


# =============================================================================
# Section 13: PDF report instruction language support
# =============================================================================

class TestPdfReportInstruction:

    def test_english(self):
        instr = _get_pdf_report_instruction("en")
        assert "English" in instr

    def test_czech(self):
        instr = _get_pdf_report_instruction("cs")
        assert "cestine" in instr

    def test_german(self):
        instr = _get_pdf_report_instruction("de")
        assert "Deutsch" in instr

    def test_unknown_language_fallback(self):
        instr = _get_pdf_report_instruction("xx")
        assert "xx" in instr
        assert "FORMATTING" in instr

    def test_japanese(self):
        instr = _get_pdf_report_instruction("ja")
        assert "Japanese" in instr


# =============================================================================
# Section 14: _execute_condition_step
# =============================================================================

class TestConditionStepExecution:

    @pytest.mark.asyncio
    async def test_true_condition_with_complex_expression(self):
        ctx = _ctx(input={"score": 85})
        step = _step(
            id="cond", type="condition",
            condition_config=ConditionConfig(
                expression="int('{input.score}') > 50",
                then_steps=["good"], else_steps=["bad"],
            ),
        )
        result = await _execute_condition_step(step, ctx)
        assert result.status == "completed"
        assert result.output["condition"] is True
        assert "bad" in ctx.branch_skip_steps
        assert "good" not in ctx.branch_skip_steps

    @pytest.mark.asyncio
    async def test_false_condition(self):
        ctx = _ctx(input={"score": 20})
        step = _step(
            id="cond", type="condition",
            condition_config=ConditionConfig(
                expression="int('{input.score}') > 50",
                then_steps=["good"], else_steps=["bad"],
            ),
        )
        result = await _execute_condition_step(step, ctx)
        assert result.output["condition"] is False
        assert "good" in ctx.branch_skip_steps

    @pytest.mark.asyncio
    async def test_missing_config_fails(self):
        result = await _execute_condition_step(_step(id="cond", type="condition"), _ctx())
        assert result.status == "failed"
        assert "Missing condition_config" in result.error

    @pytest.mark.asyncio
    async def test_invalid_expression_fails(self):
        step = _step(
            id="cond", type="condition",
            condition_config=ConditionConfig(expression="this is not valid python", then_steps=[], else_steps=[]),
        )
        result = await _execute_condition_step(step, _ctx())
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_condition_with_steps_reference(self):
        ctx = _ctx(step_outputs={"prev": {"success": True}})
        step = _step(
            id="cond", type="condition",
            condition_config=ConditionConfig(
                expression="steps['prev']['success'] == True",
                then_steps=["proceed"], else_steps=["fallback"],
            ),
        )
        result = await _execute_condition_step(step, ctx)
        assert result.output["condition"] is True


# =============================================================================
# Section 15: _execute_code_step
# =============================================================================

class TestCodeStepExecution:

    @pytest.mark.asyncio
    async def test_basic_code_execution(self):
        step = _step(id="code", type="code", code_config=CodeConfig(code="result = 42"))
        r = await _execute_code_step(step, _ctx())
        assert r.status == "completed"
        assert r.output == 42
        assert r.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_code_with_input_access(self):
        ctx = _ctx(input={"x": 10, "y": 20})
        step = _step(id="code", type="code", code_config=CodeConfig(code="result = _input['x'] + _input['y']"))
        r = await _execute_code_step(step, ctx)
        assert r.output == 30

    @pytest.mark.asyncio
    async def test_code_with_step_outputs(self):
        ctx = _ctx(step_outputs={"s1": [1, 2, 3]})
        step = _step(id="code", type="code", code_config=CodeConfig(code="result = sum(_steps['s1'])"))
        r = await _execute_code_step(step, ctx)
        assert r.output == 6

    @pytest.mark.asyncio
    async def test_code_syntax_error(self):
        r = await _execute_code_step(_step(id="code", type="code", code_config=CodeConfig(code="def !!!")), _ctx())
        assert r.status == "failed"

    @pytest.mark.asyncio
    async def test_code_runtime_error(self):
        r = await _execute_code_step(_step(id="code", type="code", code_config=CodeConfig(code="result = 1 / 0")), _ctx())
        assert r.status == "failed"
        assert "division" in r.error.lower()

    @pytest.mark.asyncio
    async def test_code_missing_config(self):
        r = await _execute_code_step(_step(id="code", type="code"), _ctx())
        assert r.status == "failed"
        assert "Missing code_config" in r.error

    @pytest.mark.asyncio
    async def test_code_restricted_builtins(self):
        step = _step(id="code", type="code", code_config=CodeConfig(code="import os; result = os.listdir('/')"))
        r = await _execute_code_step(step, _ctx())
        assert r.status == "failed"

    @pytest.mark.asyncio
    async def test_code_json_module_available(self):
        step = _step(id="code", type="code", code_config=CodeConfig(code='result = json.loads(\'{"a": 1}\')'))
        r = await _execute_code_step(step, _ctx())
        assert r.output == {"a": 1}

    @pytest.mark.asyncio
    async def test_code_no_result_set(self):
        r = await _execute_code_step(_step(id="code", type="code", code_config=CodeConfig(code="x = 42")), _ctx())
        assert r.status == "completed"
        assert r.output is None


# =============================================================================
# Section 16: _execute_transform_step (expanded)
# =============================================================================

class TestTransformStepExpanded:

    @pytest.mark.asyncio
    async def test_jinja_double_brace_syntax(self):
        ctx = _ctx(input={"name": "World"})
        step = _step(id="t", type="transform", transform_config=TransformConfig(template="{{ input.name }}"))
        r = await _execute_transform_step(step, ctx)
        assert r.status == "completed"
        assert r.output == "World"

    @pytest.mark.asyncio
    async def test_jinja_tojson_filter(self):
        ctx = _ctx(input={"data": {"key": "val"}})
        step = _step(id="t", type="transform", transform_config=TransformConfig(template="{{ input.data | tojson }}"))
        r = await _execute_transform_step(step, ctx)
        assert r.status == "completed"
        parsed = json.loads(r.output) if isinstance(r.output, str) else r.output
        assert parsed == {"key": "val"}

    @pytest.mark.asyncio
    async def test_transform_outputs_valid_json_dict(self):
        ctx = _ctx(input={"x": 42})
        step = _step(id="t", type="transform", transform_config=TransformConfig(template='{"value": {input.x}}'))
        r = await _execute_transform_step(step, ctx)
        assert isinstance(r.output, dict)
        assert r.output["value"] == 42

    @pytest.mark.asyncio
    async def test_transform_outputs_array(self):
        ctx = _ctx(input={"items": [1, 2, 3]})
        step = _step(id="t", type="transform", transform_config=TransformConfig(template="{input.items}"))
        r = await _execute_transform_step(step, ctx)
        assert r.output == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_transform_missing_config(self):
        r = await _execute_transform_step(_step(id="t", type="transform"), _ctx())
        assert r.status == "failed"

    @pytest.mark.asyncio
    async def test_transform_jinja_none_value(self):
        step = _step(id="t", type="transform", transform_config=TransformConfig(template="{{ input.missing }}"))
        r = await _execute_transform_step(step, _ctx())
        assert r.status == "completed"
        assert r.output == ""


# =============================================================================
# Section 17: _execute_notify_step
# =============================================================================

class TestNotifyStep:

    @pytest.mark.asyncio
    async def test_basic_notify(self):
        ctx = _ctx(input={"channel": "#general"})
        step = _step(id="notify", type="notify", notify_config=NotifyConfig(service="slack", channel="{input.channel}", message="Hello!"))
        r = await _execute_notify_step(step, ctx)
        assert r.status == "completed"
        assert r.output["service"] == "slack"
        assert r.output["channel"] == "#general"
        assert r.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_notify_missing_config(self):
        r = await _execute_notify_step(_step(id="notify", type="notify"), _ctx())
        assert r.status == "failed"


# =============================================================================
# Section 18: _execute_gate_step (timeout strategy)
# =============================================================================

class TestGateStepTimeout:

    @pytest.mark.asyncio
    async def test_gate_timeout_approve(self):
        step = _step(id="gate", type="gate", gate_config=GateConfig(strategies=[{"type": "timeout", "config": {"seconds": 0, "action": "approve"}}]))
        with patch("asyncio.sleep", new_callable=AsyncMock):
            from sandcastle.engine.storage import LocalStorage
            r = await _execute_gate_step(step, _ctx(), LocalStorage(base_dir=tempfile.mkdtemp()))
        assert r.output["decision"] == "approved"

    @pytest.mark.asyncio
    async def test_gate_timeout_reject(self):
        step = _step(id="gate", type="gate", gate_config=GateConfig(strategies=[{"type": "timeout", "config": {"seconds": 0, "action": "reject"}}]))
        with patch("asyncio.sleep", new_callable=AsyncMock):
            from sandcastle.engine.storage import LocalStorage
            r = await _execute_gate_step(step, _ctx(), LocalStorage(base_dir=tempfile.mkdtemp()))
        assert r.output["decision"] == "rejected"

    @pytest.mark.asyncio
    async def test_gate_missing_config(self):
        from sandcastle.engine.storage import LocalStorage
        r = await _execute_gate_step(_step(id="gate", type="gate"), _ctx(), LocalStorage(base_dir=tempfile.mkdtemp()))
        assert r.status == "failed"

    @pytest.mark.asyncio
    async def test_gate_no_matching_strategy(self):
        step = _step(id="gate", type="gate", gate_config=GateConfig(strategies=[{"type": "unknown_type", "config": {}}]))
        from sandcastle.engine.storage import LocalStorage
        r = await _execute_gate_step(step, _ctx(), LocalStorage(base_dir=tempfile.mkdtemp()))
        assert r.status == "failed"
        assert "No gate strategy" in r.error


# =============================================================================
# Section 19: Storage backend (LocalStorage)
# =============================================================================

class TestLocalStorage:

    @pytest.mark.asyncio
    async def test_write_and_read(self):
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_dir=tmpdir)
            await storage.write("test/file.txt", "hello")
            assert await storage.read("test/file.txt") == "hello"

    @pytest.mark.asyncio
    async def test_read_nonexistent(self):
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            assert await LocalStorage(base_dir=tmpdir).read("nonexistent.txt") is None

    @pytest.mark.asyncio
    async def test_list_files(self):
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_dir=tmpdir)
            await storage.write("prefix/a.txt", "a")
            await storage.write("prefix/b.txt", "b")
            await storage.write("other/c.txt", "c")
            files = await storage.list("prefix/")
            assert len(files) == 2

    @pytest.mark.asyncio
    async def test_delete_file(self):
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_dir=tmpdir)
            await storage.write("deleteme.txt", "data")
            await storage.delete("deleteme.txt")
            assert await storage.read("deleteme.txt") is None

    @pytest.mark.asyncio
    async def test_path_traversal_denied(self):
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="traversal"):
                await LocalStorage(base_dir=tmpdir).read("../../etc/passwd")

    @pytest.mark.asyncio
    async def test_path_traversal_write_denied(self):
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="traversal"):
                await LocalStorage(base_dir=tmpdir).write("../../../tmp/evil.txt", "hacked")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self):
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            await LocalStorage(base_dir=tmpdir).delete("nonexistent.txt")

    @pytest.mark.asyncio
    async def test_overwrite_existing_file(self):
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_dir=tmpdir)
            await storage.write("file.txt", "v1")
            await storage.write("file.txt", "v2")
            assert await storage.read("file.txt") == "v2"


# =============================================================================
# Section 20: DAG parsing edge cases
# =============================================================================

class TestDagParsingEdgeCases:

    def test_empty_yaml_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("")

    def test_whitespace_only_yaml_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("   \n  \n  ")

    def test_yaml_parses_to_string_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_yaml_string("just a string")

    def test_yaml_parses_to_list_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_yaml_string("- item1\n- item2")

    def test_yaml_parses_to_none_raises(self):
        with pytest.raises(ValueError, match="None"):
            parse_yaml_string("---\n")

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            parse_yaml_string("description: test\nsteps:\n  - id: s1\n    prompt: do thing\n")

    def test_steps_not_list_raises(self):
        with pytest.raises(ValueError, match="list"):
            parse_yaml_string('name: test\nsteps: "not a list"\n')

    def test_step_without_id_raises(self):
        with pytest.raises(ValueError, match="id"):
            parse_yaml_string("name: test\nsteps:\n  - prompt: do thing\n")

    def test_step_with_numeric_id_raises(self):
        with pytest.raises(ValueError, match="string"):
            parse_yaml_string("name: test\nsteps:\n  - id: 123\n    prompt: do thing\n")

    def test_yaml_too_large_raises(self):
        huge = "name: test\nsteps:\n" + "  - id: s\n    prompt: p\n" * 100000
        with pytest.raises(ValueError, match="too large"):
            parse_yaml_string(huge)

    def test_minimal_valid_workflow(self):
        wf = parse_yaml_string("name: minimal\nsteps:\n  - id: s1\n    prompt: do thing\n")
        assert wf.name == "minimal"
        assert len(wf.steps) == 1

    def test_approval_step_message_fallback(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: approve\n    type: approval\n    approval_config:\n      message: 'Please approve'\n")
        assert wf.steps[0].prompt == "Please approve"

    def test_non_prompt_type_gets_placeholder(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: h\n    type: http\n    http_config:\n      url: https://example.com\n")
        assert wf.steps[0].prompt == "http step"


# =============================================================================
# Section 21: DAG validation comprehensive
# =============================================================================

class TestDagValidationComprehensive:

    def _make_wf(self, steps, **kwargs):
        return WorkflowDefinition(
            name=kwargs.get("name", "test"), description="test",
            default_model="sonnet", default_max_turns=10, default_timeout=300,
            steps=steps, **{k: v for k, v in kwargs.items() if k != "name"},
        )

    def test_empty_name_rejected(self):
        errors = validate(self._make_wf([_step(id="s1")], name=""))
        assert any("name" in e.lower() for e in errors)

    def test_name_too_long_rejected(self):
        errors = validate(self._make_wf([_step(id="s1")], name="x" * 201))
        assert any("too long" in e for e in errors)

    def test_no_steps_rejected(self):
        errors = validate(self._make_wf([]))
        assert any("at least one step" in e for e in errors)

    def test_too_many_steps_rejected(self):
        errors = validate(self._make_wf([_step(id=f"s_{i}") for i in range(501)]))
        assert any("too many" in e for e in errors)

    def test_duplicate_step_ids_rejected(self):
        errors = validate(self._make_wf([_step(id="dup"), _step(id="dup")]))
        assert any("Duplicate" in e for e in errors)

    def test_invalid_step_id_characters(self):
        errors = validate(self._make_wf([_step(id="step with spaces")]))
        assert any("invalid" in e.lower() for e in errors)

    def test_unknown_dependency(self):
        step = _step(id="s1")
        step.depends_on = ["nonexistent"]
        errors = validate(self._make_wf([step]))
        assert any("unknown step" in e for e in errors)

    def test_unknown_step_type(self):
        step = _step(id="s1")
        step.type = "magical_step"
        errors = validate(self._make_wf([step]))
        assert any("unknown type" in e for e in errors)

    def test_http_step_without_url(self):
        errors = validate(self._make_wf([_step(id="h1", type="http", http_config=HttpConfig(url=""))]))
        assert any("url" in e.lower() for e in errors)

    def test_code_step_without_code(self):
        errors = validate(self._make_wf([_step(id="c1", type="code", code_config=CodeConfig(code=""))]))
        assert any("code" in e.lower() for e in errors)

    def test_loop_max_iterations_zero(self):
        errors = validate(self._make_wf([_step(id="l1", type="loop", loop_config=LoopConfig(over="input.items", max_iterations=0))]))
        assert any("max_iterations" in e for e in errors)

    def test_sensor_zero_interval(self):
        errors = validate(self._make_wf([_step(id="se1", type="sensor", sensor_config=SensorConfig(url="https://example.com", condition="True", check_interval=0))]))
        assert any("check_interval" in e for e in errors)

    def test_retry_invalid_backoff(self):
        errors = validate(self._make_wf([_step(id="r1", retry=RetryConfig(backoff="linear"))]))
        assert any("backoff" in e for e in errors)

    def test_step_timeout_zero(self):
        errors = validate(self._make_wf([_step(id="s1", timeout=0)]))
        assert any("timeout" in e for e in errors)

    def test_step_max_turns_over_1000(self):
        errors = validate(self._make_wf([_step(id="s1", max_turns=1001)]))
        assert any("max_turns" in e for e in errors)

    def test_memory_invalid_scope(self):
        errors = validate(self._make_wf([_step(id="s1")], memory=MemoryConfig(scope="invalid")))
        assert any("scope" in e.lower() for e in errors)


# =============================================================================
# Section 22: DAG cycle detection and build_plan
# =============================================================================

class TestDagCycleDetection:

    def test_simple_cycle(self):
        wf = parse_yaml_string("name: cycle\nsteps:\n  - id: a\n    depends_on: [b]\n    prompt: A\n  - id: b\n    depends_on: [a]\n    prompt: B\n")
        errors = validate(wf)
        assert any("cycle" in e.lower() for e in errors)

    def test_self_cycle(self):
        wf = parse_yaml_string("name: self_cycle\nsteps:\n  - id: a\n    depends_on: [a]\n    prompt: A\n")
        errors = validate(wf)
        assert any("cycle" in e.lower() for e in errors)

    def test_diamond_dag_no_cycle(self):
        wf = parse_yaml_string("name: diamond\nsteps:\n  - id: start\n    prompt: Start\n  - id: left\n    depends_on: [start]\n    prompt: Left\n  - id: right\n    depends_on: [start]\n    prompt: Right\n  - id: end\n    depends_on: [left, right]\n    prompt: End\n")
        cycle_errors = [e for e in validate(wf) if "cycle" in e.lower()]
        assert len(cycle_errors) == 0

    def test_build_plan_parallel_stages(self):
        wf = parse_yaml_string("name: p\nsteps:\n  - id: a\n    prompt: A\n  - id: b\n    prompt: B\n  - id: c\n    depends_on: [a, b]\n    prompt: C\n")
        plan = build_plan(wf)
        assert len(plan.stages) == 2
        assert set(plan.stages[0]) == {"a", "b"}

    def test_build_plan_linear_chain(self):
        wf = parse_yaml_string("name: chain\nsteps:\n  - id: s1\n    prompt: S1\n  - id: s2\n    depends_on: [s1]\n    prompt: S2\n  - id: s3\n    depends_on: [s2]\n    prompt: S3\n")
        plan = build_plan(wf)
        assert len(plan.stages) == 3

    def test_build_plan_wide_parallel(self):
        wf = parse_yaml_string("name: wide\nsteps:\n  - id: a\n    prompt: A\n  - id: b\n    prompt: B\n  - id: c\n    prompt: C\n")
        plan = build_plan(wf)
        assert len(plan.stages) == 1
        assert set(plan.stages[0]) == {"a", "b", "c"}

    def test_build_plan_cycle_raises(self):
        steps = [
            StepDefinition(id="a", depends_on=["b"], prompt="A"),
            StepDefinition(id="b", depends_on=["a"], prompt="B"),
        ]
        wf = WorkflowDefinition(name="cycle", description="", default_model="sonnet", default_max_turns=10, default_timeout=300, steps=steps)
        with pytest.raises(ValueError, match="unschedulable"):
            build_plan(wf)


# =============================================================================
# Section 23: Webhook dispatcher edge cases
# =============================================================================

class TestWebhookDispatcherEdgeCases:

    def test_rejects_javascript_scheme(self):
        from sandcastle.webhooks.dispatcher import validate_callback_url
        with pytest.raises(ValueError, match="http"):
            validate_callback_url("javascript:alert(1)")

    def test_rejects_file_scheme(self):
        from sandcastle.webhooks.dispatcher import validate_callback_url
        with pytest.raises(ValueError, match="http"):
            validate_callback_url("file:///etc/passwd")

    def test_rejects_ipv6_private(self):
        from sandcastle.webhooks.dispatcher import validate_callback_url
        with patch("socket.getaddrinfo", return_value=[(10, 1, 6, "", ("fc00::1", 443, 0, 0))]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://ipv6private.example/hook")

    def test_accepts_multiple_resolved_ips_if_all_public(self):
        from sandcastle.webhooks.dispatcher import validate_callback_url
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("93.184.216.35", 443)),
        ]):
            assert validate_callback_url("https://example.com/hook") == "https://example.com/hook"

    def test_rejects_if_any_ip_is_private(self):
        from sandcastle.webhooks.dispatcher import validate_callback_url
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]):
            with pytest.raises(ValueError, match="blocked"):
                validate_callback_url("https://dual-homed.example/hook")

    @pytest.mark.asyncio
    async def test_dispatch_webhook_with_full_payload(self):
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        captured_body = {}
        mock_response = MagicMock(status_code=200)

        async def capture_post(url, content=None, headers=None):
            captured_body["data"] = json.loads(content)
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://ok.com"),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            await dispatch_webhook(
                url="https://ok.com", event="workflow.failed", run_id="r-1",
                workflow="wf", status="failed", outputs={"key": "val"},
                costs=0.05, duration_seconds=12.5, error="Something broke",
            )

        payload = captured_body["data"]
        assert payload["event"] == "workflow.failed"
        assert payload["outputs"] == {"key": "val"}
        assert payload["costs"] == 0.05
        assert payload["error"] == "Something broke"


# =============================================================================
# Section 24: API routes - _validate_workflow_input edge cases
# =============================================================================

class TestValidateWorkflowInputEdgeCases:

    def _validate(self, data, schema):
        from sandcastle.api.routes import _validate_workflow_input
        return _validate_workflow_input(data, schema)

    def test_schema_not_dict(self):
        assert any("dict" in e for e in self._validate({}, "not a dict"))

    def test_empty_schema_no_errors(self):
        assert self._validate({}, {}) == []

    def test_integer_already_int(self):
        data = {"count": 42}
        assert self._validate(data, {"properties": {"count": {"type": "integer"}}}) == []
        assert data["count"] == 42

    def test_array_string_not_json(self):
        assert any("JSON array" in e for e in self._validate({"items": "not json"}, {"properties": {"items": {"type": "array"}}}))

    def test_multiple_required_fields_missing(self):
        assert len(self._validate({}, {"required": ["a", "b", "c"], "properties": {}})) == 3

    def test_required_field_is_none(self):
        assert len(self._validate({"name": None}, {"required": ["name"]})) == 1


# =============================================================================
# Section 25: API routes - _load_workflow_yaml security
# =============================================================================

class TestLoadWorkflowYamlSecurity:

    def test_empty_name_raises(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(ValueError, match="empty"):
            _load_workflow_yaml("")

    def test_path_traversal_double_dot(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("../../../etc/passwd")

    def test_path_traversal_slash(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("/etc/passwd")

    def test_path_traversal_backslash(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("..\\..\\etc\\passwd")


# =============================================================================
# Section 26: Auth module tests
# =============================================================================

class TestAuthModule:

    def test_hash_key_deterministic(self):
        from sandcastle.api.auth import hash_key
        assert hash_key("test-key") == hash_key("test-key")

    def test_hash_key_different_inputs(self):
        from sandcastle.api.auth import hash_key
        assert hash_key("key-1") != hash_key("key-2")

    def test_hash_key_returns_hex_64(self):
        from sandcastle.api.auth import hash_key
        h = hash_key("some-key")
        assert len(h) == 64
        int(h, 16)

    def test_generate_api_key_format(self):
        from sandcastle.api.auth import generate_api_key
        assert generate_api_key().startswith("sc_")

    def test_generate_api_key_unique(self):
        from sandcastle.api.auth import generate_api_key
        assert len({generate_api_key() for _ in range(100)}) == 100


# =============================================================================
# Section 27: resolve_storage_refs
# =============================================================================

class TestResolveStorageRefs:

    @pytest.mark.asyncio
    async def test_storage_ref_replaced(self):
        from sandcastle.engine.executor import resolve_storage_refs
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_dir=tmpdir)
            await storage.write("docs/readme.txt", "Hello from storage")
            result = await resolve_storage_refs("Read this: {storage.docs/readme.txt}", storage)
            assert result == "Read this: Hello from storage"

    @pytest.mark.asyncio
    async def test_storage_ref_not_found_keeps_placeholder(self):
        from sandcastle.engine.executor import resolve_storage_refs
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await resolve_storage_refs("Read: {storage.nonexistent.txt}", LocalStorage(base_dir=tmpdir))
            assert "{storage.nonexistent.txt}" in result

    @pytest.mark.asyncio
    async def test_no_storage_refs_unchanged(self):
        from sandcastle.engine.executor import resolve_storage_refs
        from sandcastle.engine.storage import LocalStorage
        with tempfile.TemporaryDirectory() as tmpdir:
            assert await resolve_storage_refs("No refs here", LocalStorage(base_dir=tmpdir)) == "No refs here"


# =============================================================================
# Section 28: DAG env var resolution
# =============================================================================

class TestEnvVarResolution:

    def test_env_var_in_webhook(self):
        os.environ["TEST_WEBHOOK_URL_UDA"] = "https://example.com/hook"
        try:
            wf = parse_yaml_string("name: test-env\nsteps:\n  - id: s1\n    prompt: do thing\non_complete:\n  webhook: '${TEST_WEBHOOK_URL_UDA}'\n")
            assert wf.on_complete.webhook == "https://example.com/hook"
        finally:
            del os.environ["TEST_WEBHOOK_URL_UDA"]

    def test_missing_env_var_resolves_empty(self):
        wf = parse_yaml_string("name: test-env\nsteps:\n  - id: s1\n    prompt: do thing\non_complete:\n  webhook: '${NONEXISTENT_VAR_UDA_12345}'\n")
        assert wf.on_complete.webhook == ""


# =============================================================================
# Section 29: WorkflowDefinition.get_step
# =============================================================================

class TestWorkflowDefinitionGetStep:

    def test_get_existing_step(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: alpha\n    prompt: Alpha\n  - id: beta\n    prompt: Beta\n")
        assert wf.get_step("alpha").id == "alpha"

    def test_get_nonexistent_step_raises(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: alpha\n    prompt: Alpha\n")
        with pytest.raises(ValueError, match="not found"):
            wf.get_step("nonexistent")


# =============================================================================
# Section 30: StepResult and WorkflowResult dataclasses
# =============================================================================

class TestDataclasses:

    def test_step_result_defaults(self):
        r = StepResult(step_id="s1")
        assert r.status == "completed"
        assert r.cost_usd == 0.0
        assert r.error is None
        assert r.attempt == 1

    def test_workflow_result_defaults(self):
        r = WorkflowResult(run_id="r1", outputs={}, total_cost_usd=0.0, status="completed")
        assert r.error is None
        assert r.started_at is None


# =============================================================================
# Section 31: StepBlocked and WorkflowPaused exceptions
# =============================================================================

class TestExceptions:

    def test_step_blocked_attributes(self):
        from sandcastle.engine.executor import StepBlocked
        exc = StepBlocked(step_id="s1", reason="Policy violation")
        assert exc.step_id == "s1"
        assert "Policy violation" in str(exc)

    def test_workflow_paused_attributes(self):
        from sandcastle.engine.executor import WorkflowPaused
        exc = WorkflowPaused(approval_id="a-1", run_id="r-1")
        assert exc.approval_id == "a-1"
        assert "a-1" in str(exc)


# =============================================================================
# Section 32: DAG config parsing round-trip
# =============================================================================

class TestDagConfigParsing:

    def test_http_config_full(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: http1\n    type: http\n    http_config:\n      url: https://api.example.com/data\n      method: POST\n      headers:\n        X-Custom: value\n      body: '{\"key\": \"val\"}'\n      auth: 'bearer:token123'\n")
        cfg = wf.steps[0].http_config
        assert cfg.url == "https://api.example.com/data"
        assert cfg.method == "POST"
        assert cfg.auth == "bearer:token123"

    def test_sensor_config(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: sensor1\n    type: sensor\n    sensor_config:\n      url: https://api.example.com/status\n      check_interval: 10\n      timeout: 120\n      condition: 'status_code == 200'\n")
        cfg = wf.steps[0].sensor_config
        assert cfg.check_interval == 10
        assert cfg.timeout == 120

    def test_model_pool_auto(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: s1\n    prompt: Do thing\n    model_pool: auto\n    slo:\n      quality_min: 0.8\n")
        assert wf.steps[0].model_pool is not None
        assert len(wf.steps[0].model_pool) >= 3

    def test_step_memory_bool_shorthand(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: s1\n    prompt: Do thing\n    memory: true\n")
        assert wf.steps[0].memory.read is True
        assert wf.steps[0].memory.write is True

    def test_autopilot_config(self):
        wf = parse_yaml_string("name: test\nsteps:\n  - id: s1\n    prompt: Do thing\n    autopilot:\n      enabled: true\n      optimize_for: cost\n      variants:\n        - id: fast\n          model: haiku\n      evaluation:\n        method: llm_judge\n        criteria: Quality\n      sample_rate: 0.5\n")
        ap = wf.steps[0].autopilot
        assert ap.enabled is True
        assert len(ap.variants) == 1
        assert ap.sample_rate == 0.5


# =============================================================================
# Section 33: Telemetry module safety
# =============================================================================

class TestTelemetryCapture:

    def test_capture_step_error_does_not_raise(self):
        from sandcastle.engine.telemetry import capture_step_error
        capture_step_error(Exception("test"), step_id="s1", step_type="standard", model="sonnet", workflow_name="test", run_id="r-1", attempt=1)

    def test_set_workflow_context_does_not_raise(self):
        from sandcastle.engine.telemetry import set_workflow_context
        set_workflow_context(workflow_name="test", run_id="r-1", sandbox_backend="local")


# =============================================================================
# Section 34: Events module
# =============================================================================

class TestEventBus:

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        from sandcastle.engine.events import EventBus
        bus = EventBus()
        queue = await bus.subscribe()
        try:
            bus.publish("run.started", {"key": "value"})
            event = queue.get_nowait()
            assert event["type"] == "run.started"
            assert event["data"]["key"] == "value"
            assert "timestamp" in event
        finally:
            await bus.unsubscribe(queue)

    def test_publish_no_subscribers(self):
        from sandcastle.engine.events import EventBus
        bus = EventBus()
        # Should not raise even with no subscribers
        bus.publish("run.completed", {"data": "test"})

    @pytest.mark.asyncio
    async def test_subscriber_count(self):
        from sandcastle.engine.events import EventBus
        bus = EventBus()
        assert bus.subscriber_count == 0
        q = await bus.subscribe()
        assert bus.subscriber_count == 1
        await bus.unsubscribe(q)
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_subscriber_limit(self):
        from sandcastle.engine.events import EventBus
        bus = EventBus()
        queues = []
        for _ in range(bus.MAX_SUBSCRIBERS):
            queues.append(await bus.subscribe())
        with pytest.raises(RuntimeError, match="subscriber limit"):
            await bus.subscribe()
        for q in queues:
            await bus.unsubscribe(q)


# =============================================================================
# Section 35: HTTP step SSRF prevention
# =============================================================================

class TestHttpStepSsrf:

    @pytest.mark.asyncio
    async def test_http_step_blocks_localhost(self):
        step = _step(id="http1", type="http", http_config=HttpConfig(url="http://localhost:8080/internal", method="GET"))
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 8080))]):
            r = await _execute_http_step(step, _ctx())
        assert r.status == "failed"
        assert "blocked" in r.error.lower()

    @pytest.mark.asyncio
    async def test_http_step_blocks_metadata_endpoint(self):
        step = _step(id="http2", type="http", http_config=HttpConfig(url="http://169.254.169.254/latest/meta-data/", method="GET"))
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 80))]):
            r = await _execute_http_step(step, _ctx())
        assert r.status == "failed"
        assert "blocked" in r.error.lower()

    @pytest.mark.asyncio
    async def test_http_step_blocks_ftp_scheme(self):
        r = await _execute_http_step(_step(id="http3", type="http", http_config=HttpConfig(url="ftp://evil.com/data", method="GET")), _ctx())
        assert r.status == "failed"
        assert "http" in r.error.lower()


# =============================================================================
# Section 36: Sensor step SSRF
# =============================================================================

class TestSensorStepSsrf:

    @pytest.mark.asyncio
    async def test_sensor_blocks_private_ip(self):
        step = _step(id="sensor1", type="sensor", sensor_config=SensorConfig(url="http://10.0.0.1:8080/status", condition="True", check_interval=1, timeout=5))
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 8080))]):
            r = await _execute_sensor_step(step, _ctx())
        assert r.status == "failed"
        assert "blocked" in r.error.lower()

    @pytest.mark.asyncio
    async def test_sensor_blocks_ftp_scheme(self):
        r = await _execute_sensor_step(_step(id="sensor2", type="sensor", sensor_config=SensorConfig(url="ftp://evil.com/data", condition="True", check_interval=1, timeout=5)), _ctx())
        assert r.status == "failed"
        assert "http" in r.error.lower()

    @pytest.mark.asyncio
    async def test_sensor_missing_config(self):
        r = await _execute_sensor_step(_step(id="sensor3", type="sensor"), _ctx())
        assert "Missing sensor_config" in r.error


# =============================================================================
# Section 37: Delegate step edge cases
# =============================================================================

class TestDelegateStepEdgeCases:

    @pytest.mark.asyncio
    async def test_delegate_missing_config(self):
        from sandcastle.engine.storage import LocalStorage
        r = await _execute_delegate_step(_step(id="del1", type="delegate"), _ctx(), LocalStorage(base_dir=tempfile.mkdtemp()))
        assert r.status == "failed"
        assert "Missing delegate_config" in r.error

    @pytest.mark.asyncio
    async def test_delegate_nonexistent_workflow(self):
        from sandcastle.engine.storage import LocalStorage
        step = _step(id="del1", type="delegate", delegate_config=DelegateConfig(workflow="nonexistent_workflow", task_description="Analyze"))
        r = await _execute_delegate_step(step, _ctx(), LocalStorage(base_dir=tempfile.mkdtemp()))
        assert r.status == "failed"
        assert "not found" in r.error.lower()


# =============================================================================
# Section 38: Property-based edge cases
# =============================================================================

class TestPropertyBasedEdgeCases:

    def test_resolve_variable_unicode_input(self):
        assert resolve_variable("input.name", _ctx(input={"name": "Hello"})) == "Hello"

    def test_resolve_variable_empty_string_input(self):
        assert resolve_variable("input.name", _ctx(input={"name": ""})) == ""

    def test_resolve_templates_very_long_input(self):
        long_val = "x" * 100000
        result = resolve_templates("{input.data}", _ctx(input={"data": long_val}))
        assert len(result) == 100000

    def test_cache_key_with_empty_strings(self):
        key = _compute_cache_key("", "", "", "")
        assert len(key) == 64

    def test_resolve_variable_with_integer_zero(self):
        assert resolve_variable("input.count", _ctx(input={"count": 0})) == 0

    def test_resolve_variable_with_false_value(self):
        assert resolve_variable("input.flag", _ctx(input={"flag": False})) is False

    def test_resolve_templates_empty_template(self):
        assert resolve_templates("", _ctx()) == ""


# =============================================================================
# Section 39: create_storage factory
# =============================================================================

class TestCreateStorage:

    def test_local_storage_default(self):
        from sandcastle.engine.storage import LocalStorage, create_storage
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.storage_backend = "local"
            assert isinstance(create_storage(), LocalStorage)

    def test_s3_storage_creation(self):
        from sandcastle.engine.storage import S3Storage, create_storage
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.storage_backend = "s3"
            mock_settings.storage_bucket = "my-bucket"
            mock_settings.storage_endpoint = "http://localhost:9000"
            mock_settings.aws_access_key_id = "access_key"
            mock_settings.aws_secret_access_key = "secret_key"
            storage = create_storage()
        assert isinstance(storage, S3Storage)
        assert storage.bucket == "my-bucket"


# =============================================================================
# Section 40: Rate limit module
# =============================================================================

class TestRateLimitBasic:

    def test_in_memory_backend_creation(self):
        from sandcastle.api.rate_limit import InMemoryBackend
        backend = InMemoryBackend()
        assert backend is not None
        assert backend.active_keys == 0

    @pytest.mark.asyncio
    async def test_in_memory_check_under_limit(self):
        from sandcastle.api.rate_limit import InMemoryBackend
        backend = InMemoryBackend()
        # check_and_increment returns current count (1-indexed)
        count = await backend.check_and_increment("test-key", max_requests=10, window_seconds=60)
        assert count == 1  # First request, count=0+1=1, under limit of 10

    @pytest.mark.asyncio
    async def test_in_memory_check_over_limit(self):
        from sandcastle.api.rate_limit import InMemoryBackend
        backend = InMemoryBackend()
        for _ in range(10):
            await backend.check_and_increment("flood-key", max_requests=10, window_seconds=60)
        # 11th request should be over limit (count=11 > max_requests=10)
        count = await backend.check_and_increment("flood-key", max_requests=10, window_seconds=60)
        assert count == 11  # Over the limit
