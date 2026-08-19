"""
WAVE 6 DEEP AUDIT - EXECUTOR BUG FIXES
=======================================
Targeted tests for HIGH/MEDIUM severity bugs found in Wave 5 exploration.

Bugs covered:
1. WorkflowPaused not propagated in loop step
2. WorkflowPaused not propagated in race step
3. Gate step returns after first strategy (multiple human approvals)
4. Condition step template injection risk
5. _detect_cycles only finds first cycle per component

Run:
    cd /Users/gizmax/Documents/Sandcastle
    .venv/bin/python -m pytest tests/test_executor_wave6.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    ConditionConfig,
    GateConfig,
    LoopConfig,
    RaceConfig,
    StepDefinition,
    WorkflowDefinition,
    _detect_cycles,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    WorkflowPaused,
    _execute_condition_step,
    _execute_gate_step,
    _execute_loop_step,
    _execute_race_step,
)


# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_db():
    """Bypass all DB writes during tests."""
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
    ):
        yield


def make_ctx(**kwargs) -> RunContext:
    return RunContext(
        run_id=kwargs.get("run_id", str(uuid.uuid4())),
        input=kwargs.get("input", {}),
        step_outputs=kwargs.get("step_outputs", {}),
        costs=kwargs.get("costs", []),
        max_cost_usd=kwargs.get("max_cost_usd", None),
        workflow_name=kwargs.get("workflow_name", "test_wf"),
    )


def make_step(**kwargs) -> StepDefinition:
    return StepDefinition(
        id=kwargs.get("id", "s1"),
        type=kwargs.get("type", "standard"),
        prompt=kwargs.get("prompt", "test"),
        model=kwargs.get("model", "haiku"),
        depends_on=kwargs.get("depends_on", []),
        timeout=kwargs.get("timeout", 60),
        loop_config=kwargs.get("loop_config", None),
        race_config=kwargs.get("race_config", None),
        gate_config=kwargs.get("gate_config", None),
        condition_config=kwargs.get("condition_config", None),
    )


def make_workflow(steps: list[StepDefinition]) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="test_wf",
        description="test workflow",
        default_model="haiku",
        default_max_turns=3,
        default_timeout=60,
        steps=steps,
    )


# ──────────────────────────────────────────────────────────────
# BUG 1: WorkflowPaused not propagated in loop step
# ──────────────────────────────────────────────────────────────


class TestLoopStepWorkflowPaused:
    """Verify that WorkflowPaused raised inside a loop sub-step
    propagates up instead of being caught by the generic except."""

    @pytest.mark.asyncio
    async def test_loop_propagates_workflow_paused(self):
        """When a sub-step inside a loop raises WorkflowPaused,
        _execute_loop_step must re-raise it."""
        approval_id = "approval-123"
        run_id = str(uuid.uuid4())

        # Create a loop step that iterates over a list
        sub_step = make_step(id="approval_gate", type="gate")
        loop_step = make_step(
            id="loop1",
            type="loop",
            loop_config=LoopConfig(
                over="{input.items}",
                step_ids=["approval_gate"],
                max_iterations=10,
            ),
        )
        workflow = make_workflow([loop_step, sub_step])
        context = make_ctx(
            run_id=run_id,
            input={"items": ["item1", "item2", "item3"]},
        )

        # Mock execute_step_with_retry to raise WorkflowPaused on first call
        mock_result = AsyncMock(
            side_effect=WorkflowPaused(approval_id=approval_id, run_id=run_id)
        )

        with patch("sandcastle.engine.executor.execute_step_with_retry", mock_result):
            with pytest.raises(WorkflowPaused) as exc_info:
                await _execute_loop_step(
                    loop_step, context, MagicMock(), MagicMock(), workflow, depth=0
                )

            assert exc_info.value.approval_id == approval_id
            assert exc_info.value.run_id == run_id

    @pytest.mark.asyncio
    async def test_loop_still_catches_regular_exceptions(self):
        """Regular exceptions inside loops should still be caught
        and returned as failed StepResults."""
        loop_step = make_step(
            id="loop1",
            type="loop",
            loop_config=LoopConfig(
                over="{input.items}",
                step_ids=["sub1"],
                max_iterations=10,
            ),
        )
        sub_step = make_step(id="sub1", type="standard")
        workflow = make_workflow([loop_step, sub_step])
        context = make_ctx(input={"items": ["a", "b"]})

        mock_result = AsyncMock(side_effect=RuntimeError("something broke"))

        with patch("sandcastle.engine.executor.execute_step_with_retry", mock_result):
            result = await _execute_loop_step(
                loop_step, context, MagicMock(), MagicMock(), workflow, depth=0
            )
            assert result.status == "failed"
            assert "something broke" in result.error

    @pytest.mark.asyncio
    async def test_loop_paused_on_second_iteration(self):
        """WorkflowPaused raised on the second iteration still propagates."""
        run_id = str(uuid.uuid4())
        loop_step = make_step(
            id="loop1",
            type="loop",
            loop_config=LoopConfig(
                over="{input.items}",
                step_ids=["sub1"],
                max_iterations=10,
            ),
        )
        sub_step = make_step(id="sub1", type="standard")
        workflow = make_workflow([loop_step, sub_step])
        context = make_ctx(run_id=run_id, input={"items": ["ok", "pause_here"]})

        call_count = 0

        async def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return StepResult(step_id="sub1", output="done", status="completed")
            raise WorkflowPaused(approval_id="ap-2nd", run_id=run_id)

        with patch("sandcastle.engine.executor.execute_step_with_retry", side_effect=mock_execute):
            with pytest.raises(WorkflowPaused) as exc_info:
                await _execute_loop_step(
                    loop_step, context, MagicMock(), MagicMock(), workflow, depth=0
                )
            assert exc_info.value.approval_id == "ap-2nd"


# ──────────────────────────────────────────────────────────────
# BUG 2: WorkflowPaused not propagated in race step
# ──────────────────────────────────────────────────────────────


class TestRaceStepWorkflowPaused:
    """Verify that WorkflowPaused raised inside a race branch
    propagates up instead of being silently swallowed."""

    @pytest.mark.asyncio
    async def test_race_propagates_workflow_paused_from_branch(self):
        """When a sub-step inside a race branch raises WorkflowPaused,
        _execute_race_step must cancel other branches and re-raise it."""
        run_id = str(uuid.uuid4())
        approval_id = "race-approval-1"

        race_step = make_step(
            id="race1",
            type="race",
            race_config=RaceConfig(
                branches=[["branch_a"], ["branch_b"]],
            ),
        )
        branch_a = make_step(id="branch_a", type="gate")
        branch_b = make_step(id="branch_b", type="standard")
        workflow = make_workflow([race_step, branch_a, branch_b])
        context = make_ctx(run_id=run_id, input={})

        async def mock_execute(step, ctx, sandbox, storage, **kwargs):
            if step.id == "branch_a":
                raise WorkflowPaused(approval_id=approval_id, run_id=run_id)
            # branch_b takes a bit longer
            await asyncio.sleep(0.5)
            return StepResult(step_id="branch_b", output="b_done", status="completed")

        with patch("sandcastle.engine.executor.execute_step_with_retry", side_effect=mock_execute):
            with pytest.raises(WorkflowPaused) as exc_info:
                await _execute_race_step(
                    race_step, context, MagicMock(), MagicMock(), workflow, depth=0
                )

            assert exc_info.value.approval_id == approval_id
            assert exc_info.value.run_id == run_id

    @pytest.mark.asyncio
    async def test_race_still_catches_regular_exceptions(self):
        """Regular exceptions in race branches should still be handled
        gracefully (continue to other branches or report failure)."""
        race_step = make_step(
            id="race1",
            type="race",
            race_config=RaceConfig(
                branches=[["branch_a"], ["branch_b"]],
            ),
        )
        branch_a = make_step(id="branch_a", type="standard")
        branch_b = make_step(id="branch_b", type="standard")
        workflow = make_workflow([race_step, branch_a, branch_b])
        context = make_ctx(input={})

        async def mock_execute(step, ctx, sandbox, storage, **kwargs):
            if step.id == "branch_a":
                raise RuntimeError("branch_a failed")
            return StepResult(step_id="branch_b", output="b_done", status="completed")

        with patch("sandcastle.engine.executor.execute_step_with_retry", side_effect=mock_execute):
            result = await _execute_race_step(
                race_step, context, MagicMock(), MagicMock(), workflow, depth=0
            )
            # branch_b should win since branch_a failed
            assert result.status == "completed"
            assert result.output == "b_done"

    @pytest.mark.asyncio
    async def test_race_outer_except_propagates_workflow_paused(self):
        """WorkflowPaused at the outer level of the race step is not swallowed."""
        run_id = str(uuid.uuid4())

        race_step = make_step(
            id="race1",
            type="race",
            race_config=RaceConfig(
                branches=[["only_branch"]],
            ),
        )
        only_branch = make_step(id="only_branch", type="gate")
        workflow = make_workflow([race_step, only_branch])
        context = make_ctx(run_id=run_id, input={})

        async def mock_execute(step, ctx, sandbox, storage, **kwargs):
            raise WorkflowPaused(approval_id="outer-ap", run_id=run_id)

        with patch("sandcastle.engine.executor.execute_step_with_retry", side_effect=mock_execute):
            with pytest.raises(WorkflowPaused) as exc_info:
                await _execute_race_step(
                    race_step, context, MagicMock(), MagicMock(), workflow, depth=0
                )
            assert exc_info.value.approval_id == "outer-ap"


# ──────────────────────────────────────────────────────────────
# BUG 3: Gate step returns after first strategy
# ──────────────────────────────────────────────────────────────


class TestGateMultipleStrategies:
    """Verify that gate step processes all strategies instead of
    returning after the first one."""

    @pytest.mark.asyncio
    async def test_gate_creates_multiple_human_approvals(self):
        """When a gate has multiple human strategies, all approval
        requests should be created before raising WorkflowPaused."""
        run_id = str(uuid.uuid4())

        gate_step = make_step(
            id="gate1",
            type="gate",
            gate_config=GateConfig(
                strategies=[
                    {
                        "type": "human",
                        "config": {"message": "Manager approval needed"},
                    },
                    {
                        "type": "human",
                        "config": {"message": "Security team approval needed"},
                    },
                ]
            ),
        )
        context = make_ctx(run_id=run_id)

        # Track how many times session.add is called
        add_calls = []

        mock_session_ctx = AsyncMock()
        mock_session = AsyncMock()
        mock_session.add = lambda obj: add_calls.append(obj)
        mock_session.get = AsyncMock(return_value=None)
        mock_session.commit = AsyncMock()

        # Each refresh gives the approval a unique ID
        refresh_counter = [0]

        async def mock_refresh(approval):
            refresh_counter[0] += 1
            approval.id = uuid.uuid4()

        mock_session.refresh = mock_refresh
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session_ctx):
            with pytest.raises(WorkflowPaused):
                await _execute_gate_step(gate_step, context, MagicMock())

        # Both approval requests should have been created
        assert len(add_calls) == 2, (
            f"Expected 2 approval requests, got {len(add_calls)}"
        )

    @pytest.mark.asyncio
    async def test_gate_timeout_approve_continues_to_next_strategy(self):
        """A timeout strategy that approves should not short-circuit;
        subsequent strategies should also be evaluated."""
        run_id = str(uuid.uuid4())

        gate_step = make_step(
            id="gate1",
            type="gate",
            gate_config=GateConfig(
                strategies=[
                    {
                        "type": "timeout",
                        "config": {"seconds": 0, "action": "approve"},
                    },
                    {
                        "type": "timeout",
                        "config": {"seconds": 0, "action": "approve"},
                    },
                ]
            ),
        )
        context = make_ctx(run_id=run_id)

        result = await _execute_gate_step(gate_step, context, MagicMock())
        assert result.status == "completed"
        assert result.output["decision"] == "approved"
        # Should contain results from both strategies
        assert len(result.output.get("strategies", [])) == 2

    @pytest.mark.asyncio
    async def test_gate_timeout_reject_short_circuits(self):
        """A timeout strategy with action=reject should immediately reject
        without processing further strategies."""
        run_id = str(uuid.uuid4())

        gate_step = make_step(
            id="gate1",
            type="gate",
            gate_config=GateConfig(
                strategies=[
                    {
                        "type": "timeout",
                        "config": {"seconds": 0, "action": "reject"},
                    },
                    {
                        "type": "timeout",
                        "config": {"seconds": 0, "action": "approve"},
                    },
                ]
            ),
        )
        context = make_ctx(run_id=run_id)

        result = await _execute_gate_step(gate_step, context, MagicMock())
        assert result.status == "failed"
        assert result.output["decision"] == "rejected"
        assert result.output["strategy"] == "timeout"

    @pytest.mark.asyncio
    async def test_gate_no_strategies(self):
        """Gate with empty strategies should report failure."""
        gate_step = make_step(
            id="gate1",
            type="gate",
            gate_config=GateConfig(strategies=[]),
        )
        context = make_ctx()

        result = await _execute_gate_step(gate_step, context, MagicMock())
        assert result.status == "failed"


# ──────────────────────────────────────────────────────────────
# BUG 4: Condition step template injection risk
# ──────────────────────────────────────────────────────────────


class TestConditionStepInjection:
    """Verify that condition step evaluation is safe from template injection
    where user-supplied input values could manipulate the expression.

    The canonical way to reference input/step data in conditions is via
    simpleeval's names dict (e.g. ``input.get('key')`` or ``input['key']``).
    The {template} syntax is used for step outputs (e.g. ``{steps.X.output}``).
    """

    @pytest.mark.asyncio
    async def test_condition_basic_with_names_dict(self):
        """Condition using simpleeval names dict for input access."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="input.get('status') == 'active'",
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"status": "active"})

        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_false_with_names_dict(self):
        """Condition evaluating to False correctly populates else_steps."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="input.get('status') == 'active'",
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"status": "inactive"})

        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is False
        assert "step_a" in context.branch_skip_steps

    @pytest.mark.asyncio
    async def test_condition_injection_with_dunder(self):
        """Input containing __import__ in a template variable should be blocked.
        The injection token validator catches 'import' keyword."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.value} == "safe"',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        # Malicious input with dunder
        context = make_ctx(input={"value": '__import__("os")'})

        result = await _execute_condition_step(step, context)
        # Should fail - the injection validator catches the 'import' keyword
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_condition_injection_or_keyword(self):
        """Input containing 'or' keyword should be blocked to prevent
        expression manipulation via template interpolation."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.count} > 10',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        # Attacker tries to inject: "5 or True" which, if interpolated naively,
        # becomes "5 or True > 10" -> truthy (5). The injection token
        # validator now blocks this.
        context = make_ctx(input={"count": "5 or True"})

        result = await _execute_condition_step(step, context)
        assert result.status == "failed"
        assert "unsafe tokens" in result.error

    @pytest.mark.asyncio
    async def test_condition_injection_and_keyword(self):
        """Input containing 'and' keyword should be blocked."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.flag} == True',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"flag": "True and True"})

        result = await _execute_condition_step(step, context)
        assert result.status == "failed"
        assert "unsafe tokens" in result.error

    @pytest.mark.asyncio
    async def test_condition_injection_semicolon(self):
        """Input containing semicolons should be blocked."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.val} > 0',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"val": "1; import os"})

        result = await _execute_condition_step(step, context)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_condition_safe_string_with_or_substring(self):
        """A value like 'Oregon' contains 'or' as a substring but should NOT
        be blocked - only whole-word matches are flagged."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                # Use names dict approach for string comparison
                expression="input.get('state') == 'Oregon'",
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"state": "Oregon"})

        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_numeric_via_template(self):
        """Numeric values in templates should work correctly since
        they don't contain injection tokens."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.count} > 5',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"count": 10})

        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_numeric_comparison_false(self):
        """Numeric comparison evaluating to False."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.count} > 5',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"count": 3})

        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is False

    @pytest.mark.asyncio
    async def test_condition_step_output_numeric(self):
        """Condition referencing numeric step outputs via template works.
        step_outputs stores the output value directly (not wrapped)."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="int('{steps.prev.output.score}') > 50",
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        # step_outputs["prev"] is the raw output from the step
        context = make_ctx(step_outputs={"prev": {"score": 85}})

        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_with_len_via_names(self):
        """Condition using len() on input via names dict.
        Note: simpleeval doesn't allow list literals, so use direct access."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="len(input['items']) > 0",
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"items": [1, 2, 3]})

        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_with_boolean_via_template(self):
        """Condition with boolean template variable resolves correctly."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.enabled}',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"enabled": True})

        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_template_safe_numeric_string(self):
        """A numeric string like '42' should pass injection check since
        it contains no operator keywords."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.val} > 10',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"val": "42"})

        # "42" doesn't contain injection tokens, so it gets interpolated
        # but simpleeval may handle string > int comparison
        result = await _execute_condition_step(step, context)
        # The expression becomes: 42 > 10 (string "42" interpolated)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_condition_template_injection_exec(self):
        """Input containing 'exec' keyword should be blocked."""
        step = make_step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression='{input.cmd} == "ok"',
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        context = make_ctx(input={"cmd": 'exec("bad")'})

        result = await _execute_condition_step(step, context)
        assert result.status == "failed"


# ──────────────────────────────────────────────────────────────
# BUG 5: _detect_cycles only finds first cycle per component
# ──────────────────────────────────────────────────────────────


class TestDetectCyclesMultiple:
    """Verify that _detect_cycles finds ALL cycles in the graph,
    not just the first one per connected component."""

    def test_single_cycle_detected(self):
        """Single cycle should be detected as before."""
        steps = [
            StepDefinition(id="a", type="standard", prompt="", model="haiku", depends_on=["b"]),
            StepDefinition(id="b", type="standard", prompt="", model="haiku", depends_on=["a"]),
        ]
        errors = _detect_cycles(steps)
        assert len(errors) >= 1
        assert any("Cycle detected" in e for e in errors)

    def test_multiple_cycles_in_same_component(self):
        """Multiple cycles in the same connected component should all be reported.

        Graph: a -> b -> a (cycle 1)
               b -> c -> b (cycle 2)
        """
        steps = [
            StepDefinition(
                id="a", type="standard", prompt="", model="haiku", depends_on=["b"]
            ),
            StepDefinition(
                id="b", type="standard", prompt="", model="haiku", depends_on=["a", "c"]
            ),
            StepDefinition(
                id="c", type="standard", prompt="", model="haiku", depends_on=["b"]
            ),
        ]
        errors = _detect_cycles(steps)
        # Should find at least 2 cycle errors (a->b and b->c or c->b)
        assert len(errors) >= 2, (
            f"Expected at least 2 cycle errors, got {len(errors)}: {errors}"
        )

    def test_independent_cycles_in_different_components(self):
        """Cycles in separate connected components should all be found."""
        steps = [
            # Component 1: a <-> b
            StepDefinition(id="a", type="standard", prompt="", model="haiku", depends_on=["b"]),
            StepDefinition(id="b", type="standard", prompt="", model="haiku", depends_on=["a"]),
            # Component 2: c <-> d
            StepDefinition(id="c", type="standard", prompt="", model="haiku", depends_on=["d"]),
            StepDefinition(id="d", type="standard", prompt="", model="haiku", depends_on=["c"]),
        ]
        errors = _detect_cycles(steps)
        # Should find cycles in both components
        assert len(errors) >= 2, (
            f"Expected at least 2 cycle errors (one per component), got {len(errors)}: {errors}"
        )

        # Check that both components are represented in the errors
        all_error_text = " ".join(errors)
        has_ab_cycle = "a" in all_error_text and "b" in all_error_text
        has_cd_cycle = "c" in all_error_text and "d" in all_error_text
        assert has_ab_cycle, f"Missing a<->b cycle in errors: {errors}"
        assert has_cd_cycle, f"Missing c<->d cycle in errors: {errors}"

    def test_no_false_positives_on_acyclic_graph(self):
        """Acyclic graph should produce no cycle errors."""
        steps = [
            StepDefinition(id="a", type="standard", prompt="", model="haiku", depends_on=[]),
            StepDefinition(id="b", type="standard", prompt="", model="haiku", depends_on=["a"]),
            StepDefinition(id="c", type="standard", prompt="", model="haiku", depends_on=["a", "b"]),
        ]
        errors = _detect_cycles(steps)
        assert len(errors) == 0

    def test_self_cycle_detected(self):
        """A step depending on itself should be detected."""
        steps = [
            StepDefinition(id="a", type="standard", prompt="", model="haiku", depends_on=["a"]),
        ]
        errors = _detect_cycles(steps)
        assert len(errors) >= 1
        assert any("a" in e for e in errors)

    def test_triple_cycle_all_edges_reported(self):
        """A triangle cycle should report all participating edges.

        Graph: a -> b, b -> c, c -> a
        With the fix, multiple back-edges should be detected.
        """
        steps = [
            StepDefinition(id="a", type="standard", prompt="", model="haiku", depends_on=["c"]),
            StepDefinition(id="b", type="standard", prompt="", model="haiku", depends_on=["a"]),
            StepDefinition(id="c", type="standard", prompt="", model="haiku", depends_on=["b"]),
        ]
        errors = _detect_cycles(steps)
        assert len(errors) >= 1, f"Expected cycle errors, got: {errors}"

    def test_node_with_multiple_back_edges(self):
        """A node pointing back to two ancestors should report both cycles.

        Graph: a -> b -> c -> {a, b}  (c depends on both a and b, which depend on c)
        Actually: a depends on c, b depends on a, c depends on b
        Plus: add d that depends on c and c depends on d too.
        """
        steps = [
            StepDefinition(id="a", type="standard", prompt="", model="haiku", depends_on=["c"]),
            StepDefinition(id="b", type="standard", prompt="", model="haiku", depends_on=["a"]),
            StepDefinition(id="c", type="standard", prompt="", model="haiku", depends_on=["b", "d"]),
            StepDefinition(id="d", type="standard", prompt="", model="haiku", depends_on=["c"]),
        ]
        errors = _detect_cycles(steps)
        # Should find at least 2 cycles (a-b-c and c-d)
        assert len(errors) >= 2, (
            f"Expected at least 2 cycle errors, got {len(errors)}: {errors}"
        )
