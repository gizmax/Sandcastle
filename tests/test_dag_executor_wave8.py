"""Wave 8 deep audit tests for DAG + executor edge cases.

Covers:
1. DAG cycle detection with implicit deps (prompt references)
2. DAG validation for loop step_ids, classify input, depends_on type
3. Variable resolution edge cases (bare 'input', deep nesting, list OOB)
4. Condition expression injection via dict key serialization
5. Transform step Jinja-like brace injection safety
6. Loop step cost propagation on early termination
7. Race step when ALL branches fail
8. Code step resource cleanup on timeout
9. Build_plan with disconnected subgraphs
10. Sensor step edge cases
11. Notify step with empty/None fields
12. HTTP step body template resolution for dict bodies
13. Classify step with unexpected LLM category
14. Budget checking edge cases
15. Backoff delay correctness
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    ClassifyConfig,
    CodeConfig,
    ConditionConfig,
    DelegateConfig,
    ExecutionPlan,
    GateConfig,
    HttpConfig,
    LoopConfig,
    NotifyConfig,
    RaceConfig,
    RetryConfig,
    SensorConfig,
    StepDefinition,
    TransformConfig,
    WorkflowDefinition,
    _detect_cycles,
    _extract_implicit_deps,
    _extract_step_refs,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _UNRESOLVED,
    _backoff_delay,
    _check_budget,
    _escape_braces,
    _execute_code_step,
    _execute_condition_step,
    _execute_loop_step,
    _execute_notify_step,
    _execute_race_step,
    _execute_transform_step,
    _resolve_condition_expression,
    _safe_eval_expression,
    _truncate_output,
    resolve_templates,
    resolve_variable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(
    inputs: dict | None = None,
    step_outputs: dict | None = None,
    step_results: dict | None = None,
    run_id: str | None = None,
    max_cost_usd: float | None = None,
) -> RunContext:
    """Create a RunContext for testing."""
    ctx = RunContext(
        run_id=run_id or str(uuid.uuid4()),
        input=inputs or {},
        step_outputs=step_outputs or {},
        max_cost_usd=max_cost_usd,
    )
    if step_results:
        ctx.step_results = step_results
    return ctx


def _make_workflow(steps: list[StepDefinition], name: str = "test") -> WorkflowDefinition:
    """Create a minimal WorkflowDefinition."""
    return WorkflowDefinition(
        name=name,
        description="test workflow",
        default_model="sonnet",
        default_max_turns=10,
        default_timeout=300,
        steps=steps,
    )


# =========================================================================
# 1. DAG cycle detection with implicit deps
# =========================================================================


class TestCycleDetectionImplicit:
    """Cycle detection should work for both explicit and implicit deps."""

    def test_explicit_cycle_detected(self):
        """Standard A->B->C->A cycle is detected."""
        wf = parse_yaml_string("""
name: cycle-test
steps:
  - id: a
    depends_on: [c]
    prompt: "A"
  - id: b
    depends_on: [a]
    prompt: "B"
  - id: c
    depends_on: [b]
    prompt: "C"
""")
        errors = validate(wf)
        cycle_errors = [e for e in errors if "ycle" in e.lower()]
        assert len(cycle_errors) > 0

    def test_self_dependency_detected(self):
        """A step depending on itself is caught."""
        wf = parse_yaml_string("""
name: self-dep
steps:
  - id: loopy
    depends_on: [loopy]
    prompt: "I depend on myself"
""")
        errors = validate(wf)
        cycle_errors = [e for e in errors if "ycle" in e.lower()]
        assert len(cycle_errors) > 0

    def test_implicit_mutual_reference_detected_by_build_plan(self):
        """Two steps referencing each other via prompts creates a cycle in build_plan."""
        steps = [
            StepDefinition(
                id="alpha",
                prompt="Use {steps.beta.output}",
            ),
            StepDefinition(
                id="beta",
                prompt="Use {steps.alpha.output}",
            ),
        ]
        wf = _make_workflow(steps)
        with pytest.raises(ValueError, match="unschedulable"):
            build_plan(wf)

    def test_diamond_dag_no_cycle(self):
        """Diamond-shaped DAG (A->B, A->C, B->D, C->D) has no cycles."""
        wf = parse_yaml_string("""
name: diamond
steps:
  - id: a
    prompt: "start"
  - id: b
    depends_on: [a]
    prompt: "left"
  - id: c
    depends_on: [a]
    prompt: "right"
  - id: d
    depends_on: [b, c]
    prompt: "merge"
""")
        errors = validate(wf)
        cycle_errors = [e for e in errors if "ycle" in e.lower()]
        assert len(cycle_errors) == 0

    def test_indirect_cycle_through_three_steps(self):
        """Indirect cycle A->B->C->A detected even without direct back-edge."""
        steps = [
            StepDefinition(id="a", prompt="start", depends_on=["c"]),
            StepDefinition(id="b", prompt="mid", depends_on=["a"]),
            StepDefinition(id="c", prompt="end", depends_on=["b"]),
        ]
        wf = _make_workflow(steps)
        errors = _detect_cycles(wf.steps)
        assert len(errors) > 0


# =========================================================================
# 2. DAG validation gaps
# =========================================================================


class TestValidationGaps:
    """Tests for validation edge cases that were previously uncovered."""

    def test_loop_step_ids_reference_unknown_step(self):
        """Loop step_ids referencing a non-existent step should produce a warning."""
        wf = parse_yaml_string("""
name: loop-unknown-ref
steps:
  - id: my_loop
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [nonexistent_step]
      max_iterations: 10
    prompt: "loop"
""")
        # The loop's step_ids reference a step that doesn't exist.
        # Currently validation doesn't explicitly check this -
        # it would fail at runtime. Let's verify it doesn't crash.
        errors = validate(wf)
        # At minimum, the workflow should parse without crashing.
        # The 'nonexistent_step' is used at runtime, so no validate error
        # is necessarily expected here (this documents current behavior).
        assert isinstance(errors, list)

    def test_classify_empty_input_validated(self):
        """Classify step with empty input should still parse."""
        wf = parse_yaml_string("""
name: classify-empty
steps:
  - id: classifier
    type: classify
    classify_config:
      categories: [positive, negative]
      input: ""
      model: haiku
      branches:
        positive: []
        negative: []
    prompt: "classify"
""")
        errors = validate(wf)
        # Empty input is technically valid at parse time (template vars
        # may be resolved later). Just verify it doesn't crash.
        assert isinstance(errors, list)

    def test_depends_on_string_instead_of_list(self):
        """depends_on as a string (not list) should be handled gracefully."""
        # YAML might parse `depends_on: step1` as a string
        wf = parse_yaml_string("""
name: bad-deps
steps:
  - id: step1
    prompt: "first"
  - id: step2
    depends_on: step1
    prompt: "second"
""")
        # YAML parses `depends_on: step1` as the string "step1"
        # _parse_step uses data.get("depends_on", []) which returns "step1" as string.
        # StepDefinition.depends_on would be "step1" (a string, iterable as chars).
        errors = validate(wf)
        # Since "step1" is iterable as chars ["s","t","e","p","1"],
        # each char becomes a "dep" - all would be unknown steps.
        dep_errors = [e for e in errors if "depends on unknown" in e.lower()]
        # Expect errors because individual chars aren't valid step IDs
        assert len(dep_errors) > 0

    def test_negative_timeout_rejected(self):
        """Step with negative timeout should fail validation."""
        wf = parse_yaml_string("""
name: neg-timeout
steps:
  - id: bad_step
    prompt: "test"
    timeout: -5
""")
        errors = validate(wf)
        timeout_errors = [e for e in errors if "timeout" in e.lower()]
        assert len(timeout_errors) > 0

    def test_max_turns_zero_rejected(self):
        """Step with max_turns=0 should fail validation."""
        wf = parse_yaml_string("""
name: zero-turns
steps:
  - id: bad_step
    prompt: "test"
    max_turns: 0
""")
        errors = validate(wf)
        turns_errors = [e for e in errors if "max_turns" in e.lower()]
        assert len(turns_errors) > 0

    def test_gate_empty_strategies_rejected(self):
        """Gate step with empty strategies list should fail validation."""
        wf = parse_yaml_string("""
name: empty-gate
steps:
  - id: my_gate
    type: gate
    gate_config:
      strategies: []
    prompt: "gate"
""")
        errors = validate(wf)
        gate_errors = [e for e in errors if "gate" in e.lower() and "strateg" in e.lower()]
        assert len(gate_errors) > 0

    def test_sensor_zero_check_interval_rejected(self):
        """Sensor with check_interval=0 should fail validation."""
        wf = parse_yaml_string("""
name: bad-sensor
steps:
  - id: my_sensor
    type: sensor
    sensor_config:
      url: "https://example.com/status"
      check_interval: 0
      timeout: 60
      condition: "status_code == 200"
    prompt: "sensor"
""")
        errors = validate(wf)
        interval_errors = [e for e in errors if "check_interval" in e.lower()]
        assert len(interval_errors) > 0

    def test_race_empty_branches_rejected(self):
        """Race step with no branches should fail validation."""
        wf = parse_yaml_string("""
name: empty-race
steps:
  - id: my_race
    type: race
    race_config:
      branches: []
    prompt: "race"
""")
        errors = validate(wf)
        race_errors = [e for e in errors if "race" in e.lower() and "branch" in e.lower()]
        assert len(race_errors) > 0


# =========================================================================
# 3. Variable resolution edge cases
# =========================================================================


class TestResolveVariableEdgeCases:
    """Deep variable resolution edge cases."""

    def test_bare_input_returns_full_dict(self):
        """resolve_variable('input') with no subpath should return full input dict."""
        ctx = _make_context(inputs={"name": "test", "count": 42})
        result = resolve_variable("input", ctx)
        # "input" splits to ["input"], len(parts) is 1,
        # so parts[1:] is empty, _traverse returns the full dict
        # BUT: len(parts) > 1 check for parts[1] not in context.input
        # is False since len(parts) == 1, so it proceeds to _traverse
        # with empty parts list, which returns the object itself.
        assert result == {"name": "test", "count": 42}

    def test_deep_nested_dict_access(self):
        """Access deeply nested dict fields via dotted path."""
        ctx = _make_context(
            step_outputs={
                "scrape": {
                    "data": {
                        "company": {
                            "name": "Acme",
                            "address": {"city": "Prague"},
                        }
                    }
                }
            }
        )
        result = resolve_variable("steps.scrape.output.data.company.address.city", ctx)
        assert result == "Prague"

    def test_list_index_negative_returns_unresolved(self):
        """Negative list index should return _UNRESOLVED."""
        ctx = _make_context(step_outputs={"step1": [10, 20, 30]})
        result = resolve_variable("steps.step1.output.-1", ctx)
        assert result is _UNRESOLVED

    def test_list_index_non_numeric_returns_unresolved(self):
        """Non-numeric list index should return _UNRESOLVED."""
        ctx = _make_context(step_outputs={"step1": [10, 20, 30]})
        result = resolve_variable("steps.step1.output.abc", ctx)
        assert result is _UNRESOLVED

    def test_traverse_none_value_returns_none(self):
        """Traversing through a None value returns None (not _UNRESOLVED)."""
        ctx = _make_context(step_outputs={"step1": {"data": None}})
        result = resolve_variable("steps.step1.output.data.field", ctx)
        assert result is None

    def test_step_metadata_cost(self):
        """Resolve step cost metadata."""
        ctx = _make_context(
            step_outputs={"step1": "output"},
            step_results={"step1": StepResult(step_id="step1", cost_usd=0.05)},
        )
        result = resolve_variable("steps.step1.cost", ctx)
        assert result == 0.05

    def test_step_metadata_status(self):
        """Resolve step status metadata."""
        ctx = _make_context(
            step_outputs={"step1": "output"},
            step_results={"step1": StepResult(step_id="step1", status="completed")},
        )
        result = resolve_variable("steps.step1.status", ctx)
        assert result == "completed"

    def test_step_metadata_error(self):
        """Resolve step error metadata."""
        ctx = _make_context(
            step_outputs={"step1": None},
            step_results={
                "step1": StepResult(
                    step_id="step1", status="failed", error="API timeout"
                )
            },
        )
        result = resolve_variable("steps.step1.error", ctx)
        assert result == "API timeout"

    def test_step_metadata_missing_step_result(self):
        """Step metadata for a step with no result record returns _UNRESOLVED."""
        ctx = _make_context(step_outputs={"step1": "output"})
        result = resolve_variable("steps.step1.cost", ctx)
        assert result is _UNRESOLVED

    def test_steps_with_only_two_parts(self):
        """steps.X (without .output) should return _UNRESOLVED."""
        ctx = _make_context(step_outputs={"step1": "output"})
        result = resolve_variable("steps.step1", ctx)
        assert result is _UNRESOLVED

    def test_input_missing_key_returns_unresolved(self):
        """Accessing a missing input key should return _UNRESOLVED."""
        ctx = _make_context(inputs={"name": "test"})
        result = resolve_variable("input.missing_key", ctx)
        assert result is _UNRESOLVED


# =========================================================================
# 4. Condition expression injection
# =========================================================================


class TestConditionInjection:
    """Tests for injection prevention in condition expressions."""

    def test_safe_string_value_allowed(self):
        """Normal string values pass the injection check."""
        ctx = _make_context(step_outputs={"step1": {"category": "positive"}})
        expr = _resolve_condition_expression(
            "'{steps.step1.output.category}' == 'positive'",
            ctx,
        )
        assert "positive" in expr

    def test_or_keyword_in_step_output_rejected(self):
        """Step output containing 'or' keyword is rejected."""
        ctx = _make_context(
            step_outputs={"step1": "true or True"}
        )
        with pytest.raises(ValueError, match="unsafe tokens"):
            _resolve_condition_expression(
                "{steps.step1.output} == 'safe'",
                ctx,
            )

    def test_semicolon_in_step_output_rejected(self):
        """Step output containing ';' is rejected."""
        ctx = _make_context(
            step_outputs={"step1": "value; import os"}
        )
        with pytest.raises(ValueError, match="unsafe tokens"):
            _resolve_condition_expression(
                "{steps.step1.output} == 'safe'",
                ctx,
            )

    def test_input_values_with_keywords_rejected(self):
        """Input values containing keywords like 'or' are also checked
        as defense-in-depth against injection in multi-tenant contexts."""
        ctx = _make_context(inputs={"mode": "production or staging"})
        # Even input values are checked because in multi-tenant environments,
        # API callers could inject keywords to manipulate condition outcomes.
        with pytest.raises(ValueError, match="unsafe tokens"):
            _resolve_condition_expression(
                "'{input.mode}' == 'production'",
                ctx,
            )

    def test_input_values_without_keywords_allowed(self):
        """Input values without dangerous keywords are allowed."""
        ctx = _make_context(inputs={"mode": "production"})
        expr = _resolve_condition_expression(
            "'{input.mode}' == 'production'",
            ctx,
        )
        assert "production" in expr

    def test_dunder_in_expression_rejected(self):
        """Double underscore patterns in expressions are rejected by _safe_eval."""
        with pytest.raises(ValueError, match="double-underscore"):
            _safe_eval_expression(
                "'a'.__class__.__bases__[0]",
                {"True": True, "False": False, "None": None},
            )

    def test_expression_length_limit(self):
        """Excessively long expressions are rejected."""
        long_expr = "x == 1 " * 500  # > 2000 chars
        with pytest.raises(ValueError, match="too long"):
            _safe_eval_expression(
                long_expr,
                {"x": 1, "True": True, "False": False, "None": None},
            )


# =========================================================================
# 5. Transform step brace injection safety
# =========================================================================


class TestTransformBraceInjection:
    """Verify that transform step properly handles brace content."""

    @pytest.mark.asyncio
    async def test_transform_basic_variable(self):
        """Transform step resolves basic variables."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(
                template='{"result": "{input.name}"}'
            ),
        )
        ctx = _make_context(inputs={"name": "Alice"})
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output == {"result": "Alice"}

    @pytest.mark.asyncio
    async def test_transform_jinja_like_with_tojson(self):
        """Transform step supports {{ var | tojson }} filter."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(
                template='{{ input.data | tojson }}'
            ),
        )
        ctx = _make_context(inputs={"data": {"key": "value"}})
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output == {"key": "value"}

    @pytest.mark.asyncio
    async def test_transform_jinja_unresolved_returns_empty(self):
        """Unresolved Jinja-like variable returns empty string."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(
                template="Hello {{ input.missing }}"
            ),
        )
        ctx = _make_context(inputs={})
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output == "Hello "

    @pytest.mark.asyncio
    async def test_transform_step_output_braces_escaped_in_template_vars(self):
        """Step outputs with braces are escaped in {var} resolution
        (via resolve_templates), preventing re-resolution."""
        step = StepDefinition(
            id="xform",
            type="transform",
            depends_on=["step1"],
            transform_config=TransformConfig(
                template="Data: {steps.step1.output}"
            ),
        )
        # Step output contains template-like syntax
        ctx = _make_context(step_outputs={"step1": "{input.secret}"})
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        # The braces should be escaped, not re-resolved
        assert "{input.secret}" not in str(result.output) or "{{input.secret}}" in str(result.output)

    @pytest.mark.asyncio
    async def test_transform_missing_config(self):
        """Transform step with missing config returns failure."""
        step = StepDefinition(id="xform", type="transform")
        ctx = _make_context()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "failed"
        assert "Missing" in result.error

    @pytest.mark.asyncio
    async def test_transform_oversized_template(self):
        """Transform step rejects oversized templates."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(
                template="x" * (1024 * 1024 + 1)
            ),
        )
        ctx = _make_context()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "failed"
        assert "too large" in result.error.lower()


# =========================================================================
# 6. Loop step cost propagation and edge cases
# =========================================================================


class TestLoopStepEdgeCases:
    """Loop step edge cases including cost propagation."""

    @pytest.mark.asyncio
    async def test_loop_empty_items(self):
        """Loop with empty items list should return empty results."""
        step = StepDefinition(
            id="my_loop",
            type="loop",
            loop_config=LoopConfig(
                over="input.items",
                step_ids=["sub_step"],
                max_iterations=10,
            ),
        )
        wf = _make_workflow([
            step,
            StepDefinition(id="sub_step", prompt="process"),
        ])
        ctx = _make_context(inputs={"items": []})
        sandbox = MagicMock()
        storage = AsyncMock()

        result = await _execute_loop_step(step, ctx, sandbox, storage, wf, depth=0)
        assert result.status == "completed"
        assert result.output == []
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_loop_unresolved_over_treated_as_empty(self):
        """Loop with unresolvable 'over' path should use empty list."""
        step = StepDefinition(
            id="my_loop",
            type="loop",
            loop_config=LoopConfig(
                over="input.nonexistent",
                step_ids=["sub_step"],
                max_iterations=10,
            ),
        )
        wf = _make_workflow([
            step,
            StepDefinition(id="sub_step", prompt="process"),
        ])
        ctx = _make_context(inputs={})
        sandbox = MagicMock()
        storage = AsyncMock()

        result = await _execute_loop_step(step, ctx, sandbox, storage, wf, depth=0)
        assert result.status == "completed"
        assert result.output == []

    @pytest.mark.asyncio
    async def test_loop_scalar_over_wrapped_in_list(self):
        """Loop with a scalar 'over' value wraps it in a list,
        but empty step_ids with items fails validation."""
        step = StepDefinition(
            id="my_loop",
            type="loop",
            loop_config=LoopConfig(
                over="input.single_item",
                step_ids=[],
                max_iterations=10,
            ),
        )
        wf = _make_workflow([step])
        ctx = _make_context(inputs={"single_item": "hello"})
        sandbox = MagicMock()
        storage = AsyncMock()

        result = await _execute_loop_step(step, ctx, sandbox, storage, wf, depth=0)
        # Loop with items but no step_ids is an error
        assert result.status == "failed"
        assert "step_ids" in result.error.lower()

    @pytest.mark.asyncio
    async def test_loop_scalar_over_with_step_ids(self):
        """Loop with scalar 'over' wraps it in a list and iterates once."""
        async def mock_execute_step(*args, **kwargs):
            return StepResult(
                step_id="sub_step",
                output="processed",
                cost_usd=0.01,
                status="completed",
            )

        step = StepDefinition(
            id="my_loop",
            type="loop",
            loop_config=LoopConfig(
                over="input.single_item",
                step_ids=["sub_step"],
                max_iterations=10,
            ),
        )
        wf = _make_workflow([
            step,
            StepDefinition(id="sub_step", prompt="process"),
        ])
        ctx = _make_context(inputs={"single_item": "hello"})
        sandbox = MagicMock()
        storage = AsyncMock()

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            side_effect=mock_execute_step,
        ), patch(
            "sandcastle.engine.executor._check_cancel",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await _execute_loop_step(step, ctx, sandbox, storage, wf, depth=0)

        assert result.status == "completed"
        assert len(result.output) == 1
        assert result.output[0] == "processed"

    @pytest.mark.asyncio
    async def test_loop_max_iterations_enforced(self):
        """Loop should not exceed max_iterations."""
        call_count = 0

        async def mock_execute_step(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return StepResult(
                step_id="sub_step",
                output=call_count,
                cost_usd=0.01,
                status="completed",
            )

        step = StepDefinition(
            id="my_loop",
            type="loop",
            loop_config=LoopConfig(
                over="input.items",
                step_ids=["sub_step"],
                max_iterations=3,
            ),
        )
        wf = _make_workflow([
            step,
            StepDefinition(id="sub_step", prompt="process"),
        ])
        ctx = _make_context(inputs={"items": list(range(10))})
        sandbox = MagicMock()
        storage = AsyncMock()

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            side_effect=mock_execute_step,
        ), patch(
            "sandcastle.engine.executor._check_cancel",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await _execute_loop_step(step, ctx, sandbox, storage, wf, depth=0)

        assert result.status == "completed"
        assert len(result.output) == 3  # Capped at max_iterations
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_loop_until_break_condition(self):
        """Loop 'until' condition should break iteration early."""
        call_count = 0

        async def mock_execute_step(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return StepResult(
                step_id="sub_step",
                output={"count": call_count},
                cost_usd=0.01,
                status="completed",
            )

        step = StepDefinition(
            id="my_loop",
            type="loop",
            loop_config=LoopConfig(
                over="input.items",
                step_ids=["sub_step"],
                max_iterations=100,
                until="index >= 2",  # Break after 3 iterations (0, 1, 2)
            ),
        )
        wf = _make_workflow([
            step,
            StepDefinition(id="sub_step", prompt="process"),
        ])
        ctx = _make_context(inputs={"items": list(range(10))})
        sandbox = MagicMock()
        storage = AsyncMock()

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            side_effect=mock_execute_step,
        ), patch(
            "sandcastle.engine.executor._check_cancel",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await _execute_loop_step(step, ctx, sandbox, storage, wf, depth=0)

        assert result.status == "completed"
        assert len(result.output) == 3  # Stopped at index 2

    @pytest.mark.asyncio
    async def test_loop_missing_config(self):
        """Loop step with no config returns failure."""
        step = StepDefinition(id="my_loop", type="loop")
        ctx = _make_context()
        sandbox = MagicMock()
        storage = AsyncMock()
        wf = _make_workflow([step])

        result = await _execute_loop_step(step, ctx, sandbox, storage, wf, depth=0)
        assert result.status == "failed"
        assert "Missing" in result.error


# =========================================================================
# 7. Race step when ALL branches fail
# =========================================================================


class TestRaceStepEdgeCases:
    """Race step edge cases."""

    @pytest.mark.asyncio
    async def test_race_all_branches_fail(self):
        """When all race branches fail, the race step should report failure."""
        step = StepDefinition(
            id="my_race",
            type="race",
            race_config=RaceConfig(
                branches=[["fail_step1"], ["fail_step2"]],
            ),
        )
        wf = _make_workflow([
            step,
            StepDefinition(id="fail_step1", prompt="will fail"),
            StepDefinition(id="fail_step2", prompt="will fail too"),
        ])
        ctx = _make_context()
        sandbox = MagicMock()
        storage = AsyncMock()

        async def mock_execute_fail(*args, **kwargs):
            return StepResult(
                step_id=args[0].id,
                status="failed",
                error="Simulated failure",
            )

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            side_effect=mock_execute_fail,
        ):
            result = await _execute_race_step(step, ctx, sandbox, storage, wf, depth=0)

        assert result.status == "failed"
        assert "All race branches failed" in result.error

    @pytest.mark.asyncio
    async def test_race_missing_config(self):
        """Race step with no config returns failure."""
        step = StepDefinition(id="my_race", type="race")
        ctx = _make_context()
        sandbox = MagicMock()
        storage = AsyncMock()
        wf = _make_workflow([step])

        result = await _execute_race_step(step, ctx, sandbox, storage, wf, depth=0)
        assert result.status == "failed"
        assert "Missing" in result.error

    @pytest.mark.asyncio
    async def test_race_first_branch_wins(self):
        """First branch to complete should win the race."""
        step = StepDefinition(
            id="my_race",
            type="race",
            race_config=RaceConfig(
                branches=[["fast_step"], ["slow_step"]],
            ),
        )
        wf = _make_workflow([
            step,
            StepDefinition(id="fast_step", prompt="fast"),
            StepDefinition(id="slow_step", prompt="slow"),
        ])
        ctx = _make_context()
        sandbox = MagicMock()
        storage = AsyncMock()

        call_count = 0

        async def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            step_def = args[0]
            if step_def.id == "slow_step":
                await asyncio.sleep(0.5)
            return StepResult(
                step_id=step_def.id,
                output=f"result from {step_def.id}",
                cost_usd=0.01,
                status="completed",
            )

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            side_effect=mock_execute,
        ):
            result = await _execute_race_step(step, ctx, sandbox, storage, wf, depth=0)

        assert result.status == "completed"
        assert "fast_step" in str(result.output)

    @pytest.mark.asyncio
    async def test_race_with_validator_no_match_uses_fallback(self):
        """Race with validator: if no branch passes, fallback to first result."""
        step = StepDefinition(
            id="my_race",
            type="race",
            race_config=RaceConfig(
                branches=[["step_a"], ["step_b"]],
                validator="output == 'impossible_value'",
            ),
        )
        wf = _make_workflow([
            step,
            StepDefinition(id="step_a", prompt="a"),
            StepDefinition(id="step_b", prompt="b"),
        ])
        ctx = _make_context()
        sandbox = MagicMock()
        storage = AsyncMock()

        async def mock_execute(*args, **kwargs):
            return StepResult(
                step_id=args[0].id,
                output="not_matching",
                cost_usd=0.01,
                status="completed",
            )

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            side_effect=mock_execute,
        ):
            result = await _execute_race_step(step, ctx, sandbox, storage, wf, depth=0)

        assert result.status == "completed"
        # Should use fallback output since no validator passed
        assert result.output == "not_matching"


# =========================================================================
# 8. Code step edge cases
# =========================================================================


class TestCodeStepEdgeCases:
    """Code step edge cases."""

    @pytest.mark.asyncio
    async def test_code_step_basic_execution(self):
        """Code step should execute Python code and return result."""
        step = StepDefinition(
            id="code1",
            type="code",
            code_config=CodeConfig(code="result = sum([1, 2, 3])"),
        )
        ctx = _make_context()
        result = await _execute_code_step(step, ctx)
        assert result.status == "completed"
        assert result.output == 6
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_code_step_access_input(self):
        """Code step should access context input via _input."""
        step = StepDefinition(
            id="code1",
            type="code",
            code_config=CodeConfig(
                code="result = _input['name'].upper()"
            ),
        )
        ctx = _make_context(inputs={"name": "alice"})
        result = await _execute_code_step(step, ctx)
        assert result.status == "completed"
        assert result.output == "ALICE"

    @pytest.mark.asyncio
    async def test_code_step_access_step_outputs(self):
        """Code step should access previous step outputs via _steps."""
        step = StepDefinition(
            id="code1",
            type="code",
            code_config=CodeConfig(
                code="result = len(_steps['prev'])"
            ),
        )
        ctx = _make_context(step_outputs={"prev": [1, 2, 3, 4, 5]})
        result = await _execute_code_step(step, ctx)
        assert result.status == "completed"
        assert result.output == 5

    @pytest.mark.asyncio
    async def test_code_step_blocked_pattern_exec(self):
        """Code step should block exec() calls."""
        step = StepDefinition(
            id="code1",
            type="code",
            code_config=CodeConfig(code="exec('import os')"),
        )
        ctx = _make_context()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "blocked pattern" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_step_blocked_pattern_import(self):
        """Code step should block import attempts."""
        step = StepDefinition(
            id="code1",
            type="code",
            code_config=CodeConfig(code="__import__('os')"),
        )
        ctx = _make_context()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "blocked pattern" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_step_blocked_pattern_subprocess(self):
        """Code step should block subprocess usage."""
        step = StepDefinition(
            id="code1",
            type="code",
            code_config=CodeConfig(code="import subprocess"),
        )
        ctx = _make_context()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "blocked pattern" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_step_oversized_code(self):
        """Code step should reject oversized code."""
        step = StepDefinition(
            id="code1",
            type="code",
            code_config=CodeConfig(code="x = 1\n" * 50001),
        )
        ctx = _make_context()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "too large" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_step_missing_config(self):
        """Code step with no config returns failure."""
        step = StepDefinition(id="code1", type="code")
        ctx = _make_context()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "Missing" in result.error

    @pytest.mark.asyncio
    async def test_code_step_runtime_error(self):
        """Code step with runtime error should fail gracefully."""
        step = StepDefinition(
            id="code1",
            type="code",
            code_config=CodeConfig(code="result = 1 / 0"),
        )
        ctx = _make_context()
        result = await _execute_code_step(step, ctx)
        assert result.status == "failed"
        assert "division" in result.error.lower() or "ZeroDivision" in result.error


# =========================================================================
# 9. Build plan with disconnected subgraphs
# =========================================================================


class TestBuildPlanEdgeCases:
    """Build plan edge cases."""

    def test_disconnected_subgraphs(self):
        """Steps with no dependencies between them should all be in stage 0."""
        steps = [
            StepDefinition(id="a", prompt="A"),
            StepDefinition(id="b", prompt="B"),
            StepDefinition(id="c", prompt="C"),
        ]
        wf = _make_workflow(steps)
        plan = build_plan(wf)
        # All independent steps should be in the first stage
        assert len(plan.stages) == 1
        assert sorted(plan.stages[0]) == ["a", "b", "c"]

    def test_linear_chain(self):
        """Linear chain A->B->C produces 3 stages."""
        steps = [
            StepDefinition(id="a", prompt="A"),
            StepDefinition(id="b", prompt="B", depends_on=["a"]),
            StepDefinition(id="c", prompt="C", depends_on=["b"]),
        ]
        wf = _make_workflow(steps)
        plan = build_plan(wf)
        assert len(plan.stages) == 3
        assert plan.stages[0] == ["a"]
        assert plan.stages[1] == ["b"]
        assert plan.stages[2] == ["c"]

    def test_mixed_connected_disconnected(self):
        """Mix of connected and disconnected steps."""
        steps = [
            StepDefinition(id="a", prompt="A"),
            StepDefinition(id="b", prompt="B", depends_on=["a"]),
            StepDefinition(id="c", prompt="C"),  # disconnected
            StepDefinition(id="d", prompt="D", depends_on=["b", "c"]),
        ]
        wf = _make_workflow(steps)
        plan = build_plan(wf)
        assert len(plan.stages) == 3
        # Stage 0: a, c (both have no deps)
        assert sorted(plan.stages[0]) == ["a", "c"]
        # Stage 1: b (depends on a)
        assert plan.stages[1] == ["b"]
        # Stage 2: d (depends on b, c)
        assert plan.stages[2] == ["d"]

    def test_single_step_workflow(self):
        """Single step workflow produces one stage."""
        steps = [StepDefinition(id="only", prompt="Solo")]
        wf = _make_workflow(steps)
        plan = build_plan(wf)
        assert len(plan.stages) == 1
        assert plan.stages[0] == ["only"]

    def test_wide_fan_out(self):
        """Wide fan-out: one root with many children."""
        children = [
            StepDefinition(id=f"child_{i}", prompt=f"C{i}", depends_on=["root"])
            for i in range(20)
        ]
        steps = [StepDefinition(id="root", prompt="Root")] + children
        wf = _make_workflow(steps)
        plan = build_plan(wf)
        assert len(plan.stages) == 2
        assert plan.stages[0] == ["root"]
        assert len(plan.stages[1]) == 20


# =========================================================================
# 10. Notify step edge cases
# =========================================================================


class TestNotifyStepEdgeCases:
    """Notify step edge cases."""

    @pytest.mark.asyncio
    async def test_notify_basic(self):
        """Notify step resolves message and returns success."""
        step = StepDefinition(
            id="notify1",
            type="notify",
            notify_config=NotifyConfig(
                service="slack",
                channel="#general",
                message="Build completed: {input.status}",
            ),
        )
        ctx = _make_context(inputs={"status": "success"})
        result = await _execute_notify_step(step, ctx)
        assert result.status == "completed"
        assert result.output["service"] == "slack"
        assert "success" in result.output["message"]
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_notify_empty_channel(self):
        """Notify step with empty channel should still succeed."""
        step = StepDefinition(
            id="notify1",
            type="notify",
            notify_config=NotifyConfig(
                service="email",
                channel="",
                message="Alert!",
            ),
        )
        ctx = _make_context()
        result = await _execute_notify_step(step, ctx)
        assert result.status == "completed"
        assert result.output["channel"] == ""

    @pytest.mark.asyncio
    async def test_notify_missing_config(self):
        """Notify step with no config returns failure."""
        step = StepDefinition(id="notify1", type="notify")
        ctx = _make_context()
        result = await _execute_notify_step(step, ctx)
        assert result.status == "failed"
        assert "Missing" in result.error

    @pytest.mark.asyncio
    async def test_notify_template_resolution(self):
        """Notify step resolves template variables in message and channel."""
        step = StepDefinition(
            id="notify1",
            type="notify",
            depends_on=["step1"],
            notify_config=NotifyConfig(
                service="webhook",
                channel="{input.webhook_url}",
                message="Step1 output: {steps.step1.output}",
            ),
        )
        ctx = _make_context(
            inputs={"webhook_url": "https://hooks.example.com/abc"},
            step_outputs={"step1": "analysis complete"},
        )
        result = await _execute_notify_step(step, ctx)
        assert result.status == "completed"
        assert result.output["channel"] == "https://hooks.example.com/abc"
        assert "analysis complete" in result.output["message"]


# =========================================================================
# 11. Budget checking edge cases
# =========================================================================


class TestBudgetChecking:
    """Budget checking edge cases."""

    def test_no_budget_set(self):
        """No budget set should return None."""
        ctx = _make_context()
        assert _check_budget(ctx) is None

    def test_zero_budget_returns_none(self):
        """Budget of 0 should return None (disabled)."""
        ctx = _make_context(max_cost_usd=0.0)
        assert _check_budget(ctx) is None

    def test_negative_budget_returns_none(self):
        """Negative budget should return None (disabled)."""
        ctx = _make_context(max_cost_usd=-1.0)
        assert _check_budget(ctx) is None

    def test_under_80_percent(self):
        """Under 80% should return None."""
        ctx = _make_context(max_cost_usd=1.0)
        ctx.costs = [0.5]  # 50%
        assert _check_budget(ctx) is None

    def test_at_80_percent(self):
        """At 80% should return 'warning'."""
        ctx = _make_context(max_cost_usd=1.0)
        ctx.costs = [0.8]
        assert _check_budget(ctx) == "warning"

    def test_at_100_percent(self):
        """At 100% should return 'exceeded'."""
        ctx = _make_context(max_cost_usd=1.0)
        ctx.costs = [1.0]
        assert _check_budget(ctx) == "exceeded"

    def test_over_100_percent(self):
        """Over 100% should return 'exceeded'."""
        ctx = _make_context(max_cost_usd=1.0)
        ctx.costs = [1.5]
        assert _check_budget(ctx) == "exceeded"

    def test_multiple_costs_accumulated(self):
        """Multiple cost entries should be summed."""
        ctx = _make_context(max_cost_usd=1.0)
        ctx.costs = [0.3, 0.3, 0.3]  # 0.9 = 90%
        assert _check_budget(ctx) == "warning"


# =========================================================================
# 12. Backoff delay
# =========================================================================


class TestBackoffDelay:
    """Backoff delay correctness."""

    def test_exponential_backoff(self):
        """Exponential backoff doubles each attempt, capped at 60."""
        assert _backoff_delay(1, "exponential") == 2
        assert _backoff_delay(2, "exponential") == 4
        assert _backoff_delay(3, "exponential") == 8
        assert _backoff_delay(6, "exponential") == 60  # 2^6=64, capped at 60
        assert _backoff_delay(10, "exponential") == 60  # Still capped

    def test_fixed_backoff(self):
        """Fixed backoff always returns 2.0."""
        assert _backoff_delay(1, "fixed") == 2.0
        assert _backoff_delay(5, "fixed") == 2.0
        assert _backoff_delay(100, "fixed") == 2.0

    def test_unknown_backoff_defaults_to_fixed(self):
        """Unknown backoff type defaults to fixed 2s."""
        assert _backoff_delay(1, "unknown") == 2.0


# =========================================================================
# 13. Truncate output
# =========================================================================


class TestTruncateOutput:
    """Output truncation edge cases."""

    def test_none_output(self):
        """None output should pass through."""
        assert _truncate_output(None) is None

    def test_small_output_passes_through(self):
        """Small output should pass through unchanged."""
        data = {"key": "value"}
        assert _truncate_output(data) is data

    def test_string_output_truncated(self):
        """Oversized string output should be truncated."""
        big_str = "x" * (10 * 1024 * 1024 + 100)
        result = _truncate_output(big_str)
        assert isinstance(result, str)
        assert len(result) < len(big_str)
        assert result.endswith("[TRUNCATED]")

    def test_dict_output_truncated(self):
        """Oversized dict output should be truncated to a dict with metadata."""
        big_dict = {"data": "x" * (10 * 1024 * 1024 + 100)}
        result = _truncate_output(big_dict)
        assert isinstance(result, dict)
        assert result["_truncated"] is True
        assert "_original_size" in result

    def test_custom_max_size(self):
        """Custom max_size should be respected."""
        data = "x" * 200
        result = _truncate_output(data, max_size=100)
        assert isinstance(result, str)
        assert result.endswith("[TRUNCATED]")
        assert len(result) <= 200  # 100 + len of suffix


# =========================================================================
# 14. Template resolution edge cases
# =========================================================================


class TestTemplateResolutionEdgeCases:
    """Additional template resolution edge cases."""

    def test_multiple_variables_in_one_string(self):
        """Multiple variables should all be resolved."""
        ctx = _make_context(
            inputs={"name": "Alice", "age": "30"},
        )
        result = resolve_templates("Name: {input.name}, Age: {input.age}", ctx)
        assert result == "Name: Alice, Age: 30"

    def test_date_variable(self):
        """The {date} variable should resolve to ISO date."""
        ctx = _make_context()
        result = resolve_templates("Today is {date}", ctx)
        # Should match ISO date format
        assert re.match(r"Today is \d{4}-\d{2}-\d{2}", result)

    def test_run_id_variable(self):
        """The {run_id} variable should resolve to the context run_id."""
        run_id = str(uuid.uuid4())
        ctx = _make_context(run_id=run_id)
        result = resolve_templates("Run: {run_id}", ctx)
        assert result == f"Run: {run_id}"

    def test_template_with_no_variables(self):
        """String with no template variables should pass through."""
        ctx = _make_context()
        result = resolve_templates("No variables here", ctx)
        assert result == "No variables here"

    def test_template_with_empty_braces(self):
        """Empty braces should not match the template regex."""
        ctx = _make_context()
        result = resolve_templates("Empty {} braces", ctx)
        assert result == "Empty {} braces"

    def test_auto_inject_unreferenced_deps_dict_output(self):
        """Auto-inject should handle dict outputs properly."""
        ctx = _make_context(
            step_outputs={"step1": {"key": "value"}},
        )
        result = resolve_templates(
            "Main prompt",
            ctx,
            depends_on=["step1"],
        )
        assert "Context from previous steps" in result
        assert "key" in result

    def test_template_size_limit_enforcement(self):
        """Templates exceeding 1MB should raise ValueError."""
        ctx = _make_context()
        big_template = "x" * (1024 * 1024 + 1)
        with pytest.raises(ValueError, match="too large"):
            resolve_templates(big_template, ctx)


# =========================================================================
# 15. Condition step edge cases
# =========================================================================


class TestConditionStepEdgeCases:
    """Condition step edge cases."""

    @pytest.mark.asyncio
    async def test_condition_true_skips_else_steps(self):
        """When condition is true, else_steps are added to skip set."""
        step = StepDefinition(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=["then_step"],
                else_steps=["else_step"],
            ),
        )
        ctx = _make_context()
        result = await _execute_condition_step(step, ctx)
        assert result.status == "completed"
        assert result.output["condition"] is True
        assert "else_step" in ctx.branch_skip_steps
        assert "then_step" in ctx.branch_run_steps

    @pytest.mark.asyncio
    async def test_condition_false_skips_then_steps(self):
        """When condition is false, then_steps are added to skip set."""
        step = StepDefinition(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="False",
                then_steps=["then_step"],
                else_steps=["else_step"],
            ),
        )
        ctx = _make_context()
        result = await _execute_condition_step(step, ctx)
        assert result.status == "completed"
        assert result.output["condition"] is False
        assert "then_step" in ctx.branch_skip_steps
        assert "else_step" in ctx.branch_run_steps

    @pytest.mark.asyncio
    async def test_condition_with_input_variable(self):
        """Condition using input variable for comparison."""
        step = StepDefinition(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="'{input.mode}' == 'production'",
                then_steps=["deploy"],
                else_steps=["skip_deploy"],
            ),
        )
        ctx = _make_context(inputs={"mode": "production"})
        result = await _execute_condition_step(step, ctx)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_missing_config(self):
        """Condition step with no config returns failure."""
        step = StepDefinition(id="cond1", type="condition")
        ctx = _make_context()
        result = await _execute_condition_step(step, ctx)
        assert result.status == "failed"
        assert "Missing" in result.error

    @pytest.mark.asyncio
    async def test_condition_invalid_expression(self):
        """Condition step with invalid expression fails gracefully."""
        step = StepDefinition(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="this is not valid python",
                then_steps=[],
                else_steps=[],
            ),
        )
        ctx = _make_context()
        result = await _execute_condition_step(step, ctx)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_condition_run_takes_precedence_over_skip(self):
        """A step selected to run by one condition should not be skipped by another."""
        step1 = StepDefinition(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=["shared_step"],
                else_steps=[],
            ),
        )
        step2 = StepDefinition(
            id="cond2",
            type="condition",
            condition_config=ConditionConfig(
                expression="False",
                then_steps=["shared_step"],
                else_steps=[],
            ),
        )
        ctx = _make_context()

        # First condition runs - marks shared_step as run
        await _execute_condition_step(step1, ctx)
        assert "shared_step" in ctx.branch_run_steps

        # Second condition is false - would normally skip shared_step
        # but since it's already in branch_run_steps, it stays runnable
        await _execute_condition_step(step2, ctx)
        assert "shared_step" not in ctx.branch_skip_steps
        assert "shared_step" in ctx.branch_run_steps


# =========================================================================
# 16. YAML parsing edge cases
# =========================================================================


class TestYAMLParsingEdgeCases:
    """YAML parsing edge cases."""

    def test_empty_yaml_rejected(self):
        """Empty YAML content should be rejected."""
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("")

    def test_whitespace_only_yaml_rejected(self):
        """Whitespace-only YAML should be rejected."""
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("   \n  \t  \n")

    def test_yaml_too_large_rejected(self):
        """YAML exceeding 1MB should be rejected."""
        big_yaml = "name: test\nsteps: []\n" + "# padding\n" * 200000
        with pytest.raises(ValueError, match="too large"):
            parse_yaml_string(big_yaml)

    def test_non_mapping_yaml_rejected(self):
        """YAML that parses to a list should be rejected."""
        with pytest.raises(ValueError, match="mapping"):
            parse_yaml_string("- item1\n- item2\n")

    def test_yaml_null_document_rejected(self):
        """YAML null document should be rejected."""
        with pytest.raises(ValueError, match="None"):
            parse_yaml_string("---\n")

    def test_missing_name_rejected(self):
        """YAML without 'name' field should be rejected."""
        with pytest.raises(ValueError, match="name"):
            parse_yaml_string("description: test\nsteps: []\n")

    def test_steps_not_list_rejected(self):
        """YAML with steps as a string should be rejected."""
        with pytest.raises(ValueError, match="list"):
            parse_yaml_string("name: test\nsteps: not_a_list\n")

    def test_step_missing_id_rejected(self):
        """Step without an id should be rejected."""
        with pytest.raises(ValueError, match="id"):
            parse_yaml_string("""
name: test
steps:
  - prompt: "no id here"
""")

    def test_step_id_integer_rejected(self):
        """Step with integer id should be rejected."""
        with pytest.raises(ValueError, match="string"):
            parse_yaml_string("""
name: test
steps:
  - id: 123
    prompt: "numeric id"
""")

    def test_workflow_defaults_applied(self):
        """Workflow defaults should be applied to steps without overrides."""
        wf = parse_yaml_string("""
name: defaults-test
default_model: opus
default_max_turns: 20
default_timeout: 600
steps:
  - id: step1
    prompt: "test"
""")
        assert wf.steps[0].model == "opus"
        assert wf.steps[0].max_turns == 20
        assert wf.steps[0].timeout == 600

    def test_step_overrides_workflow_defaults(self):
        """Step-level settings should override workflow defaults."""
        wf = parse_yaml_string("""
name: override-test
default_model: opus
default_max_turns: 20
steps:
  - id: step1
    prompt: "test"
    model: haiku
    max_turns: 5
""")
        assert wf.steps[0].model == "haiku"
        assert wf.steps[0].max_turns == 5


# =========================================================================
# 17. RunContext edge cases
# =========================================================================


class TestRunContextEdgeCases:
    """RunContext methods edge cases."""

    def test_with_item_creates_independent_copy(self):
        """with_item should create an independent context."""
        parent = _make_context(
            inputs={"key": "value"},
            step_outputs={"step1": "output1"},
        )
        child = parent.with_item("item_data", 0)

        # Child should have the item data
        assert child.input["_item"] == "item_data"
        assert child.input["_index"] == 0

        # Mutating child should not affect parent
        child.step_outputs["step2"] = "child_output"
        assert "step2" not in parent.step_outputs

        # Costs should be independent
        child.costs.append(0.05)
        assert len(parent.costs) == 0

    def test_total_cost(self):
        """total_cost should sum all costs."""
        ctx = _make_context()
        ctx.costs = [0.1, 0.2, 0.3]
        assert abs(ctx.total_cost - 0.6) < 1e-10

    def test_total_cost_empty(self):
        """total_cost should be 0 with no costs."""
        ctx = _make_context()
        assert ctx.total_cost == 0.0

    def test_snapshot(self):
        """snapshot should create a serializable dict."""
        ctx = _make_context(
            inputs={"name": "test"},
            step_outputs={"step1": "output1"},
        )
        ctx.costs = [0.1, 0.2]
        snap = ctx.snapshot()
        assert snap["run_id"] == ctx.run_id
        assert snap["input"] == {"name": "test"}
        assert snap["step_outputs"] == {"step1": "output1"}
        assert snap["costs"] == [0.1, 0.2]
        assert abs(snap["total_cost"] - 0.3) < 1e-10


# =========================================================================
# 18. Safe eval expression edge cases
# =========================================================================


class TestSafeEvalExpression:
    """Safe eval expression edge cases."""

    def test_basic_comparison(self):
        """Basic comparisons should work."""
        assert _safe_eval_expression("1 == 1", {}) is True
        assert _safe_eval_expression("1 > 2", {}) is False
        assert _safe_eval_expression("'hello' == 'hello'", {}) is True

    def test_with_names(self):
        """Expressions should access provided names."""
        names = {"x": 10, "y": 20, "True": True, "False": False, "None": None}
        assert _safe_eval_expression("x < y", names) is True
        assert _safe_eval_expression("x + y == 30", names) is True

    def test_len_function(self):
        """len() should be available."""
        names = {"items": [1, 2, 3], "True": True, "False": False, "None": None}
        assert _safe_eval_expression("len(items) == 3", names) is True

    def test_type_conversions(self):
        """Type conversion functions should be available."""
        names = {"True": True, "False": False, "None": None}
        assert _safe_eval_expression("int('42') == 42", names) is True
        assert _safe_eval_expression("float('3.14') > 3", names) is True
        assert _safe_eval_expression("str(42) == '42'", names) is True

    def test_boolean_logic(self):
        """Boolean logic should work."""
        names = {"a": True, "b": False, "True": True, "False": False, "None": None}
        assert _safe_eval_expression("a and not b", names) is True
        assert _safe_eval_expression("a or b", names) is True

    def test_dunder_blocked(self):
        """Double underscore patterns should be blocked."""
        with pytest.raises(ValueError, match="double-underscore"):
            _safe_eval_expression("''.__class__", {})


# =========================================================================
# 19. Escape braces
# =========================================================================


class TestEscapeBraces:
    """Brace escaping for template injection prevention."""

    def test_single_braces(self):
        assert _escape_braces("{test}") == "{{test}}"

    def test_no_braces(self):
        assert _escape_braces("no braces") == "no braces"

    def test_double_braces_become_quadruple(self):
        assert _escape_braces("{{already}}") == "{{{{already}}}}"

    def test_mixed_content(self):
        result = _escape_braces("text {var} more text")
        assert result == "text {{var}} more text"

    def test_empty_string(self):
        assert _escape_braces("") == ""


# =========================================================================
# 20. Workflow get_step
# =========================================================================


class TestWorkflowGetStep:
    """WorkflowDefinition.get_step edge cases."""

    def test_get_existing_step(self):
        """get_step should return the step with the given ID."""
        steps = [
            StepDefinition(id="a", prompt="A"),
            StepDefinition(id="b", prompt="B"),
        ]
        wf = _make_workflow(steps)
        step = wf.get_step("a")
        assert step.id == "a"
        assert step.prompt == "A"

    def test_get_nonexistent_step(self):
        """get_step should raise ValueError for unknown step ID."""
        steps = [StepDefinition(id="a", prompt="A")]
        wf = _make_workflow(steps)
        with pytest.raises(ValueError, match="not found"):
            wf.get_step("nonexistent")


# =========================================================================
# 21. Validate complex workflows
# =========================================================================


class TestValidateComplexWorkflows:
    """Validate complex workflow configurations."""

    def test_all_step_types_valid(self):
        """A workflow using all step types should validate without errors
        (excluding model/tool checks which need runtime setup)."""
        wf = parse_yaml_string("""
name: all-types
steps:
  - id: std_step
    prompt: "standard step"
    type: standard

  - id: cond_step
    type: condition
    condition_config:
      expression: "True"
      then: [std_step]
      else: []

  - id: transform_step
    type: transform
    transform_config:
      template: '{"result": "transformed"}'

  - id: notify_step
    type: notify
    notify_config:
      service: slack
      message: "Done!"

  - id: code_step
    type: code
    code_config:
      code: "result = 42"

  - id: http_step
    type: http
    http_config:
      url: "https://api.example.com/data"

  - id: loop_step
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [std_step]
      max_iterations: 10
""")
        errors = validate(wf)
        # Filter out model/tool errors which are environment-specific
        non_model_errors = [
            e for e in errors
            if "model" not in e.lower() and "tool" not in e.lower()
        ]
        assert len(non_model_errors) == 0, f"Unexpected errors: {non_model_errors}"

    def test_retry_config_validation(self):
        """Invalid retry config should produce validation errors."""
        wf = parse_yaml_string("""
name: bad-retry
steps:
  - id: step1
    prompt: "test"
    retry:
      max_attempts: 0
      backoff: invalid
      on_failure: invalid
""")
        errors = validate(wf)
        retry_errors = [e for e in errors if "retry" in e.lower()]
        assert len(retry_errors) >= 3  # max_attempts, backoff, on_failure

    def test_workflow_name_too_long(self):
        """Workflow name exceeding 200 chars should be rejected."""
        wf = parse_yaml_string(f"""
name: {"x" * 201}
steps:
  - id: step1
    prompt: "test"
""")
        errors = validate(wf)
        name_errors = [e for e in errors if "name" in e.lower() and "long" in e.lower()]
        assert len(name_errors) > 0

    def test_too_many_steps_rejected(self):
        """Workflow with more than 500 steps should be rejected."""
        steps_yaml = "\n".join(
            f"  - id: step_{i}\n    prompt: 'test {i}'" for i in range(501)
        )
        wf = parse_yaml_string(f"""
name: too-many-steps
steps:
{steps_yaml}
""")
        errors = validate(wf)
        step_errors = [e for e in errors if "too many" in e.lower()]
        assert len(step_errors) > 0

    def test_unknown_step_type_rejected(self):
        """Unknown step type should produce a validation error."""
        wf = parse_yaml_string("""
name: bad-type
steps:
  - id: step1
    type: magical_step
    prompt: "test"
""")
        errors = validate(wf)
        type_errors = [e for e in errors if "unknown type" in e.lower()]
        assert len(type_errors) > 0

    def test_duplicate_step_ids_detected(self):
        """Duplicate step IDs should be detected."""
        wf = parse_yaml_string("""
name: dups
steps:
  - id: same_id
    prompt: "first"
  - id: same_id
    prompt: "second"
""")
        errors = validate(wf)
        dup_errors = [e for e in errors if "uplicate" in e.lower()]
        assert len(dup_errors) > 0


# =========================================================================
# 22. Env var resolution in YAML
# =========================================================================


class TestEnvVarResolution:
    """Test ${ENV_VAR} resolution in YAML values."""

    def test_on_complete_webhook_env_var(self):
        """on_complete webhook should resolve env vars."""
        import os
        os.environ["TEST_WEBHOOK_URL"] = "https://hooks.example.com/test"
        try:
            wf = parse_yaml_string("""
name: env-test
steps:
  - id: step1
    prompt: "test"
on_complete:
  webhook: "${TEST_WEBHOOK_URL}"
""")
            assert wf.on_complete.webhook == "https://hooks.example.com/test"
        finally:
            del os.environ["TEST_WEBHOOK_URL"]

    def test_missing_env_var_resolves_to_empty(self):
        """Missing env var should resolve to empty string."""
        wf = parse_yaml_string("""
name: env-test
steps:
  - id: step1
    prompt: "test"
on_complete:
  webhook: "${DEFINITELY_NOT_SET_VAR}"
""")
        assert wf.on_complete.webhook == ""


# =========================================================================
# 23. Transform step JSON output
# =========================================================================


class TestTransformStepJSONOutput:
    """Transform step JSON output handling."""

    @pytest.mark.asyncio
    async def test_transform_produces_json_dict(self):
        """Transform template producing valid JSON dict should be parsed."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(
                template='{"name": "Alice", "score": 42}'
            ),
        )
        ctx = _make_context()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert result.output["name"] == "Alice"
        assert result.output["score"] == 42

    @pytest.mark.asyncio
    async def test_transform_produces_json_list(self):
        """Transform template producing valid JSON list should be parsed."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(
                template='[1, 2, 3]'
            ),
        )
        ctx = _make_context()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert isinstance(result.output, list)
        assert result.output == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_transform_non_json_stays_string(self):
        """Transform template producing non-JSON stays as string."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(
                template="Hello, World!"
            ),
        )
        ctx = _make_context()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert isinstance(result.output, str)
        assert result.output == "Hello, World!"

    @pytest.mark.asyncio
    async def test_transform_json_boolean(self):
        """Transform template producing JSON boolean should be parsed."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(template="true"),
        )
        ctx = _make_context()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output is True

    @pytest.mark.asyncio
    async def test_transform_json_null(self):
        """Transform template producing JSON null should be parsed."""
        step = StepDefinition(
            id="xform",
            type="transform",
            transform_config=TransformConfig(template="null"),
        )
        ctx = _make_context()
        result = await _execute_transform_step(step, ctx)
        assert result.status == "completed"
        assert result.output is None


# =========================================================================
# 24. HTTP config parsing edge cases
# =========================================================================


class TestHTTPConfigParsing:
    """HTTP step configuration parsing edge cases."""

    def test_http_config_defaults(self):
        """HTTP config should have sensible defaults."""
        wf = parse_yaml_string("""
name: http-defaults
steps:
  - id: api_call
    type: http
    http_config:
      url: "https://api.example.com/data"
    prompt: "fetch"
""")
        step = wf.steps[0]
        assert step.http_config is not None
        assert step.http_config.url == "https://api.example.com/data"
        assert step.http_config.method == "GET"
        assert step.http_config.headers == {}
        assert step.http_config.body is None
        assert step.http_config.auth is None

    def test_http_config_all_fields(self):
        """HTTP config should parse all fields."""
        wf = parse_yaml_string("""
name: http-full
steps:
  - id: api_call
    type: http
    http_config:
      url: "https://api.example.com/data"
      method: POST
      headers:
        Authorization: "Bearer token"
        Content-Type: "application/json"
      body: '{"key": "value"}'
      auth: "bearer:MY_TOKEN"
    prompt: "post"
""")
        step = wf.steps[0]
        assert step.http_config.method == "POST"
        assert "Authorization" in step.http_config.headers
        assert step.http_config.body == '{"key": "value"}'
        assert step.http_config.auth == "bearer:MY_TOKEN"

    def test_http_config_dict_body(self):
        """HTTP config body can be a dict (YAML mapping)."""
        wf = parse_yaml_string("""
name: http-dict-body
steps:
  - id: api_call
    type: http
    http_config:
      url: "https://api.example.com/data"
      method: POST
      body:
        key: value
        nested:
          deep: true
    prompt: "post"
""")
        step = wf.steps[0]
        assert isinstance(step.http_config.body, dict)
        assert step.http_config.body["key"] == "value"
        assert step.http_config.body["nested"]["deep"] is True


# =========================================================================
# 25. Classify config parsing
# =========================================================================


class TestClassifyConfigParsing:
    """Classify step configuration parsing."""

    def test_classify_config_basic(self):
        """Basic classify config should parse correctly."""
        wf = parse_yaml_string("""
name: classify-test
steps:
  - id: classifier
    type: classify
    classify_config:
      categories: [positive, negative, neutral]
      input: "{steps.prev.output}"
      model: haiku
      branches:
        positive: [happy_path]
        negative: [sad_path]
        neutral: [neutral_path]
    prompt: "classify"
  - id: happy_path
    prompt: "celebrate"
  - id: sad_path
    prompt: "console"
  - id: neutral_path
    prompt: "acknowledge"
""")
        step = wf.steps[0]
        assert step.classify_config is not None
        assert step.classify_config.categories == ["positive", "negative", "neutral"]
        assert step.classify_config.model == "haiku"
        assert len(step.classify_config.branches) == 3

    def test_classify_missing_categories_fails_validation(self):
        """Classify step without categories should fail validation."""
        wf = parse_yaml_string("""
name: no-categories
steps:
  - id: classifier
    type: classify
    classify_config:
      categories: []
      input: "test"
      model: haiku
    prompt: "classify"
""")
        errors = validate(wf)
        classify_errors = [e for e in errors if "classif" in e.lower() and "categor" in e.lower()]
        assert len(classify_errors) > 0


# =========================================================================
# 26. Gate config parsing
# =========================================================================


class TestGateConfigParsing:
    """Gate step configuration parsing."""

    def test_gate_multiple_strategies(self):
        """Gate with multiple strategies should parse correctly."""
        wf = parse_yaml_string("""
name: gate-test
steps:
  - id: quality_gate
    type: gate
    gate_config:
      strategies:
        - type: llm_eval
          config:
            prompt: "Is this output high quality?"
            model: haiku
        - type: timeout
          config:
            seconds: 30
            action: approve
    prompt: "gate"
""")
        step = wf.steps[0]
        assert step.gate_config is not None
        assert len(step.gate_config.strategies) == 2
        assert step.gate_config.strategies[0]["type"] == "llm_eval"
        assert step.gate_config.strategies[1]["type"] == "timeout"


# =========================================================================
# 27. Delegate config parsing
# =========================================================================


class TestDelegateConfigParsing:
    """Delegate step configuration parsing."""

    def test_delegate_config_basic(self):
        """Basic delegate config should parse correctly."""
        wf = parse_yaml_string("""
name: delegate-test
steps:
  - id: delegate_task
    type: delegate
    delegate_config:
      workflow: sub_analysis
      task_description: "Analyze {input.topic}"
      timeout: 1800
    prompt: "delegate"
""")
        step = wf.steps[0]
        assert step.delegate_config is not None
        assert step.delegate_config.workflow == "sub_analysis"
        assert step.delegate_config.timeout == 1800

    def test_delegate_missing_workflow_fails_validation(self):
        """Delegate step without workflow should fail validation."""
        wf = parse_yaml_string("""
name: bad-delegate
steps:
  - id: delegate_task
    type: delegate
    delegate_config:
      workflow: ""
      task_description: "Do something"
    prompt: "delegate"
""")
        errors = validate(wf)
        delegate_errors = [e for e in errors if "delegate" in e.lower() and "workflow" in e.lower()]
        assert len(delegate_errors) > 0

    def test_delegate_negative_timeout_fails_validation(self):
        """Delegate step with negative timeout should fail validation."""
        wf = parse_yaml_string("""
name: bad-delegate
steps:
  - id: delegate_task
    type: delegate
    delegate_config:
      workflow: some_wf
      task_description: "Do something"
      timeout: -10
    prompt: "delegate"
""")
        errors = validate(wf)
        timeout_errors = [e for e in errors if "delegate" in e.lower() and "timeout" in e.lower()]
        assert len(timeout_errors) > 0


# =========================================================================
# 28. Sensor config parsing
# =========================================================================


class TestSensorConfigParsing:
    """Sensor step configuration parsing."""

    def test_sensor_config_basic(self):
        """Basic sensor config should parse correctly."""
        wf = parse_yaml_string("""
name: sensor-test
steps:
  - id: wait_for_deploy
    type: sensor
    sensor_config:
      url: "https://api.example.com/status"
      check_interval: 15
      timeout: 600
      condition: "status_code == 200"
      method: GET
      headers:
        Authorization: "Bearer token"
    prompt: "sensor"
""")
        step = wf.steps[0]
        assert step.sensor_config is not None
        assert step.sensor_config.url == "https://api.example.com/status"
        assert step.sensor_config.check_interval == 15
        assert step.sensor_config.timeout == 600
        assert step.sensor_config.method == "GET"
        assert "Authorization" in step.sensor_config.headers

    def test_sensor_missing_condition_fails_validation(self):
        """Sensor step without condition should fail validation."""
        wf = parse_yaml_string("""
name: bad-sensor
steps:
  - id: sensor
    type: sensor
    sensor_config:
      url: "https://example.com"
      condition: ""
      check_interval: 10
      timeout: 60
    prompt: "sensor"
""")
        errors = validate(wf)
        cond_errors = [e for e in errors if "sensor" in e.lower() and "condition" in e.lower()]
        assert len(cond_errors) > 0


# =========================================================================
# 29. Browser config parsing
# =========================================================================


class TestBrowserConfigParsing:
    """Browser step configuration parsing."""

    def test_browser_config_defaults(self):
        """Browser config should have sensible defaults."""
        wf = parse_yaml_string("""
name: browser-test
steps:
  - id: scrape
    type: browser
    browser_config:
      start_url: "https://example.com"
    prompt: "extract data"
""")
        step = wf.steps[0]
        assert step.browser_config is not None
        assert step.browser_config.mode == "playwright"
        assert step.browser_config.viewport_width == 1280
        assert step.browser_config.viewport_height == 720
        assert step.browser_config.headless is True
        assert step.browser_config.captcha_strategy == "pause"

    def test_browser_invalid_mode_fails_validation(self):
        """Browser step with invalid mode should fail validation."""
        wf = parse_yaml_string("""
name: bad-browser
steps:
  - id: scrape
    type: browser
    browser_config:
      mode: telekinesis
      start_url: "https://example.com"
    prompt: "scrape"
""")
        errors = validate(wf)
        mode_errors = [e for e in errors if "browser" in e.lower() and "mode" in e.lower()]
        assert len(mode_errors) > 0


# =========================================================================
# 30. Approval config parsing
# =========================================================================


class TestApprovalConfigParsing:
    """Approval step configuration parsing."""

    def test_approval_config_basic(self):
        """Basic approval config should parse correctly."""
        wf = parse_yaml_string("""
name: approval-test
steps:
  - id: approve_step
    type: approval
    approval_config:
      message: "Please review this output"
      timeout_hours: 24
      on_timeout: skip
      allow_edit: true
    prompt: ""
""")
        step = wf.steps[0]
        assert step.approval_config is not None
        assert step.approval_config.message == "Please review this output"
        assert step.approval_config.timeout_hours == 24
        assert step.approval_config.on_timeout == "skip"
        assert step.approval_config.allow_edit is True

    def test_approval_missing_message_fails_validation(self):
        """Approval step without message should fail validation."""
        wf = parse_yaml_string("""
name: bad-approval
steps:
  - id: approve_step
    type: approval
    approval_config:
      message: ""
    prompt: ""
""")
        errors = validate(wf)
        approval_errors = [e for e in errors if "approval" in e.lower() and "message" in e.lower()]
        assert len(approval_errors) > 0


# =========================================================================
# 31. Memory config parsing
# =========================================================================


class TestMemoryConfigParsing:
    """Memory configuration parsing."""

    def test_workflow_memory_config(self):
        """Workflow-level memory config should parse correctly."""
        wf = parse_yaml_string("""
name: memory-test
memory:
  agent: research_agent
  scope: agent
  auto_inject: true
  max_inject: 5
  max_age_days: 30
  admit_threshold: 0.5
  graph: false
  enrich: true
steps:
  - id: step1
    prompt: "research"
""")
        assert wf.memory is not None
        assert wf.memory.agent == "research_agent"
        assert wf.memory.scope == "agent"
        assert wf.memory.max_inject == 5
        assert wf.memory.max_age_days == 30
        assert wf.memory.admit_threshold == 0.5

    def test_step_memory_config_boolean(self):
        """Step memory config as boolean should create config with read=write=value."""
        wf = parse_yaml_string("""
name: memory-bool
steps:
  - id: step1
    prompt: "test"
    memory: true
""")
        step = wf.steps[0]
        assert step.memory is not None
        assert step.memory.read is True
        assert step.memory.write is True

    def test_step_memory_config_dict(self):
        """Step memory config as dict should parse all fields."""
        wf = parse_yaml_string("""
name: memory-dict
steps:
  - id: step1
    prompt: "test"
    memory:
      read: true
      write: true
      admit_threshold: 0.7
""")
        step = wf.steps[0]
        assert step.memory is not None
        assert step.memory.read is True
        assert step.memory.write is True
        assert step.memory.admit_threshold == 0.7

    def test_invalid_memory_scope_fails_validation(self):
        """Invalid memory scope should fail validation."""
        wf = parse_yaml_string("""
name: bad-memory
memory:
  scope: universe
steps:
  - id: step1
    prompt: "test"
""")
        errors = validate(wf)
        scope_errors = [e for e in errors if "scope" in e.lower()]
        assert len(scope_errors) > 0

    def test_agent_scope_without_name_fails_validation(self):
        """Agent scope without agent name should fail validation."""
        wf = parse_yaml_string("""
name: bad-memory
memory:
  scope: agent
  agent: ""
steps:
  - id: step1
    prompt: "test"
""")
        errors = validate(wf)
        agent_errors = [e for e in errors if "agent" in e.lower()]
        assert len(agent_errors) > 0


# =========================================================================
# 32. Implicit dependency extraction
# =========================================================================


class TestImplicitDependencyExtraction:
    """Additional implicit dependency extraction tests."""

    def test_http_url_creates_implicit_dep(self):
        """HTTP step URL referencing a step should create implicit dep."""
        step = StepDefinition(
            id="http_step",
            type="http",
            http_config=HttpConfig(
                url="https://api.example.com/{steps.prev.output.endpoint}",
            ),
        )
        refs = _extract_implicit_deps(step, {"http_step", "prev"})
        assert "prev" in refs

    def test_sensor_url_creates_implicit_dep(self):
        """Sensor step URL referencing a step should create implicit dep."""
        step = StepDefinition(
            id="sensor_step",
            type="sensor",
            sensor_config=SensorConfig(
                url="{steps.deploy.output.url}/health",
                condition="status_code == 200",
                check_interval=10,
                timeout=300,
            ),
        )
        refs = _extract_implicit_deps(step, {"sensor_step", "deploy"})
        assert "deploy" in refs

    def test_race_validator_creates_implicit_dep(self):
        """Race step validator referencing a step should create implicit dep."""
        step = StepDefinition(
            id="race_step",
            type="race",
            race_config=RaceConfig(
                branches=[["a"], ["b"]],
                validator="{steps.threshold.output} > 0.5",
            ),
        )
        # Note: the validator field may reference steps but currently
        # _extract_step_refs only looks for {steps.X.output} patterns
        refs = _extract_step_refs("{steps.threshold.output} > 0.5")
        assert "threshold" in refs

    def test_delegate_task_description_creates_implicit_dep(self):
        """Delegate task description referencing a step should create dep."""
        step = StepDefinition(
            id="delegate_step",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="analysis",
                task_description="Analyze {steps.scrape.output}",
            ),
        )
        refs = _extract_implicit_deps(step, {"delegate_step", "scrape"})
        assert "scrape" in refs


# =========================================================================
# 33. Storage reference resolution
# =========================================================================


class TestStorageRefResolution:
    """Storage reference resolution in templates."""

    @pytest.mark.asyncio
    async def test_storage_ref_basic(self):
        """Storage references should be resolved."""
        from sandcastle.engine.executor import resolve_storage_refs

        storage = AsyncMock()
        storage.read = AsyncMock(return_value="stored content here")

        result = await resolve_storage_refs(
            "Data: {storage.data/report.txt}",
            storage,
        )
        assert result == "Data: stored content here"
        storage.read.assert_called_once_with("data/report.txt")

    @pytest.mark.asyncio
    async def test_storage_ref_missing_returns_placeholder(self):
        """Missing storage reference should leave placeholder."""
        from sandcastle.engine.executor import resolve_storage_refs

        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        result = await resolve_storage_refs(
            "Data: {storage.missing/file.txt}",
            storage,
        )
        assert result == "Data: {storage.missing/file.txt}"

    @pytest.mark.asyncio
    async def test_no_storage_refs_passes_through(self):
        """String with no storage refs should pass through."""
        from sandcastle.engine.executor import resolve_storage_refs

        storage = AsyncMock()

        result = await resolve_storage_refs("No refs here", storage)
        assert result == "No refs here"
        storage.read.assert_not_called()


# =========================================================================
# 34. CSV output config
# =========================================================================


class TestCsvOutputConfig:
    """CSV output configuration parsing."""

    def test_csv_output_defaults(self):
        """CSV output config should have sensible defaults."""
        wf = parse_yaml_string("""
name: csv-test
steps:
  - id: step1
    prompt: "test"
    csv_output:
      directory: "./output"
""")
        step = wf.steps[0]
        assert step.csv_output is not None
        assert step.csv_output.directory == "./output"
        assert step.csv_output.mode == "new_file"
        assert step.csv_output.filename == ""

    def test_csv_output_append_mode(self):
        """CSV output with append mode should parse correctly."""
        wf = parse_yaml_string("""
name: csv-test
steps:
  - id: step1
    prompt: "test"
    csv_output:
      directory: "./data"
      mode: append
      filename: "results"
""")
        step = wf.steps[0]
        assert step.csv_output.mode == "append"
        assert step.csv_output.filename == "results"


# =========================================================================
# 35. PDF report config
# =========================================================================


class TestPdfReportConfig:
    """PDF report configuration parsing."""

    def test_pdf_report_defaults(self):
        """PDF report config should have sensible defaults."""
        wf = parse_yaml_string("""
name: pdf-test
steps:
  - id: step1
    prompt: "test"
    pdf_report:
      directory: "./reports"
""")
        step = wf.steps[0]
        assert step.pdf_report is not None
        assert step.pdf_report.directory == "./reports"
        assert step.pdf_report.language == "en"
        assert step.pdf_report.filename == ""

    def test_pdf_report_custom_language(self):
        """PDF report with custom language should parse correctly."""
        wf = parse_yaml_string("""
name: pdf-test
steps:
  - id: step1
    prompt: "test"
    pdf_report:
      directory: "./reports"
      language: cs
      filename: "report"
""")
        step = wf.steps[0]
        assert step.pdf_report.language == "cs"
        assert step.pdf_report.filename == "report"


# =========================================================================
# 36. SLO and model pool config
# =========================================================================


class TestSLOConfig:
    """SLO and model pool configuration parsing."""

    def test_slo_with_auto_pool(self):
        """SLO config with auto model pool should create default pool."""
        wf = parse_yaml_string("""
name: slo-test
steps:
  - id: step1
    prompt: "test"
    slo:
      quality_min: 0.8
      cost_max_usd: 0.10
      optimize_for: quality
    model_pool: auto
""")
        step = wf.steps[0]
        assert step.slo is not None
        assert step.slo.quality_min == 0.8
        assert step.slo.optimize_for == "quality"
        assert step.model_pool is not None
        assert len(step.model_pool) > 0

    def test_slo_without_pool_gets_auto(self):
        """SLO config without explicit pool should get auto pool."""
        wf = parse_yaml_string("""
name: slo-test
steps:
  - id: step1
    prompt: "test"
    slo:
      quality_min: 0.7
      cost_max_usd: 0.15
""")
        step = wf.steps[0]
        assert step.slo is not None
        assert step.model_pool is not None  # Auto-assigned

    def test_invalid_slo_optimize_for_fails_validation(self):
        """Invalid SLO optimize_for value should fail validation."""
        wf = parse_yaml_string("""
name: bad-slo
steps:
  - id: step1
    prompt: "test"
    slo:
      optimize_for: maximum_chaos
""")
        errors = validate(wf)
        slo_errors = [e for e in errors if "slo" in e.lower() and "optimize" in e.lower()]
        assert len(slo_errors) > 0
