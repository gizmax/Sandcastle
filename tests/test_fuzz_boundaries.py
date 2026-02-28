"""Fuzz and boundary tests for Sandcastle.

Exercises extreme/unexpected inputs across DAG parsing, executor,
config validation, memory, SDK, and rate limiter.  Every test is
designed to either pass cleanly or expose a real bug that gets fixed
alongside the test.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. DAG parsing fuzz
# ---------------------------------------------------------------------------

from sandcastle.engine.dag import (
    ExecutionPlan,
    LoopConfig,
    RaceConfig,
    StepDefinition,
    WorkflowDefinition,
    _detect_cycles,
    _parse_raw,
    build_plan,
    parse_yaml_string,
    validate,
)


def _minimal_workflow(**overrides) -> dict:
    """Return the smallest valid workflow YAML dict."""
    base = {
        "name": "test",
        "steps": [{"id": "step1", "prompt": "do something"}],
    }
    base.update(overrides)
    return base


def _make_wf(steps: list[StepDefinition], **kw) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=kw.get("name", "test"),
        description="",
        default_model="sonnet",
        default_max_turns=10,
        default_timeout=300,
        steps=steps,
    )


# -- Empty / missing --

class TestDagEmpty:
    def test_empty_yaml_string(self):
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("")

    def test_whitespace_only_yaml(self):
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("   \n\t  ")

    def test_null_yaml_document(self):
        with pytest.raises(ValueError, match="None"):
            parse_yaml_string("---\n")

    def test_yaml_scalar_not_mapping(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_yaml_string("42")

    def test_yaml_list_not_mapping(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_yaml_string("- item1\n- item2")

    def test_no_steps_key(self):
        """Workflow with no steps key should parse but fail validation."""
        wf = _parse_raw({"name": "test"})
        errors = validate(wf)
        assert any("at least one step" in e for e in errors)

    def test_empty_steps_list(self):
        wf = _parse_raw({"name": "test", "steps": []})
        errors = validate(wf)
        assert any("at least one step" in e for e in errors)

    def test_steps_not_a_list(self):
        with pytest.raises(ValueError, match="list"):
            _parse_raw({"name": "test", "steps": "not_a_list"})


# -- Step IDs --

class TestDagStepIds:
    def test_empty_step_id(self):
        with pytest.raises(ValueError, match="non-empty"):
            _parse_raw({"name": "test", "steps": [{"id": "", "prompt": "x"}]})

    def test_none_step_id(self):
        with pytest.raises(ValueError, match="non-empty"):
            _parse_raw({"name": "test", "steps": [{"id": None, "prompt": "x"}]})

    def test_numeric_step_id(self):
        with pytest.raises(ValueError, match="string"):
            _parse_raw({"name": "test", "steps": [{"id": 123, "prompt": "x"}]})

    def test_very_long_step_id(self):
        """ID > 100 chars should be caught by validation regex."""
        long_id = "a" * 10000
        wf = _parse_raw({"name": "test", "steps": [{"id": long_id, "prompt": "x"}]})
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_unicode_step_id(self):
        wf = _parse_raw({"name": "test", "steps": [{"id": "kroek_unicode", "prompt": "x"}]})
        errors = validate(wf)
        # plain ascii with underscore is fine
        assert not any("invalid" in e.lower() for e in errors)

    def test_step_id_with_slashes(self):
        wf = _parse_raw({"name": "test", "steps": [{"id": "path/to/step", "prompt": "x"}]})
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_step_id_with_dots(self):
        wf = _parse_raw({"name": "test", "steps": [{"id": "step.name", "prompt": "x"}]})
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_step_id_with_spaces(self):
        wf = _parse_raw({"name": "test", "steps": [{"id": "step name", "prompt": "x"}]})
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_step_id_starting_with_hyphen(self):
        wf = _parse_raw({"name": "test", "steps": [{"id": "-step", "prompt": "x"}]})
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_step_id_single_char(self):
        wf = _parse_raw({"name": "test", "steps": [{"id": "a", "prompt": "x"}]})
        errors = validate(wf)
        # Single char should be valid (matches ^[a-zA-Z0-9_]{1}$)
        assert not any("invalid" in e.lower() for e in errors)

    def test_step_id_exactly_100_chars(self):
        """100 chars is the max. Should be valid."""
        sid = "a" * 100
        wf = _parse_raw({"name": "test", "steps": [{"id": sid, "prompt": "x"}]})
        errors = validate(wf)
        assert not any("invalid" in e.lower() for e in errors)

    def test_step_id_101_chars(self):
        """101 chars exceeds the {0,99} in the regex."""
        sid = "a" * 101
        wf = _parse_raw({"name": "test", "steps": [{"id": sid, "prompt": "x"}]})
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_duplicate_step_ids(self):
        wf = _parse_raw({
            "name": "test",
            "steps": [
                {"id": "dup", "prompt": "a"},
                {"id": "dup", "prompt": "b"},
            ],
        })
        errors = validate(wf)
        assert any("Duplicate" in e for e in errors)


# -- Circular dependencies --

class TestDagCycles:
    def test_self_reference(self):
        steps = [StepDefinition(id="self_loop", prompt="x", depends_on=["self_loop"])]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("Cycle" in e or "cycle" in e.lower() for e in errors)

    def test_two_node_cycle(self):
        steps = [
            StepDefinition(id="alpha", prompt="a", depends_on=["beta"]),
            StepDefinition(id="beta", prompt="b", depends_on=["alpha"]),
        ]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("ycle" in e for e in errors)

    def test_three_node_cycle(self):
        steps = [
            StepDefinition(id="aaa", prompt="a", depends_on=["ccc"]),
            StepDefinition(id="bbb", prompt="b", depends_on=["aaa"]),
            StepDefinition(id="ccc", prompt="c", depends_on=["bbb"]),
        ]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("ycle" in e for e in errors)

    def test_build_plan_raises_on_cycle(self):
        steps = [
            StepDefinition(id="aaa", prompt="a", depends_on=["bbb"]),
            StepDefinition(id="bbb", prompt="b", depends_on=["aaa"]),
        ]
        wf = _make_wf(steps)
        with pytest.raises(ValueError, match="unschedulable"):
            build_plan(wf)


# -- Step count boundaries --

class TestDagStepCounts:
    def test_exactly_500_steps(self):
        steps = [StepDefinition(id=f"s{i}", prompt="x") for i in range(500)]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert not any("too many" in e.lower() for e in errors)

    def test_501_steps(self):
        steps = [StepDefinition(id=f"s{i}", prompt="x") for i in range(501)]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("too many" in e.lower() for e in errors)


# -- Workflow name boundaries --

class TestDagWorkflowName:
    def test_missing_name(self):
        with pytest.raises(ValueError, match="name"):
            _parse_raw({"steps": [{"id": "s1", "prompt": "x"}]})

    def test_empty_name_validation(self):
        wf = _parse_raw({"name": "", "steps": [{"id": "s1", "prompt": "x"}]})
        errors = validate(wf)
        assert any("name is required" in e.lower() for e in errors)

    def test_name_200_chars(self):
        wf = _parse_raw({"name": "a" * 200, "steps": [{"id": "s1", "prompt": "x"}]})
        errors = validate(wf)
        assert not any("name too long" in e.lower() for e in errors)

    def test_name_201_chars(self):
        wf = _parse_raw({"name": "a" * 201, "steps": [{"id": "s1", "prompt": "x"}]})
        errors = validate(wf)
        assert any("too long" in e.lower() for e in errors)


# -- YAML size --

class TestDagYamlSize:
    def test_yaml_at_exactly_1mb(self):
        """1MB is the limit - should parse OK."""
        # Build a valid YAML string close to 1MB
        padding = "a" * (1024 * 1024 - 100)
        yaml_str = f"name: test\ndescription: {padding}\nsteps:\n  - id: s1\n    prompt: do"
        if len(yaml_str) > 1024 * 1024:
            yaml_str = yaml_str[:1024 * 1024]
        # Should not raise for size (may raise for other reasons)
        try:
            parse_yaml_string(yaml_str)
        except ValueError as e:
            assert "too large" not in str(e).lower()

    def test_yaml_over_1mb(self):
        yaml_str = "name: test\ndescription: " + "x" * (1024 * 1024 + 100)
        with pytest.raises(ValueError, match="too large"):
            parse_yaml_string(yaml_str)


# -- YAML with null values --

class TestDagNullValues:
    def test_null_prompt(self):
        """Step with null prompt should get placeholder."""
        wf = _parse_raw({
            "name": "test",
            "steps": [{"id": "s1", "prompt": None, "type": "http", "http_config": {"url": "http://x.com"}}],
        })
        # Non-prompt types get a placeholder
        assert wf.steps[0].prompt == "http step"

    def test_null_depends_on(self):
        wf = _parse_raw({
            "name": "test",
            "steps": [{"id": "s1", "prompt": "x", "depends_on": None}],
        })
        # None should be treated as empty list by .get default
        assert wf.steps[0].depends_on is None or wf.steps[0].depends_on == []

    def test_null_model(self):
        wf = _parse_raw({
            "name": "test",
            "steps": [{"id": "s1", "prompt": "x", "model": None}],
        })
        # None falls through .get("model", "sonnet")
        assert wf.steps[0].model is None or wf.steps[0].model == "sonnet"


# -- Build plan edge cases --

class TestDagBuildPlan:
    def test_single_step_no_deps(self):
        steps = [StepDefinition(id="solo", prompt="x")]
        wf = _make_wf(steps)
        plan = build_plan(wf)
        assert plan.stages == [["solo"]]

    def test_linear_chain(self):
        steps = [
            StepDefinition(id="aaa", prompt="a"),
            StepDefinition(id="bbb", prompt="b", depends_on=["aaa"]),
            StepDefinition(id="ccc", prompt="c", depends_on=["bbb"]),
        ]
        wf = _make_wf(steps)
        plan = build_plan(wf)
        assert len(plan.stages) == 3

    def test_fully_parallel(self):
        steps = [StepDefinition(id=f"p{i}", prompt="x") for i in range(10)]
        wf = _make_wf(steps)
        plan = build_plan(wf)
        assert len(plan.stages) == 1
        assert len(plan.stages[0]) == 10

    def test_diamond_dependency(self):
        steps = [
            StepDefinition(id="root", prompt="x"),
            StepDefinition(id="left", prompt="x", depends_on=["root"]),
            StepDefinition(id="right", prompt="x", depends_on=["root"]),
            StepDefinition(id="join", prompt="x", depends_on=["left", "right"]),
        ]
        wf = _make_wf(steps)
        plan = build_plan(wf)
        assert plan.stages[0] == ["root"]
        assert sorted(plan.stages[1]) == ["left", "right"]
        assert plan.stages[2] == ["join"]


# -- Step type validation --

class TestDagStepTypes:
    def test_unknown_step_type(self):
        wf = _parse_raw({
            "name": "test",
            "steps": [{"id": "s1", "prompt": "x", "type": "nonexistent_type"}],
        })
        errors = validate(wf)
        assert any("unknown type" in e.lower() for e in errors)

    def test_loop_max_iterations_zero(self):
        steps = [StepDefinition(
            id="loop1", prompt="x", type="loop",
            loop_config=LoopConfig(over="{input.items}", max_iterations=0, step_ids=["s1"]),
        )]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("max_iterations" in e and ">= 1" in e for e in errors)

    def test_loop_max_iterations_over_10000(self):
        steps = [StepDefinition(
            id="loop1", prompt="x", type="loop",
            loop_config=LoopConfig(over="{input.items}", max_iterations=10001, step_ids=["s1"]),
        )]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("10000" in e for e in errors)

    def test_loop_max_iterations_exactly_10000(self):
        steps = [StepDefinition(
            id="loop1", prompt="x", type="loop",
            loop_config=LoopConfig(over="{input.items}", max_iterations=10000, step_ids=["s1"]),
        )]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert not any("max_iterations" in e for e in errors)

    def test_race_no_branches(self):
        steps = [StepDefinition(
            id="race1", prompt="x", type="race",
            race_config=RaceConfig(branches=[]),
        )]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("branches" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# 2. Executor fuzz
# ---------------------------------------------------------------------------

from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _UNRESOLVED,
    _check_budget,
    _safe_eval_expression,
    resolve_templates,
    resolve_variable,
)


def _ctx(**kwargs) -> RunContext:
    defaults = dict(
        run_id="00000000-0000-0000-0000-000000000000",
        input={"foo": "bar"},
        step_outputs={},
    )
    defaults.update(kwargs)
    return RunContext(**defaults)


class TestResolveVariable:
    def test_empty_var_path(self):
        ctx = _ctx()
        assert resolve_variable("", ctx) is _UNRESOLVED

    def test_nonexistent_input_key(self):
        ctx = _ctx()
        assert resolve_variable("input.nonexistent", ctx) is _UNRESOLVED

    def test_steps_with_no_step_id(self):
        ctx = _ctx()
        assert resolve_variable("steps.missing.output", ctx) is _UNRESOLVED

    def test_run_id_variable(self):
        ctx = _ctx(run_id="test-id")
        assert resolve_variable("run_id", ctx) == "test-id"

    def test_date_variable(self):
        ctx = _ctx()
        result = resolve_variable("date", ctx)
        assert isinstance(result, str)
        assert re.match(r"\d{4}-\d{2}-\d{2}", result)

    def test_unknown_root(self):
        ctx = _ctx()
        assert resolve_variable("something_random", ctx) is _UNRESOLVED

    def test_deeply_nested_input(self):
        nested = {"level1": {"level2": {"level3": "deep_value"}}}
        ctx = _ctx(input=nested)
        assert resolve_variable("input.level1.level2.level3", ctx) == "deep_value"

    def test_list_index_out_of_bounds(self):
        ctx = _ctx(input={"arr": [1, 2, 3]})
        assert resolve_variable("input.arr.99", ctx) is _UNRESOLVED

    def test_negative_list_index(self):
        ctx = _ctx(input={"arr": [1, 2, 3]})
        assert resolve_variable("input.arr.-1", ctx) is _UNRESOLVED

    def test_steps_output_none(self):
        ctx = _ctx(step_outputs={"s1": None})
        result = resolve_variable("steps.s1.output", ctx)
        assert result is None

    def test_steps_output_nested_field_on_none(self):
        ctx = _ctx(step_outputs={"s1": None})
        result = resolve_variable("steps.s1.output.field", ctx)
        assert result is None


class TestResolveTemplates:
    def test_no_templates(self):
        ctx = _ctx()
        result = resolve_templates("plain text", ctx)
        assert result == "plain text"

    def test_unclosed_brace(self):
        """Unclosed brace should be left as-is (regex won't match)."""
        ctx = _ctx()
        result = resolve_templates("broken {input.foo", ctx)
        assert result == "broken {input.foo"

    def test_nested_template_literal(self):
        """Nested braces like {steps.{input.x}.output} should NOT resolve inner."""
        ctx = _ctx(input={"x": "step1"}, step_outputs={"step1": "value1"})
        result = resolve_templates("nested {steps.{input.x}.output}", ctx)
        # The regex won't match nested braces, so it stays unresolved
        assert "{" in result

    def test_empty_template(self):
        ctx = _ctx()
        result = resolve_templates("", ctx)
        assert result == ""

    def test_adjacent_templates(self):
        ctx = _ctx(input={"aaa": "hello", "bbb": "world"})
        result = resolve_templates("{input.aaa}{input.bbb}", ctx)
        assert result == "helloworld"

    def test_unresolved_kept(self):
        ctx = _ctx()
        result = resolve_templates("{input.nonexistent}", ctx)
        assert "{input.nonexistent}" in result

    def test_dict_output_json_serialized(self):
        ctx = _ctx(step_outputs={"s1": {"key": "value"}})
        result = resolve_templates("{steps.s1.output}", ctx)
        assert '"key"' in result and '"value"' in result

    def test_list_output_json_serialized(self):
        ctx = _ctx(step_outputs={"s1": [1, 2, 3]})
        result = resolve_templates("{steps.s1.output}", ctx)
        assert "[1, 2, 3]" in result

    def test_none_output_becomes_string_none(self):
        ctx = _ctx(step_outputs={"s1": None})
        result = resolve_templates("{steps.s1.output}", ctx)
        assert "None" in result


class TestSafeEvalExpression:
    def test_simple_comparison(self):
        assert _safe_eval_expression("1 > 0", {}) is True

    def test_string_comparison(self):
        assert _safe_eval_expression("x == 'hello'", {"x": "hello"}) is True

    def test_len_function(self):
        assert _safe_eval_expression("len(items) > 2", {"items": [1, 2, 3]}) is True

    def test_empty_expression(self):
        with pytest.raises(Exception):
            _safe_eval_expression("", {})

    def test_whitespace_only(self):
        with pytest.raises(Exception):
            _safe_eval_expression("   ", {})

    def test_deeply_nested_parens(self):
        expr = "(" * 50 + "1" + ")" * 50
        result = _safe_eval_expression(expr, {})
        assert result == 1

    def test_division_by_zero(self):
        with pytest.raises(ZeroDivisionError):
            _safe_eval_expression("1 / 0", {})

    def test_boolean_logic(self):
        assert _safe_eval_expression("True and False", {"True": True, "False": False}) is False

    def test_int_constructor(self):
        assert _safe_eval_expression("int('42')", {}) == 42

    def test_float_constructor(self):
        assert _safe_eval_expression("float('3.14')", {}) == pytest.approx(3.14)

    def test_none_comparison(self):
        assert _safe_eval_expression("x is None", {"x": None, "None": None}) is True

    def test_import_blocked(self):
        """simpleeval should block __import__."""
        with pytest.raises(Exception):
            _safe_eval_expression("__import__('os')", {})


# -- Code step size boundary --

from sandcastle.engine.executor import _CODE_STEP_MAX_SIZE, _execute_code_step
from sandcastle.engine.dag import CodeConfig


class TestCodeStepBoundary:
    @pytest.mark.asyncio
    async def test_exact_size_limit(self):
        """Code at exactly 50000 bytes should execute."""
        code = "result = 1" + " " * (_CODE_STEP_MAX_SIZE - len("result = 1"))
        step = StepDefinition(
            id="code1", prompt="x", type="code",
            code_config=CodeConfig(code=code),
        )
        ctx = _ctx()
        result = await _execute_code_step(step, ctx)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_one_byte_over_limit(self):
        """Code at 50001 bytes should be rejected."""
        code = "x" * (_CODE_STEP_MAX_SIZE + 1)
        step = StepDefinition(
            id="code1", prompt="x", type="code",
            code_config=CodeConfig(code=code),
        )
        ctx = _ctx()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "too large" in result.error.lower()

    @pytest.mark.asyncio
    async def test_blocked_pattern_eval(self):
        step = StepDefinition(
            id="code1", prompt="x", type="code",
            code_config=CodeConfig(code="eval('1+1')"),
        )
        ctx = _ctx()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "blocked pattern" in result.error.lower()

    @pytest.mark.asyncio
    async def test_blocked_pattern_import(self):
        step = StepDefinition(
            id="code1", prompt="x", type="code",
            code_config=CodeConfig(code="__import__('os')"),
        )
        ctx = _ctx()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "blocked" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_code_config(self):
        step = StepDefinition(id="code1", prompt="x", type="code", code_config=None)
        ctx = _ctx()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "missing" in result.error.lower()

    @pytest.mark.asyncio
    async def test_empty_code(self):
        """Empty code should execute fine - just no result."""
        step = StepDefinition(
            id="code1", prompt="x", type="code",
            code_config=CodeConfig(code=""),
        )
        ctx = _ctx()
        result = await _execute_code_step(step, ctx)
        assert result.status == "completed"
        assert result.output is None


# -- Budget checker --

class TestCheckBudget:
    def test_no_budget(self):
        ctx = _ctx(max_cost_usd=None)
        assert _check_budget(ctx) is None

    def test_zero_budget(self):
        ctx = _ctx(max_cost_usd=0.0)
        assert _check_budget(ctx) is None

    def test_negative_budget(self):
        ctx = _ctx(max_cost_usd=-1.0)
        assert _check_budget(ctx) is None

    def test_budget_warning_at_80_pct(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[0.8])
        assert _check_budget(ctx) == "warning"

    def test_budget_exceeded_at_100_pct(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[1.0])
        assert _check_budget(ctx) == "exceeded"

    def test_budget_exceeded_over(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[2.0])
        assert _check_budget(ctx) == "exceeded"

    def test_budget_ok_under_80_pct(self):
        ctx = _ctx(max_cost_usd=1.0, costs=[0.5])
        assert _check_budget(ctx) is None


# -- Cancel flags --

from sandcastle.engine.executor import cancel_run_local, _cancel_flags, _cancel_flags_lock


class TestCancelFlags:
    @pytest.mark.asyncio
    async def test_cancel_flag_set_and_check(self):
        """Basic cancel flag lifecycle."""
        from sandcastle.engine.executor import _check_cancel
        _cancel_flags.clear()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = ""
            await cancel_run_local("run-abc")
            assert await _check_cancel("run-abc") is True
            # Flag persists until cleanup (not consumed on check)
            assert await _check_cancel("run-abc") is True


# ---------------------------------------------------------------------------
# 3. Config validation fuzz
# ---------------------------------------------------------------------------

from sandcastle.config import Settings


class TestConfigValidation:
    def test_sandbox_backend_unknown(self):
        s = Settings(sandbox_backend="unknown_backend")
        assert s.sandbox_backend == "e2b"

    def test_sandbox_backend_whitespace(self):
        s = Settings(sandbox_backend="  e2b  ")
        assert s.sandbox_backend == "e2b"

    def test_sandbox_backend_mixed_case(self):
        s = Settings(sandbox_backend="Docker")
        assert s.sandbox_backend == "docker"

    def test_sandbox_backend_unicode(self):
        s = Settings(sandbox_backend="e2b\u200b")  # zero-width space
        # Should fall back because it won't match after strip().lower()
        assert s.sandbox_backend == "e2b"

    def test_storage_backend_unknown(self):
        s = Settings(storage_backend="gcs")
        assert s.storage_backend == "local"

    def test_memory_backend_unknown(self):
        s = Settings(memory_backend="redis")
        assert s.memory_backend == "local"

    def test_log_level_unknown(self):
        s = Settings(log_level="TRACE")
        assert s.log_level == "info"

    def test_log_level_valid_uppercase(self):
        s = Settings(log_level="DEBUG")
        assert s.log_level == "debug"

    def test_max_concurrent_negative(self):
        s = Settings(max_concurrent_sandboxes=-5)
        assert s.max_concurrent_sandboxes == 1

    def test_max_concurrent_zero(self):
        s = Settings(max_concurrent_sandboxes=0)
        assert s.max_concurrent_sandboxes == 1

    def test_max_concurrent_one(self):
        s = Settings(max_concurrent_sandboxes=1)
        assert s.max_concurrent_sandboxes == 1

    def test_memory_admit_threshold_negative(self):
        s = Settings(memory_admit_threshold=-0.5)
        assert s.memory_admit_threshold == 0.0

    def test_memory_admit_threshold_over_one(self):
        s = Settings(memory_admit_threshold=1.5)
        assert s.memory_admit_threshold == 1.0

    def test_memory_admit_threshold_zero(self):
        s = Settings(memory_admit_threshold=0.0)
        assert s.memory_admit_threshold == 0.0

    def test_memory_admit_threshold_one(self):
        s = Settings(memory_admit_threshold=1.0)
        assert s.memory_admit_threshold == 1.0

    def test_memory_max_age_days_negative(self):
        s = Settings(memory_max_age_days=-10)
        assert s.memory_max_age_days == 0

    def test_failover_cooldown_zero(self):
        s = Settings(failover_cooldown_seconds=0.0)
        assert s.failover_cooldown_seconds == 60.0

    def test_failover_cooldown_negative(self):
        s = Settings(failover_cooldown_seconds=-5.0)
        assert s.failover_cooldown_seconds == 60.0

    def test_docker_pids_limit_zero(self):
        s = Settings(docker_pids_limit=0)
        assert s.docker_pids_limit == 100

    def test_docker_pids_limit_negative(self):
        s = Settings(docker_pids_limit=-1)
        assert s.docker_pids_limit == 100

    def test_docker_cpu_period_below_min(self):
        s = Settings(docker_cpu_period=500)
        assert s.docker_cpu_period == 100_000

    def test_docker_cpu_quota_below_min(self):
        s = Settings(docker_cpu_quota=100)
        assert s.docker_cpu_quota == 50_000

    def test_max_workflow_depth_zero(self):
        s = Settings(max_workflow_depth=0)
        assert s.max_workflow_depth == 5

    def test_max_workflow_depth_negative(self):
        s = Settings(max_workflow_depth=-1)
        assert s.max_workflow_depth == 5

    def test_max_workflow_depth_one(self):
        s = Settings(max_workflow_depth=1)
        assert s.max_workflow_depth == 1

    def test_data_dir_tilde_expansion(self):
        s = Settings(data_dir="~/mydata")
        assert "~" not in s.data_dir

    def test_is_local_mode_with_empty_db(self):
        s = Settings(database_url="")
        assert s.is_local_mode is True

    def test_is_local_mode_with_sqlite(self):
        s = Settings(database_url="sqlite:///test.db")
        assert s.is_local_mode is True

    def test_is_local_mode_with_postgres(self):
        s = Settings(database_url="postgresql://localhost/db")
        assert s.is_local_mode is False


# ---------------------------------------------------------------------------
# 4. Memory fuzz
# ---------------------------------------------------------------------------

from sandcastle.engine.memory import (
    _word_set,
    apply_decay,
    detect_conflicts,
    enrich_memory,
    score_importance,
    should_admit,
)


class TestMemoryScoreImportance:
    def test_empty_content(self):
        """Empty content should get low score (length penalty)."""
        score = score_importance("", [])
        assert score < 0.3

    def test_very_short_content(self):
        score = score_importance("hi", [])
        assert score < 0.5

    def test_very_long_content(self):
        score = score_importance("x" * 10000, [])
        # Long content gets a penalty
        assert score < 0.5

    def test_sweet_spot_length(self):
        content = "This is a nicely sized piece of content with useful information."
        score = score_importance(content, [])
        assert score >= 0.5

    def test_structured_data_bonus(self):
        content = "The result was {key: value} on 2024-01-15"
        score = score_importance(content, [])
        assert score > 0.5

    def test_high_overlap_penalty(self):
        existing = [{"memory": "The quick brown foxes jump over lazy dogs daily"}]
        content = "The quick brown foxes jump over lazy dogs nightly"
        score = score_importance(content, existing)
        # High overlap should reduce score
        assert score < 0.5

    def test_no_overlap_no_penalty(self):
        existing = [{"memory": "completely different topic about space exploration discoveries"}]
        content = "programming tutorial about python decorators usage patterns"
        score = score_importance(content, existing)
        assert score >= 0.4

    def test_score_clamped_to_0_1(self):
        """Score should always be between 0 and 1."""
        # Trigger maximum penalties
        existing = [{"memory": "abc def ghi jkl mno pqr stu"}] * 10
        score = score_importance("abc", existing)
        assert 0.0 <= score <= 1.0


class TestMemoryShouldAdmit:
    def test_empty_content_rejected(self):
        admitted, score, reason = should_admit("", [], threshold=0.0)
        assert not admitted
        assert "Empty" in reason

    def test_whitespace_only_rejected(self):
        admitted, score, reason = should_admit("   \n\t  ", [], threshold=0.0)
        assert not admitted

    def test_threshold_zero_admits_anything(self):
        admitted, score, reason = should_admit(
            "useful data about the deployment process", [], threshold=0.0,
        )
        assert admitted

    def test_threshold_one_rejects_most(self):
        admitted, score, reason = should_admit(
            "short", [], threshold=1.0,
        )
        assert not admitted

    def test_negative_threshold(self):
        """Negative threshold should still work (everything admitted)."""
        admitted, score, reason = should_admit(
            "useful data about something", [], threshold=-1.0,
        )
        assert admitted


class TestMemoryApplyDecay:
    def test_zero_max_age(self):
        """max_age_days=0 means no expiry - all memories kept."""
        mems = [{"memory": "test", "created_at": "2020-01-01T00:00:00Z"}]
        result = apply_decay(mems, max_age_days=0)
        assert len(result) == 1
        assert result[0]["relevance_score"] == 1.0

    def test_negative_max_age(self):
        """Negative max_age should behave like 0 (no expiry)."""
        mems = [{"memory": "test", "created_at": "2020-01-01T00:00:00Z"}]
        result = apply_decay(mems, max_age_days=-5)
        assert len(result) == 1

    def test_no_timestamp(self):
        """Missing created_at should get mid relevance."""
        mems = [{"memory": "test"}]
        result = apply_decay(mems, max_age_days=90)
        assert len(result) == 1
        assert result[0]["relevance_score"] == 0.5

    def test_empty_string_timestamp(self):
        mems = [{"memory": "test", "created_at": ""}]
        result = apply_decay(mems, max_age_days=90)
        assert len(result) == 1
        assert result[0]["relevance_score"] == 0.5

    def test_numeric_timestamp(self):
        now_ts = time.time()
        mems = [{"memory": "test", "created_at": now_ts}]
        result = apply_decay(mems, max_age_days=90)
        assert len(result) == 1
        assert result[0]["relevance_score"] > 0.9

    def test_invalid_timestamp_string(self):
        mems = [{"memory": "test", "created_at": "not-a-date"}]
        result = apply_decay(mems, max_age_days=90)
        assert len(result) == 1
        # Falls back to now, so recent
        assert result[0]["relevance_score"] > 0.9

    def test_empty_memories_list(self):
        result = apply_decay([], max_age_days=90)
        assert result == []

    def test_sorting_by_relevance(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        mems = [
            {"memory": "old", "created_at": (now - timedelta(days=80)).isoformat()},
            {"memory": "new", "created_at": now.isoformat()},
        ]
        result = apply_decay(mems, max_age_days=90)
        assert result[0]["memory"] == "new"
        assert result[1]["memory"] == "old"


class TestMemoryDetectConflicts:
    def test_empty_new_content(self):
        existing = [{"memory": "some existing memory data content"}]
        conflicts = detect_conflicts("", existing)
        assert conflicts == []

    def test_single_short_word(self):
        """Words <= 2 chars are filtered by _word_set."""
        existing = [{"memory": "ab cd ef gh"}]
        conflicts = detect_conflicts("ab", existing)
        assert conflicts == []

    def test_no_conflicts(self):
        existing = [{"memory": "completely unrelated topic about space exploration science"}]
        conflicts = detect_conflicts("python decorators are powerful programming tools", existing)
        assert len(conflicts) == 0

    def test_high_overlap_detected(self):
        existing = [{"memory": "the quick brown foxes jump over lazy dogs today"}]
        conflicts = detect_conflicts("the quick brown foxes jump over lazy dogs tomorrow", existing)
        assert len(conflicts) > 0

    def test_empty_existing_memories(self):
        conflicts = detect_conflicts("any content here about stuff", [])
        assert conflicts == []

    def test_empty_memory_text(self):
        existing = [{"memory": ""}]
        conflicts = detect_conflicts("some content about something", existing)
        assert conflicts == []


class TestMemoryWordSet:
    def test_empty_string(self):
        assert _word_set("") == set()

    def test_short_words_filtered(self):
        """Words <= 2 chars are excluded."""
        result = _word_set("a bb ccc")
        assert result == {"ccc"}

    def test_unicode_words(self):
        """Unicode alpha chars are not matched by [a-zA-Z0-9_]."""
        result = _word_set("hello world")
        assert "hello" in result

    def test_numbers_included(self):
        result = _word_set("test123 value456")
        assert "test123" in result
        assert "value456" in result


class TestMemoryEnrich:
    def test_empty_content(self):
        result = enrich_memory("")
        assert result["keywords"] == []
        assert "enriched_at" in result

    def test_content_with_data_pattern(self):
        result = enrich_memory("The date 2024-01-15 is important for release")
        assert "data" in result["tags"]

    def test_content_with_issue_pattern(self):
        result = enrich_memory("There was an error in the deployment process today")
        assert "issue" in result["tags"]

    def test_content_with_preference_pattern(self):
        result = enrich_memory("I prefer using Python for backend development work")
        assert "preference" in result["tags"]

    def test_custom_metadata(self):
        result = enrich_memory("test content something", metadata={"source": "test"})
        assert result["metadata"]["source"] == "test"

    def test_max_5_keywords(self):
        result = enrich_memory(
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
        )
        assert len(result["keywords"]) <= 5


# ---------------------------------------------------------------------------
# 5. SDK fuzz
# ---------------------------------------------------------------------------

from sandcastle.sdk import (
    SandcastleClient,
    SandcastleError,
    _extract_data,
    _parse_datetime,
    _parse_run,
    _parse_sse_lines,
)


class TestSdkParseDateTime:
    def test_none_input(self):
        assert _parse_datetime(None) is None

    def test_invalid_string(self):
        assert _parse_datetime("not-a-date") is None

    def test_valid_iso(self):
        dt = _parse_datetime("2024-01-15T10:30:00")
        assert dt is not None
        assert dt.day == 15

    def test_integer_input(self):
        assert _parse_datetime(12345) is None

    def test_empty_string(self):
        assert _parse_datetime("") is None


class TestSdkExtractData:
    def test_400_error(self):
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {
            "detail": {"error": {"code": "VALIDATION", "message": "Bad input"}}
        }
        resp.text = "Bad input"
        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.status_code == 400

    def test_500_error_no_json(self):
        resp = MagicMock()
        resp.status_code = 500
        resp.json.side_effect = Exception("Not JSON")
        resp.text = "Internal Server Error"
        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.status_code == 500
        assert "Internal Server Error" in exc_info.value.message

    def test_200_with_data_key(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {"key": "value"}}
        result = _extract_data(resp)
        assert result == {"key": "value"}

    def test_200_without_data_key(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"key": "value"}
        result = _extract_data(resp)
        assert result == {"key": "value"}


class TestSdkParseRun:
    def test_empty_dict(self):
        run = _parse_run({})
        assert run.run_id == ""
        assert run.status == "unknown"

    def test_full_run(self):
        run = _parse_run({
            "run_id": "abc",
            "status": "completed",
            "workflow_name": "test",
            "outputs": {"result": "ok"},
            "total_cost_usd": 0.05,
            "steps": [{"step_id": "s1", "status": "completed"}],
        })
        assert run.run_id == "abc"
        assert run.status == "completed"
        assert len(run.steps) == 1

    def test_run_with_none_steps(self):
        run = _parse_run({"run_id": "x", "status": "running", "steps": None})
        assert run.steps is None


class TestSdkParseSseLines:
    def test_empty_string(self):
        events = list(_parse_sse_lines(""))
        assert events == []

    def test_single_event(self):
        raw = "event: step_complete\ndata: {\"step_id\": \"s1\"}\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["step_id"] == "s1"
        assert events[0]["_event"] == "step_complete"

    def test_invalid_json_data(self):
        raw = "data: not-json\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert "raw" in events[0]

    def test_multi_line_data(self):
        raw = "data: {\"key\":\n data: \"value\"}\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1

    def test_no_trailing_newline(self):
        """Without double newline, buffered data is flushed as a final event.

        This handles interrupted streams (server crash, network timeout) where
        the last event would otherwise be silently dropped.
        """
        raw = "data: {\"key\": \"value\"}"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["key"] == "value"


class TestSdkClientInit:
    def test_default_params(self):
        with SandcastleClient() as client:
            assert client._client.base_url.host == "localhost"

    def test_custom_base_url(self):
        with SandcastleClient(base_url="http://example.com:9090") as client:
            assert "example.com" in str(client._client.base_url)

    def test_api_key_in_headers(self):
        with SandcastleClient(api_key="sk-test") as client:
            assert client._client.headers.get("X-API-Key") == "sk-test"


# ---------------------------------------------------------------------------
# 6. Rate limiter fuzz
# ---------------------------------------------------------------------------

from sandcastle.api.rate_limit import InMemoryBackend, RateLimiter, _Window


class TestRateLimitWindow:
    def test_empty_window(self):
        w = _Window()
        assert w.count_in_window(60.0) == 0

    def test_add_and_count(self):
        w = _Window()
        w.add()
        w.add()
        assert w.count_in_window(60.0) == 2

    def test_expired_entries_cleaned(self):
        w = _Window()
        # Add timestamps that are old
        w.timestamps = [time.monotonic() - 120.0]
        assert w.count_in_window(60.0) == 0

    def test_zero_window(self):
        """Window of 0 seconds - everything is expired."""
        w = _Window()
        w.add()
        count = w.count_in_window(0.0)
        # monotonic() - 0.0 = now, timestamps ARE <= now, so filtered out
        assert count == 0

    def test_negative_window(self):
        """Negative window - cutoff is in the future, everything filtered."""
        w = _Window()
        w.add()
        count = w.count_in_window(-1.0)
        assert count == 0


class TestInMemoryBackend:
    @pytest.mark.asyncio
    async def test_basic_increment(self):
        backend = InMemoryBackend()
        count = await backend.check_and_increment("key1", max_requests=10, window_seconds=60)
        assert count == 1

    @pytest.mark.asyncio
    async def test_limit_reached(self):
        backend = InMemoryBackend()
        for i in range(10):
            await backend.check_and_increment("key1", max_requests=10, window_seconds=60)
        count = await backend.check_and_increment("key1", max_requests=10, window_seconds=60)
        # 11th request should return 11 (exceeds limit)
        assert count == 11

    @pytest.mark.asyncio
    async def test_max_requests_zero(self):
        """max_requests=0 means no requests allowed."""
        backend = InMemoryBackend()
        count = await backend.check_and_increment("key1", max_requests=0, window_seconds=60)
        # current (0) < max_requests (0) is False, so no add, returns 1
        assert count == 1

    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """100 concurrent requests should not corrupt state."""
        backend = InMemoryBackend()

        async def hit():
            return await backend.check_and_increment("concurrent", max_requests=1000, window_seconds=60)

        results = await asyncio.gather(*[hit() for _ in range(100)])
        # All results should be unique 1..100
        assert sorted(results) == list(range(1, 101))

    @pytest.mark.asyncio
    async def test_different_keys_independent(self):
        backend = InMemoryBackend()
        await backend.check_and_increment("keyA", max_requests=10, window_seconds=60)
        count_b = await backend.check_and_increment("keyB", max_requests=10, window_seconds=60)
        assert count_b == 1

    @pytest.mark.asyncio
    async def test_active_keys_count(self):
        backend = InMemoryBackend()
        await backend.check_and_increment("k1", max_requests=10, window_seconds=60)
        await backend.check_and_increment("k2", max_requests=10, window_seconds=60)
        assert backend.active_keys == 2


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_under_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60.0, backend=InMemoryBackend())
        request = MagicMock()
        request.state = MagicMock(spec=[])
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        # 5 requests should be fine
        for _ in range(5):
            await limiter.check(request)

    @pytest.mark.asyncio
    async def test_over_limit_raises_429(self):
        from fastapi import HTTPException
        limiter = RateLimiter(max_requests=2, window_seconds=60.0, backend=InMemoryBackend())
        request = MagicMock()
        request.state = MagicMock(spec=[])
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        await limiter.check(request)
        await limiter.check(request)
        with pytest.raises(HTTPException) as exc_info:
            await limiter.check(request)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_tenant_key(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60.0, backend=InMemoryBackend())
        request = MagicMock()
        request.state = MagicMock()
        request.state.tenant_id = "tenant-123"
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        key = limiter._get_key(request)
        assert "tenant:tenant-123" == key

    @pytest.mark.asyncio
    async def test_ip_key_no_client(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60.0, backend=InMemoryBackend())
        request = MagicMock()
        request.state = MagicMock(spec=[])
        request.client = None

        key = limiter._get_key(request)
        assert key == "ip:unknown"

    def test_info_dict(self):
        backend = InMemoryBackend()
        limiter = RateLimiter(max_requests=10, window_seconds=30.0, backend=backend)
        info = limiter.info
        assert info["max_requests"] == 10
        assert info["window_seconds"] == 30.0
        assert info["backend"] == "InMemoryBackend"
        assert "active_keys" in info


# ---------------------------------------------------------------------------
# 7. Executor context edge cases
# ---------------------------------------------------------------------------

class TestRunContext:
    def test_total_cost_empty(self):
        ctx = _ctx(costs=[])
        assert ctx.total_cost == 0.0

    def test_total_cost_float_precision(self):
        ctx = _ctx(costs=[0.1, 0.2, 0.3])
        assert ctx.total_cost == pytest.approx(0.6)

    def test_with_item_creates_copy(self):
        ctx = _ctx(step_outputs={"s1": "v1"})
        child = ctx.with_item("item0", 0)
        child.step_outputs["s2"] = "v2"
        assert "s2" not in ctx.step_outputs

    def test_with_item_injects_item_and_index(self):
        ctx = _ctx()
        child = ctx.with_item("myitem", 42)
        assert child.input["_item"] == "myitem"
        assert child.input["_index"] == 42

    def test_snapshot_serializable(self):
        import json
        ctx = _ctx(costs=[0.1, 0.2])
        snapshot = ctx.snapshot()
        # Should be JSON-serializable
        json_str = json.dumps(snapshot)
        parsed = json.loads(json_str)
        assert parsed["run_id"] == ctx.run_id


# ---------------------------------------------------------------------------
# 8. Additional DAG edge cases
# ---------------------------------------------------------------------------

class TestDagRetryValidation:
    def test_retry_max_attempts_zero(self):
        steps = [StepDefinition(
            id="s1", prompt="x",
            retry=MagicMock(max_attempts=0, backoff="exponential", on_failure="abort"),
        )]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("max_attempts" in e and ">= 1" in e for e in errors)

    def test_retry_max_attempts_over_100(self):
        steps = [StepDefinition(
            id="s1", prompt="x",
            retry=MagicMock(max_attempts=101, backoff="exponential", on_failure="abort"),
        )]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("max_attempts" in e and "<= 100" in e for e in errors)

    def test_retry_invalid_backoff(self):
        steps = [StepDefinition(
            id="s1", prompt="x",
            retry=MagicMock(max_attempts=3, backoff="linear", on_failure="abort"),
        )]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("backoff" in e for e in errors)

    def test_retry_invalid_on_failure(self):
        steps = [StepDefinition(
            id="s1", prompt="x",
            retry=MagicMock(max_attempts=3, backoff="exponential", on_failure="retry"),
        )]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("on_failure" in e for e in errors)


class TestDagTimeoutMaxTurns:
    def test_timeout_zero(self):
        steps = [StepDefinition(id="s1", prompt="x", timeout=0)]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("timeout" in e and "> 0" in e for e in errors)

    def test_timeout_over_86400(self):
        steps = [StepDefinition(id="s1", prompt="x", timeout=86401)]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("timeout" in e and "86400" in e for e in errors)

    def test_timeout_exactly_86400(self):
        steps = [StepDefinition(id="s1", prompt="x", timeout=86400)]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert not any("timeout" in e for e in errors)

    def test_max_turns_zero(self):
        steps = [StepDefinition(id="s1", prompt="x", max_turns=0)]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("max_turns" in e and ">= 1" in e for e in errors)

    def test_max_turns_over_1000(self):
        steps = [StepDefinition(id="s1", prompt="x", max_turns=1001)]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert any("max_turns" in e and "<= 1000" in e for e in errors)

    def test_max_turns_exactly_1000(self):
        steps = [StepDefinition(id="s1", prompt="x", max_turns=1000)]
        wf = _make_wf(steps)
        errors = validate(wf)
        assert not any("max_turns" in e for e in errors)


# -- Depends on non-string --

class TestDagDependsOn:
    def test_depends_on_non_string(self):
        wf = _parse_raw({
            "name": "test",
            "steps": [
                {"id": "s1", "prompt": "x"},
                {"id": "s2", "prompt": "x", "depends_on": [123]},
            ],
        })
        errors = validate(wf)
        assert any("non-string" in e for e in errors)

    def test_depends_on_unknown_step(self):
        wf = _parse_raw({
            "name": "test",
            "steps": [
                {"id": "s1", "prompt": "x", "depends_on": ["nonexistent"]},
            ],
        })
        errors = validate(wf)
        assert any("unknown step" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# 9. Backoff delay
# ---------------------------------------------------------------------------

from sandcastle.engine.executor import _backoff_delay


class TestBackoffDelay:
    def test_exponential_attempt_0(self):
        assert _backoff_delay(0, "exponential") == 1.0

    def test_exponential_attempt_1(self):
        assert _backoff_delay(1, "exponential") == 2.0

    def test_exponential_capped_at_60(self):
        assert _backoff_delay(100, "exponential") == 60.0

    def test_fixed_always_2(self):
        assert _backoff_delay(0, "fixed") == 2.0
        assert _backoff_delay(5, "fixed") == 2.0
        assert _backoff_delay(100, "fixed") == 2.0

    def test_unknown_backoff_type(self):
        """Unknown backoff type falls through to fixed."""
        assert _backoff_delay(0, "linear") == 2.0


# ---------------------------------------------------------------------------
# 10. JS string escape
# ---------------------------------------------------------------------------

from sandcastle.engine.executor import _escape_js_string


class TestEscapeJsString:
    def test_empty_string(self):
        assert _escape_js_string("") == ""

    def test_no_special_chars(self):
        assert _escape_js_string("hello world") == "hello world"

    def test_single_quotes(self):
        assert _escape_js_string("it's") == "it\\'s"

    def test_double_quotes(self):
        assert _escape_js_string('say "hi"') == 'say \\"hi\\"'

    def test_newlines(self):
        assert _escape_js_string("line1\nline2") == "line1\\nline2"

    def test_backslash(self):
        assert _escape_js_string("path\\to") == "path\\\\to"

    def test_tabs_and_returns(self):
        assert _escape_js_string("\t\r") == "\\t\\r"

    def test_combined(self):
        result = _escape_js_string("it's a \"test\"\nwith\\paths")
        assert "\\'" in result
        assert '\\"' in result
        assert "\\n" in result
        assert "\\\\" in result


# ---------------------------------------------------------------------------
# 11. Env var resolution in DAG
# ---------------------------------------------------------------------------

from sandcastle.engine.dag import _resolve_env_vars


class TestResolveEnvVars:
    def test_no_env_vars(self):
        assert _resolve_env_vars("plain text") == "plain text"

    def test_single_env_var(self):
        import os
        os.environ["TEST_FUZZ_VAR"] = "myvalue"
        try:
            assert _resolve_env_vars("prefix-${TEST_FUZZ_VAR}-suffix") == "prefix-myvalue-suffix"
        finally:
            del os.environ["TEST_FUZZ_VAR"]

    def test_missing_env_var(self):
        result = _resolve_env_vars("${NONEXISTENT_VAR_12345}")
        assert result == ""

    def test_empty_string(self):
        assert _resolve_env_vars("") == ""

    def test_multiple_env_vars(self):
        import os
        os.environ["TEST_A"] = "aaa"
        os.environ["TEST_B"] = "bbb"
        try:
            result = _resolve_env_vars("${TEST_A}/${TEST_B}")
            assert result == "aaa/bbb"
        finally:
            del os.environ["TEST_A"]
            del os.environ["TEST_B"]


# ---------------------------------------------------------------------------
# 12. Browser cache edge cases
# ---------------------------------------------------------------------------

from sandcastle.engine.executor import (
    _get_cached_actions,
    _save_cached_actions,
    _browser_action_cache,
    _BROWSER_CACHE_MAX,
)


class TestBrowserCache:
    @pytest.mark.asyncio
    async def test_cache_miss(self):
        result = await _get_cached_actions("http://unique-test-url.com", "unique intent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        url = "http://cache-test.com/page"
        intent = "click button test"
        actions = [{"action": "click", "selector": "#btn"}]
        await _save_cached_actions(url, intent, actions)
        result = await _get_cached_actions(url, intent)
        assert result == actions

    @pytest.mark.asyncio
    async def test_cache_eviction(self):
        """Filling cache beyond max should evict oldest entries."""
        # Save current cache state
        old_cache = dict(_browser_action_cache)
        _browser_action_cache.clear()
        try:
            for i in range(_BROWSER_CACHE_MAX + 10):
                await _save_cached_actions(f"http://evict-{i}.com", f"intent-{i}", [{"i": i}])
            assert len(_browser_action_cache) <= _BROWSER_CACHE_MAX
        finally:
            _browser_action_cache.clear()
            _browser_action_cache.update(old_cache)


# ---------------------------------------------------------------------------
# 13. WorkflowDefinition.get_step edge cases
# ---------------------------------------------------------------------------

class TestWorkflowGetStep:
    def test_get_existing_step(self):
        wf = _make_wf([StepDefinition(id="s1", prompt="x")])
        step = wf.get_step("s1")
        assert step.id == "s1"

    def test_get_nonexistent_step(self):
        wf = _make_wf([StepDefinition(id="s1", prompt="x")])
        with pytest.raises(ValueError, match="not found"):
            wf.get_step("nonexistent")


# ---------------------------------------------------------------------------
# 14. Condition step with unicode in expression
# ---------------------------------------------------------------------------

class TestConditionUnicode:
    @pytest.mark.asyncio
    async def test_unicode_in_expression(self):
        """Unicode characters in expressions should be handled."""
        from sandcastle.engine.executor import _execute_condition_step
        from sandcastle.engine.dag import ConditionConfig

        step = StepDefinition(
            id="cond1", prompt="x", type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=[],
                else_steps=[],
            ),
        )
        ctx = _ctx()
        result = await _execute_condition_step(step, ctx)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_missing_config(self):
        from sandcastle.engine.executor import _execute_condition_step
        step = StepDefinition(id="cond1", prompt="x", type="condition", condition_config=None)
        ctx = _ctx()
        result = await _execute_condition_step(step, ctx)
        assert result.status == "failed"
        assert "missing" in result.error.lower()


# ---------------------------------------------------------------------------
# 15. Loop step edge cases
# ---------------------------------------------------------------------------

class TestLoopEdgeCases:
    @pytest.mark.asyncio
    async def test_loop_zero_items(self):
        """Loop over empty list should complete with empty results."""
        from sandcastle.engine.executor import _execute_loop_step
        from sandcastle.engine.dag import LoopConfig

        step = StepDefinition(
            id="loop1", prompt="x", type="loop",
            loop_config=LoopConfig(over="input.items", step_ids=[], max_iterations=100),
        )
        ctx = _ctx(input={"items": []})
        result = await _execute_loop_step(step, ctx, None, MagicMock(), _make_wf([step]), 0)
        assert result.status == "completed"
        assert result.output == []

    @pytest.mark.asyncio
    async def test_loop_unresolved_variable(self):
        """Loop over unresolved variable should complete with empty results."""
        from sandcastle.engine.executor import _execute_loop_step
        from sandcastle.engine.dag import LoopConfig

        step = StepDefinition(
            id="loop1", prompt="x", type="loop",
            loop_config=LoopConfig(over="input.nonexistent", step_ids=[], max_iterations=100),
        )
        ctx = _ctx(input={})
        result = await _execute_loop_step(step, ctx, None, MagicMock(), _make_wf([step]), 0)
        assert result.status == "completed"
        assert result.output == []

    @pytest.mark.asyncio
    async def test_loop_missing_config(self):
        from sandcastle.engine.executor import _execute_loop_step
        step = StepDefinition(id="loop1", prompt="x", type="loop", loop_config=None)
        ctx = _ctx()
        result = await _execute_loop_step(step, ctx, None, MagicMock(), _make_wf([step]), 0)
        assert result.status == "failed"
        assert "missing" in result.error.lower()


# ---------------------------------------------------------------------------
# 16. Race step edge cases
# ---------------------------------------------------------------------------

class TestRaceEdgeCases:
    @pytest.mark.asyncio
    async def test_race_missing_config(self):
        from sandcastle.engine.executor import _execute_race_step
        step = StepDefinition(id="race1", prompt="x", type="race", race_config=None)
        ctx = _ctx()
        result = await _execute_race_step(step, ctx, None, MagicMock(), _make_wf([step]), 0)
        assert result.status == "failed"
        assert "missing" in result.error.lower()

    @pytest.mark.asyncio
    async def test_race_empty_branches(self):
        """Race with empty branches list fails - no branches to race."""
        from sandcastle.engine.executor import _execute_race_step

        step = StepDefinition(
            id="race1", prompt="x", type="race",
            race_config=RaceConfig(branches=[]),
        )
        ctx = _ctx()
        result = await _execute_race_step(step, ctx, None, MagicMock(), _make_wf([step]), 0)
        # Empty branches means no tasks, winning_output stays None -> "failed"
        assert result.status == "failed"
        assert "All race branches failed" in result.error


# ---------------------------------------------------------------------------
# 17. Config edge cases - computed fields
# ---------------------------------------------------------------------------

class TestConfigComputedFields:
    def test_is_local_mode_empty_url(self):
        s = Settings(database_url="")
        assert s.is_local_mode is True

    def test_is_local_mode_sqlite_prefix(self):
        s = Settings(database_url="sqlite+aiosqlite:///data.db")
        assert s.is_local_mode is True

    def test_is_local_mode_postgres(self):
        s = Settings(database_url="postgresql+asyncpg://user:pass@host/db")
        assert s.is_local_mode is False

    def test_workflows_dir_tilde(self):
        s = Settings(workflows_dir="~/workflows")
        assert "~" not in s.workflows_dir


# ---------------------------------------------------------------------------
# 18. Memory scope resolution
# ---------------------------------------------------------------------------

from sandcastle.engine.memory import resolve_scope_id


class TestMemoryScopeResolution:
    def test_default_workflow_scope(self):
        config = MagicMock()
        config.scope = "workflow"
        result = resolve_scope_id(config, "my_workflow")
        assert result == "workflow:my_workflow"

    def test_agent_scope_with_name(self):
        config = MagicMock()
        config.scope = "agent"
        config.agent = "agent007"
        result = resolve_scope_id(config, "my_workflow")
        assert result == "agent:agent007"

    def test_agent_scope_without_name(self):
        """Agent scope with empty name falls back to workflow."""
        config = MagicMock()
        config.scope = "agent"
        config.agent = ""
        result = resolve_scope_id(config, "my_workflow")
        assert result == "workflow:my_workflow"

    def test_global_scope(self):
        config = MagicMock()
        config.scope = "global"
        result = resolve_scope_id(config, "my_workflow")
        assert result == "global"

    def test_none_config(self):
        result = resolve_scope_id(None, "my_workflow")
        assert result == "workflow:my_workflow"
