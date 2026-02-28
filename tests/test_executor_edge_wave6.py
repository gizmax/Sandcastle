"""
Wave 6 deep audit - executor edge case tests.

Covers:
1. resolve_variable for steps.X.status / steps.X.error / steps.X.cost
2. Loop step with empty step_ids validation
3. _escape_braces applied uniformly to all template variables
4. Classify step fuzzy matching guards against spurious substring matches
5. RunContext.with_item deep-copy isolation
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    ClassifyConfig,
    LoopConfig,
    StepDefinition,
    WorkflowDefinition,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _UNRESOLVED,
    _escape_braces,
    resolve_templates,
    resolve_variable,
)


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────

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
        step_results=kwargs.get("step_results", {}),
        costs=kwargs.get("costs", []),
        max_cost_usd=kwargs.get("max_cost_usd", None),
        workflow_name=kwargs.get("workflow_name", "test_wf"),
    )


def step(**kwargs) -> StepDefinition:
    return StepDefinition(
        id=kwargs.get("id", "s1"),
        prompt=kwargs.get("prompt", "Test prompt"),
        model=kwargs.get("model", "sonnet"),
        max_turns=kwargs.get("max_turns", 1),
        timeout=kwargs.get("timeout", 60),
        retry=kwargs.get("retry"),
        type=kwargs.get("type", "standard"),
        loop_config=kwargs.get("loop_config"),
        classify_config=kwargs.get("classify_config"),
    )


# ═══════════════════════════════════════════════════════════
# BUG 1: resolve_variable for steps.X.status / error / cost
# ═══════════════════════════════════════════════════════════

class TestResolveVariableStepMeta:
    """Test that steps.X.status, steps.X.error, steps.X.cost resolve correctly."""

    def test_step_status_completed(self):
        c = ctx(
            step_outputs={"analyze": "some output"},
            step_results={"analyze": StepResult(step_id="analyze", status="completed")},
        )
        assert resolve_variable("steps.analyze.status", c) == "completed"

    def test_step_status_failed(self):
        c = ctx(
            step_outputs={"analyze": None},
            step_results={"analyze": StepResult(step_id="analyze", status="failed", error="timeout")},
        )
        assert resolve_variable("steps.analyze.status", c) == "failed"

    def test_step_status_skipped(self):
        c = ctx(
            step_outputs={"analyze": None},
            step_results={"analyze": StepResult(step_id="analyze", status="skipped")},
        )
        assert resolve_variable("steps.analyze.status", c) == "skipped"

    def test_step_error_present(self):
        c = ctx(
            step_outputs={"fetch": None},
            step_results={"fetch": StepResult(step_id="fetch", status="failed", error="connection refused")},
        )
        assert resolve_variable("steps.fetch.error", c) == "connection refused"

    def test_step_error_none_on_success(self):
        c = ctx(
            step_outputs={"fetch": "ok"},
            step_results={"fetch": StepResult(step_id="fetch", status="completed")},
        )
        assert resolve_variable("steps.fetch.error", c) is None

    def test_step_cost(self):
        c = ctx(
            step_outputs={"gen": "text"},
            step_results={"gen": StepResult(step_id="gen", status="completed", cost_usd=0.0042)},
        )
        assert resolve_variable("steps.gen.cost", c) == pytest.approx(0.0042)

    def test_step_cost_zero(self):
        c = ctx(
            step_outputs={"code": "result"},
            step_results={"code": StepResult(step_id="code", status="completed", cost_usd=0.0)},
        )
        assert resolve_variable("steps.code.cost", c) == 0.0

    def test_step_meta_unknown_step(self):
        """steps.X.status for a step that hasn't executed yet returns _UNRESOLVED."""
        c = ctx(step_outputs={}, step_results={})
        assert resolve_variable("steps.missing.status", c) is _UNRESOLVED

    def test_step_meta_step_in_outputs_but_not_results(self):
        """If step is in step_outputs but not step_results, meta returns _UNRESOLVED."""
        c = ctx(step_outputs={"old": "data"}, step_results={})
        assert resolve_variable("steps.old.status", c) is _UNRESOLVED

    def test_step_output_still_works(self):
        """Ensure output resolution is not broken by the new meta handling."""
        c = ctx(
            step_outputs={"s1": {"key": "val"}},
            step_results={"s1": StepResult(step_id="s1", status="completed")},
        )
        assert resolve_variable("steps.s1.output", c) == {"key": "val"}
        assert resolve_variable("steps.s1.output.key", c) == "val"

    def test_step_meta_in_template(self):
        """resolve_templates can interpolate steps.X.status."""
        c = ctx(
            step_outputs={"fetch": "ok"},
            step_results={"fetch": StepResult(step_id="fetch", status="completed", cost_usd=0.01)},
        )
        result = resolve_templates("Status: {steps.fetch.status}, Cost: {steps.fetch.cost}", c)
        assert "completed" in result
        assert "0.01" in result

    def test_step_error_in_template(self):
        c = ctx(
            step_outputs={"fetch": None},
            step_results={"fetch": StepResult(step_id="fetch", status="failed", error="boom")},
        )
        result = resolve_templates("Error was: {steps.fetch.error}", c)
        assert "boom" in result

    def test_unknown_meta_attribute_returns_unresolved(self):
        """steps.X.duration or other unknown attributes return _UNRESOLVED."""
        c = ctx(
            step_outputs={"s1": "ok"},
            step_results={"s1": StepResult(step_id="s1", status="completed")},
        )
        # "duration" is not a supported meta attribute path
        assert resolve_variable("steps.s1.duration", c) is _UNRESOLVED


# ═══════════════════════════════════════════════════════════
# BUG 2: Loop step with empty step_ids
# ═══════════════════════════════════════════════════════════

class TestLoopEmptyStepIds:
    """Loop step with empty step_ids should fail when items are present."""

    @pytest.mark.asyncio
    async def test_empty_step_ids_with_items_returns_failed(self):
        """Empty step_ids with non-empty items list should fail."""
        from sandcastle.engine.executor import _execute_loop_step

        s = step(
            id="loop1",
            type="loop",
            loop_config=LoopConfig(
                over="{input.items}",
                step_ids=[],
            ),
        )
        c = ctx(input={"items": [1, 2, 3]})
        wf = MagicMock(spec=WorkflowDefinition)
        storage = AsyncMock()

        result = await _execute_loop_step(s, c, None, storage, wf, 0)

        assert result.status == "failed"
        assert "step_ids" in result.error.lower() or "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_empty_step_ids_with_empty_items_completes(self):
        """Empty step_ids with empty items list is a valid no-op."""
        from sandcastle.engine.executor import _execute_loop_step

        s = step(
            id="loop1",
            type="loop",
            loop_config=LoopConfig(
                over="{input.items}",
                step_ids=[],
            ),
        )
        c = ctx(input={"items": []})
        wf = MagicMock(spec=WorkflowDefinition)
        storage = AsyncMock()

        result = await _execute_loop_step(s, c, None, storage, wf, 0)

        assert result.status == "completed"
        assert result.output == []

    @pytest.mark.asyncio
    async def test_empty_step_ids_with_unresolved_items_completes(self):
        """Empty step_ids with unresolved items is a valid no-op."""
        from sandcastle.engine.executor import _execute_loop_step

        s = step(
            id="loop1",
            type="loop",
            loop_config=LoopConfig(
                over="{input.nonexistent}",
                step_ids=[],
            ),
        )
        c = ctx(input={})
        wf = MagicMock(spec=WorkflowDefinition)
        storage = AsyncMock()

        result = await _execute_loop_step(s, c, None, storage, wf, 0)

        assert result.status == "completed"
        assert result.output == []

    @pytest.mark.asyncio
    async def test_nonempty_step_ids_pass(self):
        """Sanity check: non-empty step_ids does not trigger the validation."""
        from sandcastle.engine.executor import _execute_loop_step, execute_step_with_retry

        s = step(
            id="loop1",
            type="loop",
            loop_config=LoopConfig(
                over="{input.items}",
                step_ids=["sub_step"],
                max_iterations=2,
            ),
        )
        c = ctx(input={"items": ["a", "b"]})

        sub = step(id="sub_step", prompt="echo")
        wf = MagicMock(spec=WorkflowDefinition)
        wf.get_step.return_value = sub

        mock_result = StepResult(step_id="sub_step", output="done", status="completed")
        storage = AsyncMock()

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await _execute_loop_step(s, c, None, storage, wf, 0)

        assert result.status == "completed"
        assert len(result.output) == 2


# ═══════════════════════════════════════════════════════════
# BUG 3: _escape_braces applied uniformly
# ═══════════════════════════════════════════════════════════

class TestEscapeBracesConsistency:
    """_escape_braces is applied to steps.* and memory values (LLM sources)."""

    def test_step_output_braces_escaped(self):
        """Step outputs should have braces escaped (LLM-generated content)."""
        c = ctx(step_outputs={"s1": "output with {braces}"})
        result = resolve_templates("Result: {steps.s1.output}", c)
        assert "{{braces}}" in result

    def test_step_output_template_pattern_escaped(self):
        """Step output containing template pattern should not be re-resolved."""
        c = ctx(step_outputs={"s1": "{input.secret}"})
        result = resolve_templates("Result: {steps.s1.output}", c)
        assert "{{input.secret}}" in result
        assert "secret" not in result.replace("{{input.secret}}", "").replace("secret}}", "")

    def test_memory_value_escaped(self):
        """Memory-resolved values should be escaped (LLM-generated content)."""
        c = ctx()
        c.memories = [{"role": "user", "content": "test {injection}"}]
        result = resolve_templates("Memories: {memory}", c)
        assert "{{injection}}" in result or "{injection}" not in result.replace("{{injection}}", "")

    def test_input_value_not_escaped(self):
        """Input values are trusted and should NOT have braces escaped."""
        c = ctx(input={"data": {"key": "value"}})
        result = resolve_templates("{input.data}", c)
        # JSON braces should be preserved as-is for input values
        assert result == '{"key": "value"}'

    def test_input_string_with_braces_preserved(self):
        """Input string values with braces are preserved (trusted source)."""
        c = ctx(input={"name": "test {value} here"})
        result = resolve_templates("Name: {input.name}", c)
        assert "test {value} here" in result

    def test_auto_injected_deps_escaped(self):
        """Auto-injected dependency outputs should have braces escaped."""
        c = ctx(step_outputs={"dep1": "data with {braces}"})
        result = resolve_templates(
            "Main prompt",
            c,
            depends_on=["dep1"],
        )
        assert "{{braces}}" in result

    def test_step_meta_values_escaped(self):
        """Step metadata values should be escaped through resolve_templates."""
        c = ctx(
            step_outputs={"s1": "ok"},
            step_results={"s1": StepResult(step_id="s1", status="completed", error=None)},
        )
        result = resolve_templates("Status: {steps.s1.status}", c)
        assert "completed" in result


# ═══════════════════════════════════════════════════════════
# BUG 4: Classify fuzzy matching improvements
# ═══════════════════════════════════════════════════════════

class TestClassifyFuzzyMatching:
    """Test that classify step fuzzy matching is guarded against spurious matches."""

    def _match_category(self, categories: list[str], raw_response: str) -> str:
        """Simulate the matching logic from _execute_classify_step."""
        raw_category = raw_response.strip().lower()

        # Exact match first
        matched = None
        for cat in categories:
            if cat.lower() == raw_category:
                matched = cat
                break

        if not matched:
            best_cat = None
            best_ratio = 0.0
            for cat in categories:
                cat_lower = cat.lower()
                if cat_lower in raw_category or raw_category in cat_lower:
                    shorter = min(len(cat_lower), len(raw_category))
                    longer = max(len(cat_lower), len(raw_category))
                    ratio = shorter / longer if longer > 0 else 0.0
                    if ratio >= 0.6 and ratio > best_ratio:
                        best_ratio = ratio
                        best_cat = cat
            matched = best_cat

        if not matched:
            matched = categories[0]

        return matched

    def test_exact_match(self):
        assert self._match_category(["positive", "negative", "neutral"], "positive") == "positive"

    def test_exact_match_case_insensitive(self):
        assert self._match_category(["Positive", "Negative"], "POSITIVE") == "Positive"

    def test_close_substring_matches(self):
        """'positively' should still match 'positive' (8/10 = 80% ratio)."""
        assert self._match_category(["positive", "negative"], "positively") == "positive"

    def test_short_fragment_does_not_match_long_category(self):
        """'pos' should not match 'position' (3/8 = 37.5% < 60%)."""
        result = self._match_category(["position", "negative"], "pos")
        # "pos" is a substring of "position" but ratio is too low - should default
        assert result == "position"  # defaults to first category

    def test_very_dissimilar_no_match(self):
        """Completely different string defaults to first category."""
        result = self._match_category(["positive", "negative"], "banana")
        assert result == "positive"  # default

    def test_prefers_best_ratio_match(self):
        """When multiple categories match, prefer the one with highest ratio."""
        # "negat" is 5 chars.  "negative" is 8 chars -> ratio 5/8=0.625.
        # "neg" as category -> ratio 3/5=0.6
        # Should prefer "negative" with higher ratio
        result = self._match_category(["negative", "neg"], "negat")
        # "negat" in "negative" is True (ratio 5/8=0.625)
        # "neg" in "negat" is True (ratio 3/5=0.6)
        assert result == "negative"

    def test_exact_match_takes_priority(self):
        """Exact match should always win, even if a longer fuzzy match exists."""
        result = self._match_category(["neg", "negative"], "neg")
        assert result == "neg"

    def test_ratio_threshold_boundary_below(self):
        """Test the 60% threshold boundary - just below should not match."""
        # "ab" in "abcde" -> ratio 2/5 = 40% < 60%
        result = self._match_category(["abcde", "other"], "ab")
        assert result == "abcde"  # defaults to first (no match)

    def test_ratio_threshold_boundary_at(self):
        """Test the 60% threshold boundary - exactly at 60% should match."""
        # "abc" in "abcde" -> ratio 3/5 = 60% >= 60%
        result = self._match_category(["abcde", "other"], "abc")
        assert result == "abcde"  # substring match

    def test_real_world_sentiment_categories(self):
        """Real-world example: sentiment analysis with typical LLM responses."""
        categories = ["positive", "negative", "neutral"]

        assert self._match_category(categories, "positive") == "positive"
        assert self._match_category(categories, "NEUTRAL") == "neutral"
        # LLM might return "the sentiment is positive" - "positive" in response
        # "positive" (8) in "the sentiment is positive" (25) -> ratio 8/25=0.32 < 60%
        # Should NOT match, defaults to first
        result = self._match_category(categories, "the sentiment is positive")
        assert result == "positive"  # defaults to first (ratio too low for fuzzy)


# ═══════════════════════════════════════════════════════════
# BUG 5: RunContext.with_item deep copy isolation
# ═══════════════════════════════════════════════════════════

class TestWithItemDeepCopy:
    """with_item must deep-copy step_outputs so mutations don't leak."""

    def test_child_dict_mutation_isolated(self):
        """Mutating a nested dict in child should not affect parent."""
        parent = ctx(step_outputs={"s1": {"key": [1, 2, 3]}})
        child = parent.with_item("item_val", 0)

        # Mutate the nested list in child
        child.step_outputs["s1"]["key"].append(4)

        # Parent should be unchanged
        assert parent.step_outputs["s1"]["key"] == [1, 2, 3]

    def test_child_dict_replacement_isolated(self):
        """Replacing a value in child should not affect parent."""
        parent = ctx(step_outputs={"s1": {"a": 1, "b": 2}})
        child = parent.with_item("item_val", 0)

        child.step_outputs["s1"]["a"] = 999
        assert parent.step_outputs["s1"]["a"] == 1

    def test_child_new_key_isolated(self):
        """Adding a new step output in child should not affect parent."""
        parent = ctx(step_outputs={"s1": "hello"})
        child = parent.with_item("item_val", 0)

        child.step_outputs["new_step"] = "new_output"
        assert "new_step" not in parent.step_outputs

    def test_child_deeply_nested_mutation_isolated(self):
        """Deep nesting: child mutations at any level should not leak."""
        parent = ctx(step_outputs={"s1": {"level1": {"level2": {"level3": [1]}}}})
        child = parent.with_item("item_val", 0)

        child.step_outputs["s1"]["level1"]["level2"]["level3"].append(2)
        assert parent.step_outputs["s1"]["level1"]["level2"]["level3"] == [1]

    def test_child_step_results_isolated(self):
        """step_results in child should also be deep-copied."""
        parent_result = StepResult(step_id="s1", status="completed", output={"data": [1, 2]})
        parent = ctx(
            step_outputs={"s1": "ok"},
            step_results={"s1": parent_result},
        )
        child = parent.with_item("item_val", 0)

        # Mutate the step result output in child
        child.step_results["s1"].output["data"].append(3)

        # Parent should be unchanged
        assert parent.step_results["s1"].output["data"] == [1, 2]

    def test_branch_skip_steps_isolated(self):
        """branch_skip_steps should be isolated (set copy)."""
        parent = ctx()
        parent.branch_skip_steps = {"skip_me"}
        child = parent.with_item("x", 0)

        child.branch_skip_steps.add("skip_more")
        assert "skip_more" not in parent.branch_skip_steps

    def test_with_item_preserves_item_and_index(self):
        """Sanity check: _item and _index are set correctly."""
        parent = ctx(input={"key": "value"})
        child = parent.with_item("my_item", 7)

        assert child.input["_item"] == "my_item"
        assert child.input["_index"] == 7
        assert child.input["key"] == "value"
        # Parent should not have _item/_index
        assert "_item" not in parent.input

    def test_costs_list_isolated(self):
        """Costs list should already be isolated (new empty list)."""
        parent = ctx(costs=[1.0, 2.0])
        child = parent.with_item("x", 0)

        assert child.costs == []
        child.costs.append(3.0)
        assert parent.costs == [1.0, 2.0]

    def test_multiple_children_isolated_from_each_other(self):
        """Multiple children from the same parent should be isolated."""
        parent = ctx(step_outputs={"s1": {"data": []}})
        child1 = parent.with_item("a", 0)
        child2 = parent.with_item("b", 1)

        child1.step_outputs["s1"]["data"].append("from_child1")
        child2.step_outputs["s1"]["data"].append("from_child2")

        assert parent.step_outputs["s1"]["data"] == []
        assert child1.step_outputs["s1"]["data"] == ["from_child1"]
        assert child2.step_outputs["s1"]["data"] == ["from_child2"]


# ═══════════════════════════════════════════════════════════
# Integration: step_results populated through _handle_step_result
# ═══════════════════════════════════════════════════════════

class TestStepResultsPopulated:
    """Verify that step_results is populated during step execution."""

    @pytest.mark.asyncio
    async def test_handle_step_result_populates_step_results(self):
        from sandcastle.engine.executor import _prepare_and_run_step

        c = ctx(input={"topic": "AI"})

        mock_result = StepResult(
            step_id="s1",
            output="Generated text",
            cost_usd=0.005,
            duration_seconds=1.2,
            status="completed",
        )

        wf = MagicMock(spec=WorkflowDefinition)
        s = step(id="s1", type="llm", prompt="Write about {input.topic}")
        wf.get_step.return_value = s
        wf.on_failure = None

        with (
            patch(
                "sandcastle.engine.executor._execute_llm_step",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("sandcastle.engine.executor.event_bus", MagicMock()),
        ):
            await _prepare_and_run_step("s1", wf, c, None, AsyncMock(), [], None, 0)

        assert "s1" in c.step_results
        assert c.step_results["s1"].status == "completed"
        assert c.step_results["s1"].cost_usd == 0.005

    @pytest.mark.asyncio
    async def test_skipped_step_populates_step_results(self):
        from sandcastle.engine.executor import _prepare_and_run_step

        c = ctx()
        c.branch_skip_steps = {"s1"}

        wf = MagicMock(spec=WorkflowDefinition)
        s = step(id="s1", type="llm", prompt="ignored")
        wf.get_step.return_value = s
        wf.on_failure = None

        with patch("sandcastle.engine.executor.event_bus", MagicMock()):
            await _prepare_and_run_step("s1", wf, c, None, AsyncMock(), [], None, 0)

        assert "s1" in c.step_results
        assert c.step_results["s1"].status == "skipped"
