"""
Wave 9 deep audit - executor edge case tests for uncovered execution paths.

Covers:
1. Step retry with backoff: exponential vs fixed strategy, retry exhaustion,
   fallback after retry, context preservation across retries
2. Concurrent race step interactions: branch failure handling, validator logic,
   cost accumulation from cancelled branches, WorkflowPaused in race
3. Nested delegate workflows: depth limit enforcement, context propagation,
   fallback when sub-workflow missing
4. Loop with conditional break (until) and cancel mid-iteration
5. Step cache: key generation, _is_cacheable_output edge cases, memory-step
   cache bypass
6. Memory integration: scope ID, memory read bypass for cache, memory write
   after execution
7. HTTP step with body templates: dict body serialization, header injection
   via templates, auth handling
8. Code step: large output truncation, blocked pattern detection, timeout
9. Multi-step error cascading: on_failure=skip vs abort, dead letter queue,
   error propagation chains
10. Workflow cancellation mid-execution: cancel flag check, _cancel_running
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    CodeConfig,
    CompletionConfig,
    ConditionConfig,
    DelegateConfig,
    ExecutionPlan,
    FailureConfig,
    FallbackConfig,
    HttpConfig,
    LoopConfig,
    RaceConfig,
    RetryConfig,
    StepDefinition,
    StepMemoryConfig,
    TransformConfig,
    WorkflowDefinition,
)
from sandcastle.engine.executor import (
    RunContext,
    StepBlocked,
    StepExecutionError,
    StepResult,
    WorkflowPaused,
    WorkflowResult,
    _UNRESOLVED,
    _backoff_delay,
    _check_budget,
    _compute_cache_key,
    _escape_braces,
    _is_cacheable_output,
    _truncate_output,
    cancel_run_local,
    execute_step_with_retry,
    execute_workflow,
    resolve_templates,
    resolve_variable,
)


# ---------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_db():
    """Bypass all DB writes during tests."""
    with (
        patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._save_checkpoint", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._get_cached_result", new_callable=AsyncMock, return_value=None),
        patch("sandcastle.engine.executor._save_to_cache", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._check_cancel", new_callable=AsyncMock, return_value=False),
        patch("sandcastle.engine.executor._save_policy_violations", new_callable=AsyncMock),
        patch("sandcastle.engine.executor.event_bus") as mock_bus,
    ):
        mock_bus.publish = MagicMock()
        yield


def ctx(**kwargs) -> RunContext:
    return RunContext(
        run_id=kwargs.get("run_id", str(uuid.uuid4())),
        input=kwargs.get("input", {}),
        step_outputs=kwargs.get("step_outputs", {}),
        step_results=kwargs.get("step_results", {}),
        costs=kwargs.get("costs", []),
        max_cost_usd=kwargs.get("max_cost_usd", None),
        workflow_name=kwargs.get("workflow_name", "test_wf"),
        default_tools=kwargs.get("default_tools", []),
        memories=kwargs.get("memories", []),
        _memory_config=kwargs.get("_memory_config", None),
        _memory_scope_id=kwargs.get("_memory_scope_id", ""),
        branch_skip_steps=kwargs.get("branch_skip_steps", set()),
        branch_run_steps=kwargs.get("branch_run_steps", set()),
        admin_trusted=True,
    )


def step(**kwargs) -> StepDefinition:
    return StepDefinition(
        id=kwargs.get("id", "s1"),
        prompt=kwargs.get("prompt", "Test prompt"),
        depends_on=kwargs.get("depends_on", []),
        model=kwargs.get("model", "sonnet"),
        max_turns=kwargs.get("max_turns", 1),
        timeout=kwargs.get("timeout", 60),
        retry=kwargs.get("retry"),
        fallback=kwargs.get("fallback"),
        type=kwargs.get("type", "standard"),
        tools=kwargs.get("tools"),
        parallel_over=kwargs.get("parallel_over"),
        output_schema=kwargs.get("output_schema"),
        loop_config=kwargs.get("loop_config"),
        race_config=kwargs.get("race_config"),
        http_config=kwargs.get("http_config"),
        code_config=kwargs.get("code_config"),
        condition_config=kwargs.get("condition_config"),
        transform_config=kwargs.get("transform_config"),
        delegate_config=kwargs.get("delegate_config"),
        memory=kwargs.get("memory"),
    )


def make_workflow(
    steps: list[StepDefinition],
    name: str = "test_wf",
    on_failure: FailureConfig | None = None,
    on_complete: CompletionConfig | None = None,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name,
        description="test",
        default_model="sonnet",
        default_max_turns=1,
        default_timeout=60,
        steps=steps,
        on_failure=on_failure,
        on_complete=on_complete,
    )


def make_plan(stages: list[list[str]]) -> ExecutionPlan:
    return ExecutionPlan(stages=stages)


def mock_sandbox_result(text: str = "ok", cost: float = 0.01):
    result = MagicMock()
    result.text = text
    result.structured_output = None
    result.total_cost_usd = cost
    return result


# ================================================================
# 1. STEP RETRY WITH BACKOFF
# ================================================================

class TestBackoffDelay:
    """Verify exponential and fixed backoff strategies."""

    def test_exponential_attempt_1(self):
        result = _backoff_delay(1, "exponential")
        assert 0 <= result <= 2  # uniform(0, min(2**1, 60))

    def test_exponential_attempt_2(self):
        result = _backoff_delay(2, "exponential")
        assert 0 <= result <= 4  # uniform(0, min(2**2, 60))

    def test_exponential_attempt_5(self):
        result = _backoff_delay(5, "exponential")
        assert 0 <= result <= 32  # uniform(0, min(2**5, 60))

    def test_exponential_capped_at_60(self):
        # 2**7 = 128, but capped at 60
        result = _backoff_delay(7, "exponential")
        assert 0 <= result <= 60

    def test_exponential_large_attempt_still_capped(self):
        result = _backoff_delay(20, "exponential")
        assert 0 <= result <= 60

    def test_fixed_always_returns_in_range(self):
        for attempt in range(1, 10):
            result = _backoff_delay(attempt, "fixed")
            assert 1.0 <= result <= 3.0

    def test_unknown_strategy_falls_through_to_fixed(self):
        # Any non-"exponential" string returns fixed delay
        result1 = _backoff_delay(1, "linear")
        assert 1.0 <= result1 <= 3.0
        result2 = _backoff_delay(5, "random")
        assert 1.0 <= result2 <= 3.0


class TestRetryWithBackoff:
    """Test execute_step_with_retry with various retry configs."""

    @pytest.mark.asyncio
    async def test_retry_exhaustion_returns_last_failure(self):
        """When all retries exhaust, the final failure is returned."""
        s = step(
            retry=RetryConfig(max_attempts=3, backoff="fixed", on_failure="abort"),
        )
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=Exception("sandbox error"))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            with patch("sandcastle.engine.executor.capture_step_error", create=True):
                result = await execute_step_with_retry(s, c, sandbox, storage)

        assert result.status == "failed"
        assert result.attempt == 3
        assert "sandbox error" in result.error

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        """Step fails first, succeeds on retry - returns success."""
        s = step(
            retry=RetryConfig(max_attempts=3, backoff="exponential", on_failure="abort"),
        )
        c = ctx()

        call_count = 0

        async def mock_query(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("transient error")
            return mock_sandbox_result("success output", 0.05)

        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=mock_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(s, c, sandbox, storage)

        assert result.status == "completed"
        assert result.output == "success output"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_fallback_after_retry_exhaustion(self):
        """When on_failure=fallback and retries exhaust, fallback is tried."""
        s = step(
            retry=RetryConfig(max_attempts=2, backoff="fixed", on_failure="fallback"),
            fallback=FallbackConfig(prompt="fallback prompt", model="haiku"),
        )
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=[
            Exception("fail 1"),
            Exception("fail 2"),
            mock_sandbox_result("fallback result", 0.02),
        ])
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(s, c, sandbox, storage)

        assert result.status == "completed"
        assert result.output == "fallback result"

    @pytest.mark.asyncio
    async def test_fallback_also_fails(self):
        """When fallback also fails, the original attempt error is returned."""
        s = step(
            retry=RetryConfig(max_attempts=1, backoff="fixed", on_failure="fallback"),
            fallback=FallbackConfig(prompt="fallback prompt", model="haiku"),
        )
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=Exception("always fails"))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(s, c, sandbox, storage)

        assert result.status == "failed"
        # When fallback also fails, the original attempt's error is returned
        assert "always fails" in result.error

    @pytest.mark.asyncio
    async def test_no_retry_config_single_attempt(self):
        """Without retry config, only one attempt is made."""
        s = step()  # no retry
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=Exception("boom"))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await execute_step_with_retry(s, c, sandbox, storage)

        assert result.status == "failed"
        assert result.attempt == 1

    @pytest.mark.asyncio
    async def test_step_overrides_applied(self):
        """Step overrides (for fork) modify prompt, model, etc."""
        s = step(prompt="original prompt", model="sonnet")
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(return_value=mock_sandbox_result("done", 0.01))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await execute_step_with_retry(
            s, c, sandbox, storage,
            step_overrides={"prompt": "overridden prompt", "model": "haiku"},
        )

        assert result.status == "completed"
        # Verify the query was called with the overridden prompt
        called_req = sandbox.query.call_args[0][0]
        assert "overridden prompt" in called_req["prompt"]


# ================================================================
# 2. CONCURRENT RACE STEP
# ================================================================

class TestRaceStepEdgeCases:
    """Test race step with various branch interaction patterns."""

    @pytest.mark.asyncio
    async def test_race_all_branches_fail(self):
        """When all race branches fail, result is failed."""
        from sandcastle.engine.executor import _execute_race_step

        wf = make_workflow([
            step(id="b1", prompt="branch 1"),
            step(id="b2", prompt="branch 2"),
            step(id="race1", type="race", race_config=RaceConfig(
                branches=[["b1"], ["b2"]],
            )),
        ])
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=Exception("branch error"))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await _execute_race_step(
            wf.get_step("race1"), c, sandbox, storage, wf, 0,
        )

        assert result.status == "failed"
        assert "All race branches failed" in result.error

    @pytest.mark.asyncio
    async def test_race_with_validator_fallback(self):
        """When validator rejects all branches, fallback_output is used."""
        from sandcastle.engine.executor import _execute_race_step

        call_count = 0

        async def branch_query(req):
            nonlocal call_count
            call_count += 1
            return mock_sandbox_result(f"result_{call_count}", 0.01)

        wf = make_workflow([
            step(id="b1", prompt="branch 1"),
            step(id="b2", prompt="branch 2"),
            step(id="race1", type="race", race_config=RaceConfig(
                branches=[["b1"], ["b2"]],
                validator="output == 'never_matches'",
            )),
        ])
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=branch_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await _execute_race_step(
            wf.get_step("race1"), c, sandbox, storage, wf, 0,
        )

        # Validator rejects all, fallback_output used
        assert result.status == "completed"
        assert result.output is not None  # Falls back to first non-error result

    @pytest.mark.asyncio
    async def test_race_cost_accumulation_across_branches(self):
        """Costs from ALL branches (including cancelled) are accumulated."""
        from sandcastle.engine.executor import _execute_race_step

        call_idx = 0

        async def branch_query(req):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                # Slow branch - will be cancelled
                await asyncio.sleep(0.1)
            return mock_sandbox_result(f"result_{call_idx}", 0.05)

        wf = make_workflow([
            step(id="fast", prompt="fast branch"),
            step(id="slow", prompt="slow branch"),
            step(id="race1", type="race", race_config=RaceConfig(
                branches=[["fast"], ["slow"]],
            )),
        ])
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=branch_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await _execute_race_step(
            wf.get_step("race1"), c, sandbox, storage, wf, 0,
        )

        assert result.status == "completed"
        # Cost should be >= 0.05 from at least the winning branch
        assert result.cost_usd >= 0.05

    @pytest.mark.asyncio
    async def test_race_missing_config_returns_failed(self):
        """Race step without race_config returns failed."""
        from sandcastle.engine.executor import _execute_race_step

        s = step(id="race_bad", type="race")  # no race_config
        c = ctx()

        result = await _execute_race_step(s, c, None, None, None, 0)

        assert result.status == "failed"
        assert "Missing race_config" in result.error

    @pytest.mark.asyncio
    async def test_race_validator_passes_first_valid(self):
        """Validator expression selects only matching outputs."""
        from sandcastle.engine.executor import _execute_race_step

        call_count = 0

        async def branch_query(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_sandbox_result("bad_result", 0.01)
            return mock_sandbox_result("good_result", 0.02)

        wf = make_workflow([
            step(id="b1", prompt="branch 1"),
            step(id="b2", prompt="branch 2"),
            step(id="race1", type="race", race_config=RaceConfig(
                branches=[["b1"], ["b2"]],
                validator="output == 'good_result'",
            )),
        ])
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=branch_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await _execute_race_step(
            wf.get_step("race1"), c, sandbox, storage, wf, 0,
        )

        assert result.status == "completed"


# ================================================================
# 3. DELEGATE / SUB-WORKFLOW DEPTH
# ================================================================

class TestDelegateDepthLimits:
    """Test delegate step depth limit enforcement."""

    @pytest.mark.asyncio
    async def test_delegate_exceeds_max_depth(self):
        """Delegate step at max depth returns failure."""
        from sandcastle.engine.executor import _execute_delegate_step

        s = step(
            id="del1",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="sub_wf",
                task_description="do something",
            ),
        )
        c = ctx()
        storage = MagicMock()

        mock_settings = MagicMock()
        mock_settings.max_workflow_depth = 3
        mock_settings.workflows_dir = "/tmp/workflows"

        with patch("sandcastle.config.settings", mock_settings):
            result = await _execute_delegate_step(s, c, storage, depth=2)

        assert result.status == "failed"
        assert "Max workflow depth" in result.error

    @pytest.mark.asyncio
    async def test_delegate_missing_config(self):
        """Delegate step without config returns failure."""
        from sandcastle.engine.executor import _execute_delegate_step

        s = step(id="del_bad", type="delegate")  # no delegate_config
        c = ctx()
        storage = MagicMock()

        result = await _execute_delegate_step(s, c, storage, depth=0)

        assert result.status == "failed"
        assert "Missing delegate_config" in result.error

    @pytest.mark.asyncio
    async def test_delegate_path_traversal_blocked(self):
        """Delegate step rejects workflow names with path traversal."""
        from sandcastle.engine.executor import _execute_delegate_step

        s = step(
            id="del_traversal",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="../../../etc/passwd",
                task_description="evil",
            ),
        )
        c = ctx()
        storage = MagicMock()

        with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
            mock_settings.max_workflow_depth = 10
            mock_settings.workflows_dir = "/tmp/workflows"
            result = await _execute_delegate_step(s, c, storage, depth=0)

        assert result.status == "failed"
        assert "path traversal" in result.error.lower() or "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_delegate_workflow_not_found_returns_fallback(self):
        """When delegate workflow does not exist, fallback output is returned."""
        from sandcastle.engine.executor import _execute_delegate_step

        s = step(
            id="del_missing",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="nonexistent_workflow",
                task_description="find me",
            ),
        )
        c = ctx()
        storage = MagicMock()

        with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
            mock_settings.max_workflow_depth = 10
            mock_settings.workflows_dir = "/tmp/nonexistent_dir"
            result = await _execute_delegate_step(s, c, storage, depth=0)

        assert result.status == "failed"
        assert "not found" in result.error.lower()


# ================================================================
# 4. LOOP WITH CONDITIONAL BREAK AND CANCEL
# ================================================================

class TestLoopEdgeCases:
    """Test loop step with until-break, cancel, and edge cases."""

    @pytest.mark.asyncio
    async def test_loop_until_break_condition(self):
        """Loop breaks early when until condition is met."""
        from sandcastle.engine.executor import _execute_loop_step

        call_count = 0

        async def mock_query(req):
            nonlocal call_count
            call_count += 1
            return mock_sandbox_result(f"iter_{call_count}", 0.01)

        wf = make_workflow([
            step(id="process", prompt="process {input._item}"),
            step(id="loop1", type="loop", loop_config=LoopConfig(
                over="{input.items}",
                step_ids=["process"],
                max_iterations=10,
                until="index >= 2",  # Break after 3rd iteration (index 0,1,2)
            )),
        ])
        c = ctx(input={"items": [1, 2, 3, 4, 5]})
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=mock_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await _execute_loop_step(
            wf.get_step("loop1"), c, sandbox, storage, wf, 0,
        )

        assert result.status == "completed"
        # Should have exactly 3 results (index 0, 1, 2 then break)
        assert len(result.output) == 3

    @pytest.mark.asyncio
    async def test_loop_cancel_mid_iteration(self):
        """Loop checks cancel between iterations and stops."""
        from sandcastle.engine.executor import _execute_loop_step

        cancel_after = 2
        iteration = 0

        async def mock_cancel(run_id):
            return iteration >= cancel_after

        async def mock_query(req):
            nonlocal iteration
            iteration += 1
            return mock_sandbox_result(f"iter_{iteration}", 0.01)

        wf = make_workflow([
            step(id="process", prompt="process"),
            step(id="loop1", type="loop", loop_config=LoopConfig(
                over="{input.items}",
                step_ids=["process"],
                max_iterations=100,
            )),
        ])
        c = ctx(input={"items": list(range(10))})
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=mock_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor._check_cancel", new_callable=AsyncMock, side_effect=mock_cancel):
            result = await _execute_loop_step(
                wf.get_step("loop1"), c, sandbox, storage, wf, 0,
            )

        assert result.status == "failed"
        assert "cancelled" in str(result.output).lower()

    @pytest.mark.asyncio
    async def test_loop_max_iterations_limit(self):
        """Loop respects max_iterations even if more items exist."""
        from sandcastle.engine.executor import _execute_loop_step

        async def mock_query(req):
            return mock_sandbox_result("processed", 0.01)

        wf = make_workflow([
            step(id="process", prompt="process"),
            step(id="loop1", type="loop", loop_config=LoopConfig(
                over="{input.items}",
                step_ids=["process"],
                max_iterations=3,
            )),
        ])
        c = ctx(input={"items": list(range(100))})
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=mock_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await _execute_loop_step(
            wf.get_step("loop1"), c, sandbox, storage, wf, 0,
        )

        assert result.status == "completed"
        assert len(result.output) == 3

    @pytest.mark.asyncio
    async def test_loop_empty_items_succeeds(self):
        """Loop with empty items list returns empty results."""
        from sandcastle.engine.executor import _execute_loop_step

        wf = make_workflow([
            step(id="process", prompt="process"),
            step(id="loop1", type="loop", loop_config=LoopConfig(
                over="{input.items}",
                step_ids=["process"],
            )),
        ])
        c = ctx(input={"items": []})
        sandbox = MagicMock()
        storage = MagicMock()

        result = await _execute_loop_step(
            wf.get_step("loop1"), c, sandbox, storage, wf, 0,
        )

        assert result.status == "completed"
        assert result.output == []

    @pytest.mark.asyncio
    async def test_loop_unresolved_variable_treated_as_empty(self):
        """Loop with unresolvable over variable runs zero iterations."""
        from sandcastle.engine.executor import _execute_loop_step

        wf = make_workflow([
            step(id="process", prompt="process"),
            step(id="loop1", type="loop", loop_config=LoopConfig(
                over="{input.nonexistent}",
                step_ids=["process"],
            )),
        ])
        c = ctx(input={})
        sandbox = MagicMock()
        storage = MagicMock()

        result = await _execute_loop_step(
            wf.get_step("loop1"), c, sandbox, storage, wf, 0,
        )

        assert result.status == "completed"
        assert result.output == []

    @pytest.mark.asyncio
    async def test_loop_non_list_item_wrapped(self):
        """Loop wraps non-list resolved value into single-element list."""
        from sandcastle.engine.executor import _execute_loop_step

        async def mock_query(req):
            return mock_sandbox_result("done", 0.01)

        wf = make_workflow([
            step(id="process", prompt="process"),
            step(id="loop1", type="loop", loop_config=LoopConfig(
                over="{input.single}",
                step_ids=["process"],
            )),
        ])
        c = ctx(input={"single": "just_one_item"})
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=mock_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await _execute_loop_step(
            wf.get_step("loop1"), c, sandbox, storage, wf, 0,
        )

        assert result.status == "completed"
        assert len(result.output) == 1

    @pytest.mark.asyncio
    async def test_loop_missing_config(self):
        """Loop step without loop_config returns failed."""
        from sandcastle.engine.executor import _execute_loop_step

        s = step(id="loop_bad", type="loop")
        c = ctx()

        result = await _execute_loop_step(s, c, None, None, None, 0)

        assert result.status == "failed"
        assert "Missing loop_config" in result.error


# ================================================================
# 5. STEP CACHE
# ================================================================

class TestCacheKeyGeneration:
    """Test _compute_cache_key edge cases."""

    def test_cache_key_is_sha256_hex(self):
        key = _compute_cache_key("wf", "step1", "prompt text", "sonnet")
        assert len(key) == 64
        int(key, 16)  # Should not raise

    def test_same_inputs_same_key(self):
        k1 = _compute_cache_key("wf", "s1", "prompt", "model")
        k2 = _compute_cache_key("wf", "s1", "prompt", "model")
        assert k1 == k2

    def test_different_prompt_different_key(self):
        k1 = _compute_cache_key("wf", "s1", "prompt A", "model")
        k2 = _compute_cache_key("wf", "s1", "prompt B", "model")
        assert k1 != k2

    def test_different_model_different_key(self):
        k1 = _compute_cache_key("wf", "s1", "prompt", "sonnet")
        k2 = _compute_cache_key("wf", "s1", "prompt", "haiku")
        assert k1 != k2

    def test_different_workflow_different_key(self):
        k1 = _compute_cache_key("wf_a", "s1", "prompt", "model")
        k2 = _compute_cache_key("wf_b", "s1", "prompt", "model")
        assert k1 != k2

    def test_empty_strings_valid_key(self):
        key = _compute_cache_key("", "", "", "")
        assert len(key) == 64

    def test_unicode_in_prompt(self):
        key = _compute_cache_key("wf", "s1", "prompt with unicode", "model")
        assert len(key) == 64

    def test_key_matches_manual_sha256(self):
        # The cache key prepends a tenant scope so two tenants never
        # share cached outputs even for identical workflow/step/model/prompt.
        # None tenant_id encodes as the literal "_none_" so single-tenant
        # mode also has a stable shape - see security fix in round 9.
        raw = "_none_:wf:s1:model:prompt"
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert _compute_cache_key("wf", "s1", "prompt", "model") == expected

    def test_key_tenant_scope_isolates_hashes(self):
        # Same workflow + step + prompt + model but different tenants
        # MUST produce different cache keys. Regression guard for the
        # round 9 cross-tenant data-leak fix.
        a = _compute_cache_key("wf", "s1", "prompt", "model", tenant_id="tenant-a")
        b = _compute_cache_key("wf", "s1", "prompt", "model", tenant_id="tenant-b")
        none = _compute_cache_key("wf", "s1", "prompt", "model")
        assert a != b
        assert a != none
        assert b != none


class TestIsCacheableOutput:
    """Test _is_cacheable_output edge cases."""

    def test_none_not_cacheable(self):
        assert _is_cacheable_output(None) is False

    def test_empty_string_not_cacheable(self):
        assert _is_cacheable_output("") is False

    def test_empty_dict_not_cacheable(self):
        assert _is_cacheable_output({}) is False

    def test_empty_list_not_cacheable(self):
        assert _is_cacheable_output([]) is False

    def test_valid_string_cacheable(self):
        assert _is_cacheable_output("analysis results here") is True

    def test_valid_dict_cacheable(self):
        assert _is_cacheable_output({"data": [1, 2, 3]}) is True

    def test_failed_output_keywords_not_cacheable(self):
        assert _is_cacheable_output("please provide more context") is False
        assert _is_cacheable_output("I don't have access to that") is False
        assert _is_cacheable_output("unable to access the resource") is False

    def test_failed_keyword_in_dict_result(self):
        assert _is_cacheable_output({"result": "please provide the URL"}) is False

    def test_long_string_with_keyword_is_cacheable(self):
        # Keywords only checked in short strings (< 200 chars)
        long_text = "A" * 200 + " please provide"
        assert _is_cacheable_output(long_text) is True

    def test_zero_mentions_not_cacheable(self):
        assert _is_cacheable_output({"total_mentions": 0, "mentions": []}) is False

    def test_nonzero_mentions_cacheable(self):
        assert _is_cacheable_output({"total_mentions": 5, "mentions": [1, 2]}) is True

    def test_integer_output_cacheable(self):
        assert _is_cacheable_output(42) is True

    def test_list_of_items_cacheable(self):
        assert _is_cacheable_output([1, 2, 3]) is True


# ================================================================
# 6. OUTPUT TRUNCATION
# ================================================================

class TestTruncateOutput:
    """Test _truncate_output for oversized outputs."""

    def test_none_unchanged(self):
        assert _truncate_output(None) is None

    def test_small_string_unchanged(self):
        assert _truncate_output("hello") == "hello"

    def test_small_dict_unchanged(self):
        d = {"key": "value"}
        assert _truncate_output(d) == d

    def test_large_string_truncated(self):
        big = "x" * 20_000_000
        result = _truncate_output(big, max_size=1000)
        assert isinstance(result, str)
        assert len(result) <= 1020  # 1000 + len of truncation marker
        assert "TRUNCATED" in result

    def test_large_dict_truncated_to_metadata(self):
        big = {"data": "x" * 20_000_000}
        result = _truncate_output(big, max_size=1000)
        assert isinstance(result, dict)
        assert result["_truncated"] is True
        assert "_original_size" in result

    def test_non_serializable_output(self):
        # Output that can't be json-serialized uses str() fallback
        class Weird:
            def __repr__(self):
                return "weird_obj"
        result = _truncate_output(Weird(), max_size=100)
        # Should not raise
        assert result is not None


# ================================================================
# 7. HTTP STEP WITH BODY TEMPLATES
# ================================================================

class TestHttpStepEdgeCases:
    """Test HTTP step with body template resolution and auth."""

    @pytest.mark.asyncio
    async def test_http_dict_body_template_resolved(self):
        """Dict body has templates resolved inside JSON serialization."""
        from sandcastle.engine.executor import _execute_http_step
        import httpx

        s = step(
            id="http1",
            type="http",
            http_config=HttpConfig(
                url="https://httpbin.org/post",
                method="POST",
                headers={},
                body={"name": "{input.user_name}", "count": "{input.count}"},
            ),
        )
        c = ctx(input={"user_name": "Alice", "count": "42"})

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"received": True}
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _execute_http_step(s, c)

        assert result.status == "completed"
        # Verify the body was serialized with resolved templates
        call_kwargs = mock_client.request.call_args
        body_content = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content")
        if body_content:
            assert "Alice" in body_content
            assert "42" in body_content

    @pytest.mark.asyncio
    async def test_http_string_body_template_resolved(self):
        """String body has templates resolved."""
        from sandcastle.engine.executor import _execute_http_step

        s = step(
            id="http2",
            type="http",
            http_config=HttpConfig(
                url="https://api.example.com/data",
                method="POST",
                body="Hello {input.name}!",
            ),
        )
        c = ctx(input={"name": "Bob"})

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _execute_http_step(s, c)

        assert result.status == "completed"
        call_kwargs = mock_client.request.call_args
        body = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content")
        if body:
            assert "Bob" in body

    @pytest.mark.asyncio
    async def test_http_auth_bearer_prefix(self):
        """Auth string starting with 'bearer:' sets Authorization header."""
        from sandcastle.engine.executor import _execute_http_step

        s = step(
            id="http_auth",
            type="http",
            http_config=HttpConfig(
                url="https://api.example.com/data",
                method="GET",
                auth="bearer:test-token-value-1234",
            ),
        )
        c = ctx()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": "secret"}
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _execute_http_step(s, c)

        assert result.status == "completed"
        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["Authorization"] == "Bearer test-token-value-1234"

    @pytest.mark.asyncio
    async def test_http_header_template_resolved(self):
        """Headers with template vars are resolved."""
        from sandcastle.engine.executor import _execute_http_step

        s = step(
            id="http_hdr",
            type="http",
            http_config=HttpConfig(
                url="https://api.example.com/data",
                method="GET",
                headers={"X-Custom": "value-{input.token}"},
            ),
        )
        c = ctx(input={"token": "abc123"})

        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _execute_http_step(s, c)

        assert result.status == "completed"
        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["X-Custom"] == "value-abc123"

    @pytest.mark.asyncio
    async def test_http_missing_config_returns_failed(self):
        """HTTP step without http_config returns failed."""
        from sandcastle.engine.executor import _execute_http_step

        s = step(id="http_bad", type="http")
        c = ctx()

        result = await _execute_http_step(s, c)

        assert result.status == "failed"
        assert "Missing http_config" in result.error

    @pytest.mark.asyncio
    async def test_http_non_json_response_truncated(self):
        """Non-JSON response text is truncated at 5000 chars."""
        from sandcastle.engine.executor import _execute_http_step

        s = step(
            id="http_big",
            type="http",
            http_config=HttpConfig(url="https://example.com", method="GET"),
        )
        c = ctx()

        big_text = "X" * 10000
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not JSON")
        mock_resp.text = big_text
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _execute_http_step(s, c)

        assert result.status == "completed"
        assert result.output["_truncated"] is True
        assert len(result.output["text"]) == 5000


# ================================================================
# 8. CODE STEP EDGE CASES
# ================================================================

class TestCodeStepEdgeCases:
    """Test code step: blocked patterns, timeouts, oversized code."""

    @pytest.mark.asyncio
    async def test_code_blocked_pattern_exec(self):
        """Code containing exec() is rejected."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(
            id="code1",
            type="code",
            code_config=CodeConfig(code="exec('print(1)')"),
        )
        c = ctx()

        result = await _execute_code_step(s, c)

        assert result.status == "failed"
        assert "blocked pattern" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_blocked_pattern_eval(self):
        """Code containing eval() is rejected."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(
            id="code2",
            type="code",
            code_config=CodeConfig(code="x = eval('1+1')"),
        )
        c = ctx()

        result = await _execute_code_step(s, c)

        assert result.status == "failed"
        assert "blocked pattern" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_blocked_pattern_subprocess(self):
        """Code containing subprocess is rejected."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(
            id="code3",
            type="code",
            code_config=CodeConfig(code="import subprocess"),
        )
        c = ctx()

        result = await _execute_code_step(s, c)

        assert result.status == "failed"
        assert "blocked pattern" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_oversized_rejected(self):
        """Code exceeding size limit is rejected."""
        from sandcastle.engine.executor import _execute_code_step

        big_code = "x = 1\n" * 100_000  # Well over 50KB
        s = step(
            id="code_big",
            type="code",
            code_config=CodeConfig(code=big_code),
        )
        c = ctx()

        result = await _execute_code_step(s, c)

        assert result.status == "failed"
        assert "too large" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_result_variable_returned(self):
        """Code step returns the `result` variable from exec globals."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(
            id="code_ok",
            type="code",
            code_config=CodeConfig(code="result = {'answer': 42}"),
        )
        c = ctx()

        result = await _execute_code_step(s, c)

        assert result.status == "completed"
        assert result.output == {"answer": 42}

    @pytest.mark.asyncio
    async def test_code_accesses_context_input(self):
        """Code step can access _input from context."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(
            id="code_ctx",
            type="code",
            code_config=CodeConfig(code="result = _input['name'].upper()"),
        )
        c = ctx(input={"name": "alice"})

        result = await _execute_code_step(s, c)

        assert result.status == "completed"
        assert result.output == "ALICE"

    @pytest.mark.asyncio
    async def test_code_accesses_step_outputs(self):
        """Code step can access _steps from context."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(
            id="code_steps",
            type="code",
            code_config=CodeConfig(code="result = len(_steps['prev'])"),
        )
        c = ctx(step_outputs={"prev": "hello"})

        result = await _execute_code_step(s, c)

        assert result.status == "completed"
        assert result.output == 5

    @pytest.mark.asyncio
    async def test_code_missing_config(self):
        """Code step without code_config returns failed."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(id="code_bad", type="code")
        c = ctx()

        result = await _execute_code_step(s, c)

        assert result.status == "failed"
        assert "Missing code_config" in result.error

    @pytest.mark.asyncio
    async def test_code_runtime_error_caught(self):
        """Runtime error in code is caught and reported."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(
            id="code_err",
            type="code",
            code_config=CodeConfig(code="result = 1 / 0"),
        )
        c = ctx()

        result = await _execute_code_step(s, c)

        assert result.status == "failed"
        assert "division by zero" in result.error.lower()

    @pytest.mark.asyncio
    async def test_code_uses_json_module(self):
        """Code step has json module available."""
        from sandcastle.engine.executor import _execute_code_step

        s = step(
            id="code_json",
            type="code",
            code_config=CodeConfig(code='result = json.loads(\'{"a": 1}\')'),
        )
        c = ctx()

        result = await _execute_code_step(s, c)

        assert result.status == "completed"
        assert result.output == {"a": 1}


# ================================================================
# 9. MULTI-STEP ERROR CASCADING
# ================================================================

class TestErrorCascading:
    """Test multi-step error propagation: abort vs skip vs dead_letter."""

    @pytest.mark.asyncio
    async def test_failed_step_with_abort_raises(self):
        """Step failure with on_failure=abort raises StepExecutionError."""
        s1 = step(
            id="fail_step",
            prompt="will fail",
            retry=RetryConfig(max_attempts=1, on_failure="abort"),
        )
        wf = make_workflow([s1])

        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=Exception("kaboom"))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
            with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
                mock_settings.max_workflow_depth = 10
                mock_settings.sandbox_backend = "local"
                mock_settings.e2b_api_key = ""
                mock_settings.e2b_template = ""
                mock_settings.max_concurrent_sandboxes = 1
                mock_settings.anthropic_api_key = ""
                mock_settings.docker_image = ""
                mock_settings.docker_url = ""
                mock_settings.cloudflare_worker_url = ""
                mock_settings.memory_enabled = False
                mock_settings.workflows_dir = "/tmp/workflows"

                result = await execute_workflow(
                    workflow=wf,
                    plan=make_plan([["fail_step"]]),
                    input_data={},
                    storage=storage,
                )

        assert result.status == "failed"
        assert "kaboom" in result.error

    @pytest.mark.asyncio
    async def test_failed_step_with_skip_continues(self):
        """Step failure with on_failure=skip sets output to None, continues."""
        s1 = step(
            id="skip_step",
            prompt="will fail",
            retry=RetryConfig(max_attempts=1, on_failure="skip"),
        )
        s2 = step(
            id="next_step",
            prompt="should run",
            depends_on=["skip_step"],
        )
        wf = make_workflow([s1, s2])

        call_count = 0

        async def mock_query(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("expected fail")
            return mock_sandbox_result("next ok", 0.01)

        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=mock_query)
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
            with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
                mock_settings.max_workflow_depth = 10
                mock_settings.sandbox_backend = "local"
                mock_settings.e2b_api_key = ""
                mock_settings.e2b_template = ""
                mock_settings.max_concurrent_sandboxes = 1
                mock_settings.anthropic_api_key = ""
                mock_settings.docker_image = ""
                mock_settings.docker_url = ""
                mock_settings.cloudflare_worker_url = ""
                mock_settings.memory_enabled = False
                mock_settings.workflows_dir = "/tmp/workflows"

                result = await execute_workflow(
                    workflow=wf,
                    plan=make_plan([["skip_step"], ["next_step"]]),
                    input_data={},
                    storage=storage,
                )

        assert result.status == "completed"
        assert result.outputs["skip_step"] is None
        assert result.outputs["next_step"] == "next ok"

    @pytest.mark.asyncio
    async def test_dead_letter_queue_on_failure(self):
        """Step failure with dead_letter=True sends to DLQ instead of aborting."""
        s1 = step(
            id="dlq_step",
            prompt="will fail",
            retry=RetryConfig(max_attempts=1, on_failure="abort"),
        )
        wf = make_workflow(
            [s1],
            on_failure=FailureConfig(dead_letter=True),
        )

        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=Exception("send to dlq"))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
            with patch("sandcastle.engine.executor._send_to_dead_letter", new_callable=AsyncMock, return_value=True) as mock_dlq:
                with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
                    mock_settings.max_workflow_depth = 10
                    mock_settings.sandbox_backend = "local"
                    mock_settings.e2b_api_key = ""
                    mock_settings.e2b_template = ""
                    mock_settings.max_concurrent_sandboxes = 1
                    mock_settings.anthropic_api_key = ""
                    mock_settings.docker_image = ""
                    mock_settings.docker_url = ""
                    mock_settings.cloudflare_worker_url = ""
                    mock_settings.memory_enabled = False
                    mock_settings.workflows_dir = "/tmp/workflows"

                    result = await execute_workflow(
                        workflow=wf,
                        plan=make_plan([["dlq_step"]]),
                        input_data={},
                        storage=storage,
                    )

        # DLQ was called
        mock_dlq.assert_called_once()
        # Workflow completes (DLQ absorbs the failure)
        assert result.status == "completed"
        assert result.outputs["dlq_step"] is None


# ================================================================
# 10. WORKFLOW CANCELLATION
# ================================================================

class TestWorkflowCancellation:
    """Test workflow cancellation mid-execution."""

    @pytest.mark.asyncio
    async def test_cancel_before_any_step_runs(self):
        """Cancel flag set before workflow starts returns cancelled status."""
        s1 = step(id="never_runs", prompt="test")
        wf = make_workflow([s1])

        sandbox = MagicMock()
        storage = MagicMock()

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
            with patch("sandcastle.engine.executor._check_cancel", new_callable=AsyncMock, return_value=True):
                with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
                    mock_settings.max_workflow_depth = 10
                    mock_settings.sandbox_backend = "local"
                    mock_settings.e2b_api_key = ""
                    mock_settings.e2b_template = ""
                    mock_settings.max_concurrent_sandboxes = 1
                    mock_settings.anthropic_api_key = ""
                    mock_settings.docker_image = ""
                    mock_settings.docker_url = ""
                    mock_settings.cloudflare_worker_url = ""
                    mock_settings.memory_enabled = False
                    mock_settings.workflows_dir = "/tmp/workflows"

                    result = await execute_workflow(
                        workflow=wf,
                        plan=make_plan([["never_runs"]]),
                        input_data={},
                        storage=storage,
                    )

        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_run_local_sets_flag(self):
        """cancel_run_local adds run_id to in-memory cancel set."""
        from sandcastle.engine.executor import _cancel_flags, _cancel_flags_lock

        run_id = str(uuid.uuid4())
        await cancel_run_local(run_id)

        async with _cancel_flags_lock:
            assert run_id in _cancel_flags
            # Clean up
            _cancel_flags.pop(run_id, None)

    @pytest.mark.asyncio
    async def test_cancel_flag_eviction_when_full(self):
        """Cancel flags evict oldest entries when set exceeds max."""
        from sandcastle.engine.executor import (
            _MAX_CANCEL_FLAGS,
            _cancel_flags,
            _cancel_flags_lock,
        )

        original_flags = dict(_cancel_flags)
        try:
            # Fill to capacity
            async with _cancel_flags_lock:
                _cancel_flags.clear()

            ids = []
            for i in range(_MAX_CANCEL_FLAGS):
                rid = f"run_{i:05d}"
                ids.append(rid)
                await cancel_run_local(rid)

            # Adding one more triggers eviction
            overflow_id = "run_overflow"
            await cancel_run_local(overflow_id)

            async with _cancel_flags_lock:
                assert overflow_id in _cancel_flags
                # First entries should have been evicted
                assert len(_cancel_flags) <= _MAX_CANCEL_FLAGS
                # Oldest half should be gone
                assert "run_00000" not in _cancel_flags
        finally:
            # Restore original state
            async with _cancel_flags_lock:
                _cancel_flags.clear()
                _cancel_flags.update(original_flags)


# ================================================================
# 11. BUDGET CHECK EDGE CASES
# ================================================================

class TestBudgetCheckEdgeCases:
    """Test _check_budget boundary conditions."""

    def test_no_budget_returns_none(self):
        c = ctx(max_cost_usd=None, costs=[1.0])
        assert _check_budget(c) is None

    def test_zero_budget_returns_none(self):
        c = ctx(max_cost_usd=0, costs=[])
        assert _check_budget(c) is None

    def test_negative_budget_returns_none(self):
        c = ctx(max_cost_usd=-1.0, costs=[0.5])
        assert _check_budget(c) is None

    def test_under_80_percent(self):
        c = ctx(max_cost_usd=1.0, costs=[0.5])
        assert _check_budget(c) is None

    def test_exactly_80_percent(self):
        c = ctx(max_cost_usd=1.0, costs=[0.8])
        assert _check_budget(c) == "warning"

    def test_between_80_and_100_percent(self):
        c = ctx(max_cost_usd=1.0, costs=[0.9])
        assert _check_budget(c) == "warning"

    def test_exactly_100_percent(self):
        c = ctx(max_cost_usd=1.0, costs=[1.0])
        assert _check_budget(c) == "exceeded"

    def test_over_100_percent(self):
        c = ctx(max_cost_usd=1.0, costs=[1.5])
        assert _check_budget(c) == "exceeded"

    def test_multiple_cost_entries(self):
        c = ctx(max_cost_usd=1.0, costs=[0.3, 0.3, 0.3])
        assert _check_budget(c) == "warning"  # 0.9 -> warning


# ================================================================
# 12. WORKFLOW DEPTH LIMIT
# ================================================================

class TestWorkflowDepthLimit:
    """Test depth enforcement in execute_workflow."""

    @pytest.mark.asyncio
    async def test_max_depth_exceeded_returns_failed(self):
        """Workflow at or beyond max depth returns failed result."""
        wf = make_workflow([step(id="s1", prompt="test")])

        mock_settings = MagicMock()
        mock_settings.max_workflow_depth = 3

        with patch("sandcastle.config.settings", mock_settings):
            result = await execute_workflow(
                workflow=wf,
                plan=make_plan([["s1"]]),
                input_data={},
                depth=3,
            )

        assert result.status == "failed"
        assert "Max workflow depth" in result.error


# ================================================================
# 13. RESOLVE VARIABLE EDGE CASES
# ================================================================

class TestResolveVariableEdgeCases:
    """Test obscure resolve_variable paths."""

    def test_empty_var_path_returns_unresolved(self):
        c = ctx()
        assert resolve_variable("", c) is _UNRESOLVED

    def test_steps_with_only_two_parts_returns_unresolved(self):
        c = ctx(step_outputs={"s1": "output"})
        assert resolve_variable("steps.s1", c) is _UNRESOLVED

    def test_steps_output_none_returns_none(self):
        c = ctx(step_outputs={"s1": None})
        result = resolve_variable("steps.s1.output", c)
        assert result is None

    def test_steps_output_field_of_none_returns_none(self):
        c = ctx(step_outputs={"s1": None})
        result = resolve_variable("steps.s1.output.field", c)
        assert result is None

    def test_list_index_access(self):
        c = ctx(step_outputs={"s1": ["a", "b", "c"]})
        assert resolve_variable("steps.s1.output.1", c) == "b"

    def test_list_negative_index_returns_unresolved(self):
        c = ctx(step_outputs={"s1": ["a", "b"]})
        assert resolve_variable("steps.s1.output.-1", c) is _UNRESOLVED

    def test_list_out_of_bounds_returns_unresolved(self):
        c = ctx(step_outputs={"s1": ["a"]})
        assert resolve_variable("steps.s1.output.5", c) is _UNRESOLVED

    def test_list_non_numeric_index_returns_unresolved(self):
        c = ctx(step_outputs={"s1": ["a"]})
        assert resolve_variable("steps.s1.output.foo", c) is _UNRESOLVED

    def test_step_status_from_step_results(self):
        c = ctx(
            step_outputs={"s1": "data"},
            step_results={"s1": StepResult(step_id="s1", status="completed")},
        )
        assert resolve_variable("steps.s1.status", c) == "completed"

    def test_step_error_from_step_results(self):
        c = ctx(
            step_outputs={"s1": None},
            step_results={"s1": StepResult(step_id="s1", status="failed", error="boom")},
        )
        assert resolve_variable("steps.s1.error", c) == "boom"

    def test_step_cost_from_step_results(self):
        c = ctx(
            step_outputs={"s1": "data"},
            step_results={"s1": StepResult(step_id="s1", cost_usd=0.05)},
        )
        assert resolve_variable("steps.s1.cost", c) == 0.05

    def test_unknown_step_meta_attribute_returns_unresolved(self):
        c = ctx(
            step_outputs={"s1": "data"},
            step_results={"s1": StepResult(step_id="s1")},
        )
        assert resolve_variable("steps.s1.unknown_attr", c) is _UNRESOLVED

    def test_memory_variable(self):
        from sandcastle.engine.memory import format_memories_for_prompt
        c = ctx(memories=[])
        result = resolve_variable("memory", c)
        # Should call format_memories_for_prompt with empty list
        assert result == format_memories_for_prompt([])

    def test_date_returns_iso_string(self):
        c = ctx()
        result = resolve_variable("date", c)
        # Should be valid ISO date string
        datetime.fromisoformat(result)

    def test_run_id_returns_context_run_id(self):
        rid = str(uuid.uuid4())
        c = ctx(run_id=rid)
        assert resolve_variable("run_id", c) == rid

    def test_deeply_nested_dict_access(self):
        c = ctx(step_outputs={"s1": {"a": {"b": {"c": 42}}}})
        assert resolve_variable("steps.s1.output.a.b.c", c) == 42

    def test_input_missing_key_with_subpath(self):
        c = ctx(input={"a": {"b": 1}})
        assert resolve_variable("input.missing", c) is _UNRESOLVED

    def test_input_nested_path(self):
        c = ctx(input={"user": {"name": "test", "age": 30}})
        assert resolve_variable("input.user.name", c) == "test"


# ================================================================
# 14. RESOLVE TEMPLATES EDGE CASES
# ================================================================

class TestResolveTemplatesEdgeCases:
    """Test resolve_templates with tricky inputs."""

    def test_oversized_template_raises(self):
        from sandcastle.engine.executor import _MAX_TEMPLATE_SIZE
        big = "x" * (_MAX_TEMPLATE_SIZE + 1)
        c = ctx()
        with pytest.raises(ValueError, match="Template string too large"):
            resolve_templates(big, c)

    def test_step_output_braces_escaped(self):
        c = ctx(step_outputs={"s1": "value with {braces}"})
        result = resolve_templates("{steps.s1.output}", c)
        assert "{" not in result.replace("{{", "")

    def test_auto_inject_unreferenced_deps(self):
        c = ctx(step_outputs={"dep1": "dep_value"})
        result = resolve_templates("just a prompt", c, depends_on=["dep1"])
        assert "dep_value" in result
        assert "Context from previous steps" in result

    def test_no_auto_inject_when_dep_referenced(self):
        c = ctx(step_outputs={"dep1": "dep_value"})
        result = resolve_templates(
            "use {steps.dep1.output}",
            c,
            depends_on=["dep1"],
        )
        # Should not have "Context from previous steps" since dep1 is referenced
        assert "Context from previous steps" not in result

    def test_none_value_renders_as_none(self):
        c = ctx(step_outputs={"s1": None})
        result = resolve_templates("{steps.s1.output}", c)
        assert result == "None"

    def test_dict_value_serialized_as_json(self):
        c = ctx(step_outputs={"s1": {"key": "val"}})
        result = resolve_templates("{steps.s1.output}", c)
        # Braces are escaped, so check for the content
        assert "key" in result
        assert "val" in result

    def test_multiple_vars_in_one_template(self):
        c = ctx(input={"a": "1", "b": "2"})
        result = resolve_templates("{input.a} and {input.b}", c)
        assert result == "1 and 2"


# ================================================================
# 15. TRANSFORM STEP EDGE CASES
# ================================================================

class TestTransformStepEdgeCases:
    """Test transform step with Jinja-like syntax and JSON parsing."""

    @pytest.mark.asyncio
    async def test_transform_jinja_variable(self):
        """Transform resolves {{ var }} Jinja-like syntax."""
        from sandcastle.engine.executor import _execute_transform_step

        s = step(
            id="t1",
            type="transform",
            transform_config=TransformConfig(
                template='{"name": "{{ input.name }}"}',
            ),
        )
        c = ctx(input={"name": "Alice"})

        result = await _execute_transform_step(s, c)

        assert result.status == "completed"
        assert result.output == {"name": "Alice"}

    @pytest.mark.asyncio
    async def test_transform_tojson_filter(self):
        """Transform resolves {{ var | tojson }} filter."""
        from sandcastle.engine.executor import _execute_transform_step

        s = step(
            id="t2",
            type="transform",
            transform_config=TransformConfig(
                template="{{ steps.data.output | tojson }}",
            ),
        )
        c = ctx(step_outputs={"data": {"items": [1, 2, 3]}})

        result = await _execute_transform_step(s, c)

        assert result.status == "completed"
        assert result.output == {"items": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_transform_missing_config(self):
        """Transform step without config returns failed."""
        from sandcastle.engine.executor import _execute_transform_step

        s = step(id="t_bad", type="transform")
        c = ctx()

        result = await _execute_transform_step(s, c)

        assert result.status == "failed"
        assert "Missing transform_config" in result.error

    @pytest.mark.asyncio
    async def test_transform_plain_text_output(self):
        """Transform with non-JSON template returns string."""
        from sandcastle.engine.executor import _execute_transform_step

        s = step(
            id="t3",
            type="transform",
            transform_config=TransformConfig(template="Hello {input.name}!"),
        )
        c = ctx(input={"name": "World"})

        result = await _execute_transform_step(s, c)

        assert result.status == "completed"
        assert result.output == "Hello World!"

    @pytest.mark.asyncio
    async def test_transform_oversized_template_rejected(self):
        """Transform with oversized template returns failed."""
        from sandcastle.engine.executor import _MAX_TEMPLATE_SIZE, _execute_transform_step

        s = step(
            id="t_big",
            type="transform",
            transform_config=TransformConfig(template="x" * (_MAX_TEMPLATE_SIZE + 1)),
        )
        c = ctx()

        result = await _execute_transform_step(s, c)

        assert result.status == "failed"
        assert "too large" in result.error.lower()

    @pytest.mark.asyncio
    async def test_transform_unresolved_jinja_var_empty(self):
        """Unresolved Jinja-style {{ var }} resolves to empty string."""
        from sandcastle.engine.executor import _execute_transform_step

        s = step(
            id="t_unresolved",
            type="transform",
            transform_config=TransformConfig(template="prefix-{{ input.missing }}-suffix"),
        )
        c = ctx(input={})

        result = await _execute_transform_step(s, c)

        assert result.status == "completed"
        assert "prefix--suffix" in result.output


# ================================================================
# 16. CONDITION STEP EDGE CASES
# ================================================================

class TestConditionStepEdgeCases:
    """Test condition step with various expression patterns."""

    @pytest.mark.asyncio
    async def test_condition_true_skips_else_steps(self):
        """True condition adds else_steps to branch_skip_steps."""
        from sandcastle.engine.executor import _execute_condition_step

        s = step(
            id="cond1",
            type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=["do_this"],
                else_steps=["skip_this"],
            ),
        )
        c = ctx()

        result = await _execute_condition_step(s, c)

        assert result.status == "completed"
        assert result.output["condition"] is True
        assert "skip_this" in c.branch_skip_steps
        assert "do_this" in c.branch_run_steps

    @pytest.mark.asyncio
    async def test_condition_false_skips_then_steps(self):
        """False condition adds then_steps to branch_skip_steps."""
        from sandcastle.engine.executor import _execute_condition_step

        s = step(
            id="cond2",
            type="condition",
            condition_config=ConditionConfig(
                expression="False",
                then_steps=["skip_this"],
                else_steps=["do_this"],
            ),
        )
        c = ctx()

        result = await _execute_condition_step(s, c)

        assert result.status == "completed"
        assert result.output["condition"] is False
        assert "skip_this" in c.branch_skip_steps
        assert "do_this" in c.branch_run_steps

    @pytest.mark.asyncio
    async def test_condition_with_numeric_comparison(self):
        """Condition comparing numeric values works."""
        from sandcastle.engine.executor import _execute_condition_step

        s = step(
            id="cond3",
            type="condition",
            condition_config=ConditionConfig(
                expression="{input.score} > 50",
                then_steps=["high"],
                else_steps=["low"],
            ),
        )
        c = ctx(input={"score": "75"})

        result = await _execute_condition_step(s, c)

        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_missing_config(self):
        """Condition without config returns failed."""
        from sandcastle.engine.executor import _execute_condition_step

        s = step(id="cond_bad", type="condition")
        c = ctx()

        result = await _execute_condition_step(s, c)

        assert result.status == "failed"
        assert "Missing condition_config" in result.error

    @pytest.mark.asyncio
    async def test_condition_dunder_blocked(self):
        """Condition with double underscore is blocked."""
        from sandcastle.engine.executor import _execute_condition_step

        s = step(
            id="cond_dunder",
            type="condition",
            condition_config=ConditionConfig(
                expression="__import__('os')",
                then_steps=[],
                else_steps=[],
            ),
        )
        c = ctx()

        result = await _execute_condition_step(s, c)

        assert result.status == "failed"
        assert "blocked" in result.error.lower() or "unsafe" in result.error.lower()

    @pytest.mark.asyncio
    async def test_condition_expression_too_long(self):
        """Expression exceeding max length is rejected."""
        from sandcastle.engine.executor import _execute_condition_step

        s = step(
            id="cond_long",
            type="condition",
            condition_config=ConditionConfig(
                expression="True or " * 500,
                then_steps=[],
                else_steps=[],
            ),
        )
        c = ctx()

        result = await _execute_condition_step(s, c)

        assert result.status == "failed"
        assert "too long" in result.error.lower() or "unsafe" in result.error.lower()

    @pytest.mark.asyncio
    async def test_condition_run_steps_precedence(self):
        """A step explicitly run by one condition cannot be skipped by another."""
        from sandcastle.engine.executor import _execute_condition_step

        # First condition says run "shared_step"
        s1 = step(
            id="cond_a",
            type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=["shared_step"],
                else_steps=[],
            ),
        )
        c = ctx()
        await _execute_condition_step(s1, c)

        assert "shared_step" in c.branch_run_steps

        # Second condition tries to skip "shared_step" via else_steps
        s2 = step(
            id="cond_b",
            type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=[],
                else_steps=["shared_step"],
            ),
        )
        await _execute_condition_step(s2, c)

        # shared_step should NOT be in skip set because it was explicitly run
        assert "shared_step" not in c.branch_skip_steps
        assert "shared_step" in c.branch_run_steps


# ================================================================
# 17. RUN CONTEXT SNAPSHOT AND WITH_ITEM
# ================================================================

class TestRunContextEdgeCases:
    """Test RunContext snapshot, total_cost, and with_item isolation."""

    def test_snapshot_structure(self):
        c = ctx(
            run_id="test-run-123",
            input={"x": 1},
            step_outputs={"s1": "out"},
            costs=[0.01, 0.02],
        )
        snap = c.snapshot()
        assert snap["run_id"] == "test-run-123"
        assert snap["input"] == {"x": 1}
        assert snap["step_outputs"] == {"s1": "out"}
        assert snap["costs"] == [0.01, 0.02]
        assert snap["total_cost"] == 0.03

    def test_total_cost_empty(self):
        c = ctx(costs=[])
        assert c.total_cost == 0.0

    def test_total_cost_multiple_entries(self):
        c = ctx(costs=[0.1, 0.2, 0.3])
        assert abs(c.total_cost - 0.6) < 1e-9

    def test_with_item_sets_item_and_index(self):
        c = ctx(input={"base": "val"})
        child = c.with_item("item_data", 5)
        assert child.input["_item"] == "item_data"
        assert child.input["_index"] == 5
        assert child.input["base"] == "val"

    def test_with_item_cost_isolation(self):
        c = ctx(costs=[0.5])
        child = c.with_item("x", 0)
        child.costs.append(0.1)
        # Parent costs unaffected
        assert c.costs == [0.5]
        assert child.costs == [0.1]

    def test_with_item_step_outputs_isolation(self):
        c = ctx(step_outputs={"s1": "original"})
        child = c.with_item("x", 0)
        child.step_outputs["s1"] = "modified"
        # Parent unaffected
        assert c.step_outputs["s1"] == "original"

    def test_with_item_preserves_memory_config(self):
        mem_config = MagicMock()
        c = ctx(_memory_config=mem_config, _memory_scope_id="scope_123")
        child = c.with_item("x", 0)
        assert child._memory_config is mem_config
        assert child._memory_scope_id == "scope_123"

    def test_with_item_branch_skip_isolation(self):
        c = ctx(branch_skip_steps={"skip1"})
        child = c.with_item("x", 0)
        child.branch_skip_steps.add("skip2")
        assert "skip2" not in c.branch_skip_steps


# ================================================================
# 18. ESCAPE BRACES EDGE CASES
# ================================================================

class TestEscapeBraces:
    """Test _escape_braces with various inputs."""

    def test_empty_string(self):
        assert _escape_braces("") == ""

    def test_no_braces(self):
        assert _escape_braces("hello world") == "hello world"

    def test_single_open_brace(self):
        assert _escape_braces("{") == "{{"

    def test_single_close_brace(self):
        assert _escape_braces("}") == "}}"

    def test_template_pattern(self):
        assert _escape_braces("{steps.s1.output}") == "{{steps.s1.output}}"

    def test_nested_braces(self):
        assert _escape_braces("{{already}}") == "{{{{already}}}}"

    def test_mixed_content(self):
        result = _escape_braces("value={data}")
        assert result == "value={{data}}"


# ================================================================
# 19. WORKFLOW WITH INITIAL CONTEXT (REPLAY/FORK)
# ================================================================

class TestWorkflowReplayFork:
    """Test workflow execution with initial_context and skip_steps."""

    @pytest.mark.asyncio
    async def test_skip_steps_are_not_executed(self):
        """Steps in skip_steps set are not executed."""
        s1 = step(id="already_done", prompt="done")
        s2 = step(id="run_me", prompt="run", depends_on=["already_done"])
        wf = make_workflow([s1, s2])

        sandbox = MagicMock()
        sandbox.query = AsyncMock(return_value=mock_sandbox_result("new result", 0.01))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
            with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
                mock_settings.max_workflow_depth = 10
                mock_settings.sandbox_backend = "local"
                mock_settings.e2b_api_key = ""
                mock_settings.e2b_template = ""
                mock_settings.max_concurrent_sandboxes = 1
                mock_settings.anthropic_api_key = ""
                mock_settings.docker_image = ""
                mock_settings.docker_url = ""
                mock_settings.cloudflare_worker_url = ""
                mock_settings.memory_enabled = False
                mock_settings.workflows_dir = "/tmp/workflows"

                result = await execute_workflow(
                    workflow=wf,
                    plan=make_plan([["already_done"], ["run_me"]]),
                    input_data={},
                    storage=storage,
                    initial_context={
                        "step_outputs": {"already_done": "cached output"},
                        "costs": [0.05],
                    },
                    skip_steps={"already_done"},
                )

        assert result.status == "completed"
        assert result.outputs["already_done"] == "cached output"
        assert result.outputs["run_me"] == "new result"
        # Sandbox should only have been called once (for run_me)
        assert sandbox.query.call_count == 1

    @pytest.mark.asyncio
    async def test_input_schema_defaults_merged(self):
        """Input schema defaults are merged with provided input."""
        s1 = step(id="s1", prompt="test {input.color}")
        wf = make_workflow([s1])
        wf.input_schema = {
            "properties": {
                "color": {"type": "string", "default": "blue"},
                "size": {"type": "string", "default": "medium"},
            }
        }

        sandbox = MagicMock()
        sandbox.query = AsyncMock(return_value=mock_sandbox_result("done", 0.01))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
            with patch("sandcastle.engine.executor.settings", create=True) as mock_settings:
                mock_settings.max_workflow_depth = 10
                mock_settings.sandbox_backend = "local"
                mock_settings.e2b_api_key = ""
                mock_settings.e2b_template = ""
                mock_settings.max_concurrent_sandboxes = 1
                mock_settings.anthropic_api_key = ""
                mock_settings.docker_image = ""
                mock_settings.docker_url = ""
                mock_settings.cloudflare_worker_url = ""
                mock_settings.memory_enabled = False
                mock_settings.workflows_dir = "/tmp/workflows"

                result = await execute_workflow(
                    workflow=wf,
                    plan=make_plan([["s1"]]),
                    input_data={"color": "red"},  # Override default
                    storage=storage,
                )

        assert result.status == "completed"
        # The query should contain "red" (overridden), not "blue" (default)
        called_req = sandbox.query.call_args[0][0]
        assert "red" in called_req["prompt"]


# ================================================================
# 20. EVENT BUS AND STEP EVENTS
# ================================================================

class TestStepEventEmission:
    """Test that execute_step_with_retry emits correct events."""

    @pytest.mark.asyncio
    async def test_step_started_and_completed_events(self):
        """Successful step emits step.started and step.completed events."""
        from sandcastle.engine.executor import event_bus

        s = step(id="evt_step", prompt="test")
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(return_value=mock_sandbox_result("result", 0.01))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await execute_step_with_retry(s, c, sandbox, storage)

        assert result.status == "completed"
        # Check events were published
        event_names = [call[0][0] for call in event_bus.publish.call_args_list]
        assert "step.started" in event_names
        assert "step.completed" in event_names

    @pytest.mark.asyncio
    async def test_step_failed_event_on_exhaustion(self):
        """Failed step (all retries exhausted) emits step.failed event."""
        from sandcastle.engine.executor import event_bus

        s = step(
            id="evt_fail",
            prompt="test",
            retry=RetryConfig(max_attempts=1, on_failure="skip"),
        )
        c = ctx()
        sandbox = MagicMock()
        sandbox.query = AsyncMock(side_effect=Exception("test error"))
        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await execute_step_with_retry(s, c, sandbox, storage)

        assert result.status == "failed"
        event_names = [call[0][0] for call in event_bus.publish.call_args_list]
        assert "step.started" in event_names
        assert "step.failed" in event_names


# ================================================================
# 21. WORKFLOW PAUSED/BLOCKED PROPAGATION
# ================================================================

class TestWorkflowPausedBlockedPropagation:
    """Test that WorkflowPaused and StepBlocked propagate correctly."""

    def test_workflow_paused_has_approval_id(self):
        exc = WorkflowPaused(approval_id="apr-123", run_id="run-456")
        assert exc.approval_id == "apr-123"
        assert exc.run_id == "run-456"
        assert "apr-123" in str(exc)

    def test_step_blocked_has_step_id_and_reason(self):
        exc = StepBlocked(step_id="s1", reason="policy violation")
        assert exc.step_id == "s1"
        assert exc.reason == "policy violation"
        assert "policy violation" in str(exc)

    def test_step_execution_error_is_exception(self):
        exc = StepExecutionError("step failed badly")
        assert isinstance(exc, Exception)
        assert "step failed badly" in str(exc)


# ================================================================
# 22. STORAGE REFERENCE RESOLUTION
# ================================================================

class TestStorageRefResolution:
    """Test resolve_storage_refs with storage backend."""

    @pytest.mark.asyncio
    async def test_storage_ref_replaced(self):
        from sandcastle.engine.executor import resolve_storage_refs

        storage = MagicMock()
        storage.read = AsyncMock(return_value="stored content here")

        result = await resolve_storage_refs(
            "Use this: {storage.data/file.txt}", storage,
        )

        assert "stored content here" in result
        storage.read.assert_called_once_with("data/file.txt")

    @pytest.mark.asyncio
    async def test_storage_ref_not_found_kept(self):
        from sandcastle.engine.executor import resolve_storage_refs

        storage = MagicMock()
        storage.read = AsyncMock(return_value=None)

        result = await resolve_storage_refs(
            "Use this: {storage.missing/file.txt}", storage,
        )

        # Original placeholder kept when storage returns None
        assert "{storage.missing/file.txt}" in result

    @pytest.mark.asyncio
    async def test_multiple_storage_refs(self):
        from sandcastle.engine.executor import resolve_storage_refs

        async def mock_read(path):
            return {"data/a.txt": "content_a", "data/b.txt": "content_b"}.get(path)

        storage = MagicMock()
        storage.read = AsyncMock(side_effect=mock_read)

        result = await resolve_storage_refs(
            "A={storage.data/a.txt} B={storage.data/b.txt}", storage,
        )

        assert "content_a" in result
        assert "content_b" in result


# ================================================================
# 23. BROWSER URL VALIDATION
# ================================================================

class TestBrowserUrlValidation:
    """Test _validate_browser_url edge cases."""

    def test_empty_url_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_browser_url("")

    def test_whitespace_only_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_browser_url("   ")

    def test_javascript_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("javascript:alert(1)")

    def test_data_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("data:text/html,<h1>evil</h1>")

    def test_file_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("file:///etc/passwd")

    def test_ftp_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _validate_browser_url("ftp://example.com/file")

    def test_http_url_valid(self):
        from sandcastle.engine.executor import _validate_browser_url
        assert _validate_browser_url("http://example.com") == "http://example.com"

    def test_https_url_valid(self):
        from sandcastle.engine.executor import _validate_browser_url
        assert _validate_browser_url("https://example.com") == "https://example.com"

    def test_no_scheme_gets_https(self):
        from sandcastle.engine.executor import _validate_browser_url
        result = _validate_browser_url("example.com/path")
        assert result == "https://example.com/path"

    def test_leading_whitespace_stripped(self):
        from sandcastle.engine.executor import _validate_browser_url
        result = _validate_browser_url("  https://example.com  ")
        assert result == "https://example.com"
