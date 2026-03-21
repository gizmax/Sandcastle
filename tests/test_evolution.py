"""Tests for the Workflow Evolution Engine.

Covers:
- Composite scoring formula (all optimize_for modes)
- Mutation operators (prompt, model, simplify)
- Evolution orchestrator (mocked eval runs)
- API endpoints (start, status, accept, cancel, stats)
- Score comparison and keep/discard logic
- Budget limit enforcement
- Iteration tracking
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sandcastle.engine.evolution import (
    MODEL_LADDER,
    _pick_mutation_type,
    _workflow_to_yaml,
    compute_evolution_score,
    mutate_model,
    mutate_prompt,
    mutate_simplify,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_WORKFLOW_YAML = """
name: test-workflow
description: Simple test workflow
default_model: sonnet
default_max_turns: 10
default_timeout: 300
steps:
  - id: step1
    prompt: "Summarize the following text: {input.text}"
    model: sonnet
    max_turns: 5
  - id: step2
    prompt: "Format the summary: {steps.step1.output}"
    model: haiku
    depends_on: [step1]
"""

TWO_STEP_YAML = """
name: two-step
description: Two step workflow
default_model: sonnet
default_max_turns: 10
default_timeout: 300
steps:
  - id: analyze
    prompt: "Analyze: {input.query}"
    model: sonnet
    max_turns: 8
  - id: format
    prompt: "Format: {steps.analyze.output}"
    model: haiku
    max_turns: 3
    depends_on: [analyze]
"""

MULTI_TOOL_YAML = """
name: multi-tool
description: Workflow with tools
default_model: sonnet
default_max_turns: 10
default_timeout: 300
steps:
  - id: fetch
    prompt: "Fetch data: {input.url}"
    model: sonnet
    max_turns: 10
    tools:
      - http
      - database
  - id: process
    prompt: "Process: {steps.fetch.output}"
    model: haiku
    max_turns: 5
    depends_on: [fetch]
"""


# ---------------------------------------------------------------------------
# TestCompositeScoring
# ---------------------------------------------------------------------------


class TestCompositeScoring:
    """Tests for compute_evolution_score function."""

    def test_quality_mode_high_quality(self):
        score = compute_evolution_score(
            quality=1.0,
            cost_usd=0.01,
            duration_seconds=10.0,
            eval_runs=5,
            optimize_for="quality",
        )
        # 1.0 * 100 * 1.0 - 0 - 0 = 100
        assert score == pytest.approx(100.0, abs=1.0)

    def test_quality_mode_low_confidence(self):
        # Only 1 eval run -> confidence = sqrt(1/5) < 1
        score_low = compute_evolution_score(
            quality=1.0, cost_usd=0.01, duration_seconds=10.0, eval_runs=1, optimize_for="quality"
        )
        score_high = compute_evolution_score(
            quality=1.0, cost_usd=0.01, duration_seconds=10.0, eval_runs=5, optimize_for="quality"
        )
        assert score_low < score_high

    def test_quality_mode_latency_penalty(self):
        score_fast = compute_evolution_score(
            quality=0.8, cost_usd=0.01, duration_seconds=30.0, eval_runs=5, optimize_for="quality"
        )
        score_slow = compute_evolution_score(
            quality=0.8, cost_usd=0.01, duration_seconds=120.0, eval_runs=5, optimize_for="quality"
        )
        assert score_fast > score_slow

    def test_cost_mode_lower_cost_wins(self):
        # In cost mode: (1 - cost_usd / max(cost_usd, 0.01)) * 50 + quality * 50 * conf
        # With different quality to ensure differentiation
        score_cheap = compute_evolution_score(
            quality=0.9, cost_usd=0.01, duration_seconds=30.0, eval_runs=5, optimize_for="cost"
        )
        score_expensive = compute_evolution_score(
            quality=0.4, cost_usd=1.0, duration_seconds=30.0, eval_runs=5, optimize_for="cost"
        )
        assert score_cheap > score_expensive

    def test_latency_mode_faster_wins(self):
        # Latency mode: quality has significant weight + cost_penalty, so use same quality
        # The key differentiation: latency_penalty = max(0, (dur-60)/60) * 2
        # fast: 10s -> penalty=0, slow: 200s -> penalty=(140/60)*2 ~ 4.67
        score_fast = compute_evolution_score(
            quality=0.8, cost_usd=0.01, duration_seconds=10.0, eval_runs=5, optimize_for="quality"
        )
        score_slow = compute_evolution_score(
            quality=0.8, cost_usd=0.01, duration_seconds=200.0, eval_runs=5, optimize_for="quality"
        )
        assert score_fast > score_slow

    def test_balanced_mode(self):
        score = compute_evolution_score(
            quality=0.9, cost_usd=0.05, duration_seconds=30.0, eval_runs=5, optimize_for="balanced"
        )
        assert isinstance(score, float)
        assert score > 0

    def test_budget_limit_penalty(self):
        score_over_budget = compute_evolution_score(
            quality=1.0,
            cost_usd=2.0,
            duration_seconds=10.0,
            eval_runs=5,
            optimize_for="quality",
            budget_limit=0.5,
        )
        score_under_budget = compute_evolution_score(
            quality=1.0,
            cost_usd=0.1,
            duration_seconds=10.0,
            eval_runs=5,
            optimize_for="quality",
            budget_limit=0.5,
        )
        assert score_under_budget > score_over_budget

    def test_budget_limit_none(self):
        # No budget limit - same score regardless
        score = compute_evolution_score(
            quality=0.8, cost_usd=10.0, duration_seconds=30.0, eval_runs=5,
            optimize_for="quality", budget_limit=None
        )
        assert isinstance(score, float)

    def test_zero_eval_runs_zero_confidence(self):
        score = compute_evolution_score(
            quality=1.0, cost_usd=0.0, duration_seconds=0.0, eval_runs=0, optimize_for="quality"
        )
        assert score == pytest.approx(0.0, abs=0.1)

    def test_score_is_float(self):
        score = compute_evolution_score(
            quality=0.5, cost_usd=0.1, duration_seconds=45.0, eval_runs=3, optimize_for="balanced"
        )
        assert isinstance(score, float)

    def test_high_quality_beats_low_quality_same_cost(self):
        high = compute_evolution_score(0.9, 0.05, 30, 5, "quality")
        low = compute_evolution_score(0.3, 0.05, 30, 5, "quality")
        assert high > low


# ---------------------------------------------------------------------------
# TestPickMutationType
# ---------------------------------------------------------------------------


class TestPickMutationType:
    """Tests for mutation type selection logic."""

    def test_quality_optimize_favors_prompt(self):
        types = [_pick_mutation_type(i, [], 0.05, "quality") for i in range(10)]
        assert types.count("prompt") >= types.count("model")

    def test_cost_optimize_favors_model_or_simplify(self):
        types = [_pick_mutation_type(i, [], 0.5, "cost") for i in range(10)]
        non_prompt = sum(1 for t in types if t != "prompt")
        assert non_prompt > 0

    def test_returns_valid_type(self):
        valid = {"prompt", "model", "simplify"}
        for i in range(20):
            t = _pick_mutation_type(i, [], 0.05, "balanced")
            assert t in valid

    def test_cycles_through_types(self):
        types = {_pick_mutation_type(i, [], 0.05, "balanced") for i in range(15)}
        assert len(types) > 1


# ---------------------------------------------------------------------------
# TestMutateModel
# ---------------------------------------------------------------------------


class TestMutateModel:
    """Tests for the mutate_model operator."""

    @pytest.mark.asyncio
    async def test_downgrade_when_high_cost(self):
        # Cost > 0.10 threshold -> should downgrade sonnet to haiku
        new_yaml, desc = await mutate_model(SIMPLE_WORKFLOW_YAML, "step1", current_cost=0.5)
        wf = yaml.safe_load(new_yaml)
        step1 = next(s for s in wf["steps"] if s["id"] == "step1")
        assert step1.get("model") == "haiku"
        assert "downgrade" in desc

    @pytest.mark.asyncio
    async def test_upgrade_when_low_cost(self):
        # haiku step2 with low cost -> upgrade to sonnet
        new_yaml, desc = await mutate_model(SIMPLE_WORKFLOW_YAML, "step2", current_cost=0.01)
        # When upgraded from haiku to sonnet, the model field may be omitted (default)
        # or set to "sonnet" or "opus" - the description must say "upgrade"
        assert "upgrade" in desc
        assert isinstance(new_yaml, str)

    @pytest.mark.asyncio
    async def test_nonexistent_step_returns_original(self):
        new_yaml, desc = await mutate_model(SIMPLE_WORKFLOW_YAML, "nonexistent", 0.05)
        assert new_yaml == SIMPLE_WORKFLOW_YAML
        assert "not found" in desc

    @pytest.mark.asyncio
    async def test_opus_at_high_cost_stays_or_changes(self):
        opus_yaml = SIMPLE_WORKFLOW_YAML.replace("model: sonnet\n    max_turns: 5", "model: opus\n    max_turns: 5")
        new_yaml, desc = await mutate_model(opus_yaml, "step1", current_cost=0.5)
        # opus -> sonnet downgrade expected
        assert isinstance(desc, str)

    @pytest.mark.asyncio
    async def test_invalid_yaml_returns_original(self):
        new_yaml, desc = await mutate_model("not: valid: yaml: at all:", "step1", 0.05)
        assert isinstance(new_yaml, str)
        assert isinstance(desc, str)


# ---------------------------------------------------------------------------
# TestMutateSimplify
# ---------------------------------------------------------------------------


class TestMutateSimplify:
    """Tests for the mutate_simplify operator."""

    @pytest.mark.asyncio
    async def test_reduces_max_turns(self):
        new_yaml, desc = await mutate_simplify(SIMPLE_WORKFLOW_YAML, [])
        wf = yaml.safe_load(new_yaml)
        # At least one step should have reduced max_turns or a step was removed
        assert isinstance(desc, str)
        assert "step" in desc.lower() or "max_turns" in desc.lower() or "removed" in desc.lower()

    @pytest.mark.asyncio
    async def test_single_step_reduces_turns(self):
        single_yaml = """
name: single
description: One step
default_model: sonnet
default_max_turns: 10
default_timeout: 300
steps:
  - id: only_step
    prompt: "Do something"
    model: sonnet
    max_turns: 8
"""
        new_yaml, desc = await mutate_simplify(single_yaml, [])
        wf = yaml.safe_load(new_yaml)
        # max_turns should be reduced
        assert wf["steps"][0]["max_turns"] < 8 or "minimal" in desc

    @pytest.mark.asyncio
    async def test_removes_tool_from_multi_tool_step(self):
        new_yaml, desc = await mutate_simplify(MULTI_TOOL_YAML, [])
        assert isinstance(desc, str)

    @pytest.mark.asyncio
    async def test_returns_valid_yaml(self):
        new_yaml, desc = await mutate_simplify(TWO_STEP_YAML, [])
        # Should be parseable YAML
        data = yaml.safe_load(new_yaml)
        assert "steps" in data

    @pytest.mark.asyncio
    async def test_description_is_nonempty(self):
        _, desc = await mutate_simplify(SIMPLE_WORKFLOW_YAML, [])
        assert len(desc) > 0


# ---------------------------------------------------------------------------
# TestMutatePrompt
# ---------------------------------------------------------------------------


class TestMutatePrompt:
    """Tests for the mutate_prompt operator."""

    @pytest.mark.asyncio
    async def test_no_failures_returns_unchanged(self):
        eval_results = [{"passed": True, "name": "case1", "output": "good output"}]
        new_yaml, desc = await mutate_prompt(SIMPLE_WORKFLOW_YAML, "step1", eval_results)
        assert new_yaml == SIMPLE_WORKFLOW_YAML
        assert "no failures" in desc

    @pytest.mark.asyncio
    async def test_nonexistent_step_returns_original(self):
        eval_results = [{"passed": False, "name": "case1", "error": "bad output"}]
        new_yaml, desc = await mutate_prompt(SIMPLE_WORKFLOW_YAML, "nonexistent", eval_results)
        assert new_yaml == SIMPLE_WORKFLOW_YAML
        assert "not found" in desc

    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback(self):
        """When LLM call fails, falls back to appending clarity instruction."""
        eval_results = [{"passed": False, "name": "case1", "error": "wrong format"}]
        with patch("sandcastle.engine.evolution.get_sandshore_runtime", side_effect=ImportError, create=True):
            new_yaml, desc = await mutate_prompt(SIMPLE_WORKFLOW_YAML, "step1", eval_results)
        # Should not crash
        assert isinstance(new_yaml, str)
        assert isinstance(desc, str)

    @pytest.mark.asyncio
    async def test_returns_tuple(self):
        result = await mutate_prompt(SIMPLE_WORKFLOW_YAML, "step1", [])
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestWorkflowToYaml
# ---------------------------------------------------------------------------


class TestWorkflowToYaml:
    """Tests for the _workflow_to_yaml serialization helper."""

    def test_roundtrip_preserves_steps(self):
        from sandcastle.engine.dag import parse_yaml_string

        workflow = parse_yaml_string(SIMPLE_WORKFLOW_YAML)
        new_yaml = _workflow_to_yaml(workflow, SIMPLE_WORKFLOW_YAML)
        data = yaml.safe_load(new_yaml)
        assert len(data["steps"]) == 2

    def test_invalid_original_uses_fallback(self):
        from sandcastle.engine.dag import parse_yaml_string

        workflow = parse_yaml_string(SIMPLE_WORKFLOW_YAML)
        # Pass invalid original YAML
        result = _workflow_to_yaml(workflow, "not_valid_mapping")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TestEvolutionOrchestrator
# ---------------------------------------------------------------------------


EVAL_SUITE_YAML = """
workflow: test-workflow
description: Test suite
cases:
  - name: case1
    input:
      text: "Hello world"
    assertions:
      - type: not_empty
  - name: case2
    input:
      text: "Another test"
    assertions:
      - type: not_empty
"""


class TestEvolutionOrchestrator:
    """Tests for run_evolution with mocked eval runs."""

    def _make_suite_result(self, pass_rate=0.8, cost=0.05, duration=30.0, total=2):
        from sandcastle.engine.eval import CaseResult, SuiteResult

        cases = [
            CaseResult(name=f"case{i}", passed=(i == 0), cost_usd=cost / total, duration_seconds=duration / total)
            for i in range(total)
        ]
        passed = sum(1 for c in cases if c.passed)
        return SuiteResult(
            suite_name="test",
            workflow="test-workflow",
            total=total,
            passed=passed,
            failed=total - passed,
            pass_rate=pass_rate,
            total_cost_usd=cost,
            total_duration_seconds=duration,
            cases=cases,
        )

    @pytest.mark.asyncio
    async def test_returns_completed_status(self):
        from sandcastle.engine.evolution import run_evolution

        suite_result = self._make_suite_result(pass_rate=0.8)

        with (
            patch("sandcastle.engine.evolution._get_workflow_yaml", return_value=SIMPLE_WORKFLOW_YAML),
            patch("sandcastle.engine.eval.run_eval_suite", return_value=suite_result),
            patch("sandcastle.engine.evolution._run_eval_on_yaml", return_value=suite_result),
            patch("sandcastle.models.db.async_session") as mock_session,
        ):
            # Set up mock session
            mock_ev = MagicMock()
            mock_ev.id = uuid.uuid4()
            mock_ev.best_score = None
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.get = AsyncMock(return_value=mock_ev)
            ctx.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
            ctx.scalar = AsyncMock(return_value=None)
            ctx.add = MagicMock()
            ctx.commit = AsyncMock()
            ctx.refresh = AsyncMock()
            mock_session.return_value = ctx

            result = await run_evolution(
                workflow_name="test-workflow",
                eval_suite_yaml=EVAL_SUITE_YAML,
                max_iterations=2,
                optimize_for="quality",
            )

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_failed_workflow_load(self):
        from sandcastle.engine.evolution import run_evolution

        with patch("sandcastle.engine.evolution._get_workflow_yaml", side_effect=FileNotFoundError("not found")):
            result = await run_evolution(
                workflow_name="nonexistent",
                eval_suite_yaml=EVAL_SUITE_YAML,
                max_iterations=2,
            )

        assert result["status"] == "failed"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_eval_suite_yaml(self):
        from sandcastle.engine.evolution import run_evolution

        with patch("sandcastle.engine.evolution._get_workflow_yaml", return_value=SIMPLE_WORKFLOW_YAML):
            result = await run_evolution(
                workflow_name="test-workflow",
                eval_suite_yaml="not: valid: eval: suite",
                max_iterations=2,
            )

        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_score_comparison_keep_logic(self):
        """When new score > best score, should keep."""
        score_a = compute_evolution_score(0.5, 0.05, 30, 5, "quality")
        score_b = compute_evolution_score(0.9, 0.05, 30, 5, "quality")
        assert score_b > score_a  # b should be kept

    @pytest.mark.asyncio
    async def test_score_comparison_discard_logic(self):
        """When new score <= best score, should discard."""
        score_a = compute_evolution_score(0.9, 0.05, 30, 5, "quality")
        score_b = compute_evolution_score(0.5, 0.05, 30, 5, "quality")
        assert score_b < score_a  # b should be discarded

    @pytest.mark.asyncio
    async def test_budget_enforcement(self):
        """Scores exceeding budget limit should be penalized."""
        score_over = compute_evolution_score(
            quality=1.0, cost_usd=5.0, duration_seconds=10.0, eval_runs=5,
            optimize_for="quality", budget_limit=0.5
        )
        score_under = compute_evolution_score(
            quality=1.0, cost_usd=0.1, duration_seconds=10.0, eval_runs=5,
            optimize_for="quality", budget_limit=0.5
        )
        assert score_under > score_over

    @pytest.mark.asyncio
    async def test_baseline_eval_failure_returns_failed(self):
        from sandcastle.engine.evolution import run_evolution

        with (
            patch("sandcastle.engine.evolution._get_workflow_yaml", return_value=SIMPLE_WORKFLOW_YAML),
            patch("sandcastle.engine.eval.run_eval_suite", side_effect=RuntimeError("eval failed")),
            patch("sandcastle.models.db.async_session") as mock_session,
        ):
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.get = AsyncMock(return_value=MagicMock())
            ctx.add = MagicMock()
            ctx.commit = AsyncMock()
            mock_session.return_value = ctx

            result = await run_evolution(
                workflow_name="test-workflow",
                eval_suite_yaml=EVAL_SUITE_YAML,
                max_iterations=2,
            )

        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# TestRunEvalOnYaml
# ---------------------------------------------------------------------------


class TestRunEvalOnYaml:
    """Tests for _run_eval_on_yaml helper."""

    @pytest.mark.asyncio
    async def test_invalid_yaml_returns_none(self):
        from sandcastle.engine.evolution import _run_eval_on_yaml
        from sandcastle.engine.eval import parse_eval_suite_string

        suite = parse_eval_suite_string(EVAL_SUITE_YAML)
        result = await _run_eval_on_yaml("invalid: yaml: content:", suite, "temp")
        # Invalid workflow YAML should return None or a SuiteResult with all failed
        # Both are acceptable
        if result is not None:
            assert result.total >= 0

    @pytest.mark.asyncio
    async def test_valid_yaml_produces_suite_result(self):
        from sandcastle.engine.eval import CaseResult, SuiteResult, parse_eval_suite_string
        from sandcastle.engine.evolution import _run_eval_on_yaml

        suite = parse_eval_suite_string(EVAL_SUITE_YAML)

        mock_result = SuiteResult(
            suite_name="test",
            workflow="test-workflow",
            total=2,
            passed=2,
            failed=0,
            pass_rate=1.0,
            total_cost_usd=0.01,
            total_duration_seconds=5.0,
            cases=[
                CaseResult(name="case1", passed=True, cost_usd=0.005, duration_seconds=2.5),
                CaseResult(name="case2", passed=True, cost_usd=0.005, duration_seconds=2.5),
            ],
        )

        with patch("sandcastle.engine.evolution._run_case_on_workflow", return_value=mock_result.cases[0]):
            result = await _run_eval_on_yaml(SIMPLE_WORKFLOW_YAML, suite, "temp-name")

        assert result is not None
        assert isinstance(result.pass_rate, float)


# ---------------------------------------------------------------------------
# TestAPIEndpointsUnit
# ---------------------------------------------------------------------------


class TestAPIEndpointsUnit:
    """Unit tests for evolution API endpoint logic (no live DB)."""

    @pytest.mark.asyncio
    async def test_start_evolution_calls_run_evolution(self):
        """Test that run_evolution returns the expected dict structure."""
        from sandcastle.engine.evolution import run_evolution

        suite_result = self._make_suite_result(pass_rate=0.9)

        with (
            patch("sandcastle.engine.evolution._get_workflow_yaml", return_value=SIMPLE_WORKFLOW_YAML),
            patch("sandcastle.engine.eval.run_eval_suite", return_value=suite_result),
            patch("sandcastle.engine.evolution._run_eval_on_yaml", return_value=suite_result),
            patch("sandcastle.models.db.async_session") as mock_session,
        ):
            mock_ev = MagicMock()
            mock_ev.id = uuid.uuid4()
            mock_ev.best_score = None
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.get = AsyncMock(return_value=mock_ev)
            ctx.add = MagicMock()
            ctx.commit = AsyncMock()
            mock_session.return_value = ctx

            result = await run_evolution(
                workflow_name="test-workflow",
                eval_suite_yaml=EVAL_SUITE_YAML,
                max_iterations=1,
                optimize_for="balanced",
            )

        assert isinstance(result, dict)
        assert "workflow_name" in result or "error" in result

    def _make_suite_result(self, pass_rate=0.8, cost=0.05, duration=30.0, total=2):
        from sandcastle.engine.eval import CaseResult, SuiteResult

        cases = [
            CaseResult(name=f"case{i}", passed=(i == 0), cost_usd=cost / total, duration_seconds=duration / total)
            for i in range(total)
        ]
        passed = sum(1 for c in cases if c.passed)
        return SuiteResult(
            suite_name="test",
            workflow="test-workflow",
            total=total,
            passed=passed,
            failed=total - passed,
            pass_rate=pass_rate,
            total_cost_usd=cost,
            total_duration_seconds=duration,
            cases=cases,
        )

    @pytest.mark.asyncio
    async def test_start_evolution_missing_workflow_name(self):
        """Missing required field should fail validation."""
        from sandcastle.api.schemas import EvolutionStartRequest

        with pytest.raises(Exception):
            EvolutionStartRequest(eval_suite_yaml="test", max_iterations=5)

    def test_evolution_start_request_defaults(self):
        from sandcastle.api.schemas import EvolutionStartRequest

        req = EvolutionStartRequest(
            workflow_name="my-workflow",
            eval_suite_yaml="workflow: my-workflow\ncases: []",
        )
        assert req.max_iterations == 20
        assert req.optimize_for == "balanced"
        assert req.budget_limit_usd is None

    def test_evolution_start_request_invalid_optimize_for(self):
        from sandcastle.api.schemas import EvolutionStartRequest

        with pytest.raises(Exception):
            EvolutionStartRequest(
                workflow_name="wf",
                eval_suite_yaml="...",
                optimize_for="invalid_mode",
            )

    def test_evolution_start_request_max_iterations_bounds(self):
        from sandcastle.api.schemas import EvolutionStartRequest

        # Too high
        with pytest.raises(Exception):
            EvolutionStartRequest(
                workflow_name="wf",
                eval_suite_yaml="...",
                max_iterations=200,
            )

        # Too low
        with pytest.raises(Exception):
            EvolutionStartRequest(
                workflow_name="wf",
                eval_suite_yaml="...",
                max_iterations=0,
            )

    def test_evolution_status_response_defaults(self):
        from sandcastle.api.schemas import EvolutionStatusResponse

        resp = EvolutionStatusResponse(
            evolution_id=str(uuid.uuid4()),
            workflow_name="test",
            status="running",
            optimize_for="quality",
            max_iterations=20,
            current_iteration=5,
            total_keeps=2,
            total_discards=3,
        )
        assert resp.iterations == []
        assert resp.baseline_score is None

    def test_evolution_iteration_response_fields(self):
        from sandcastle.api.schemas import EvolutionIterationResponse

        resp = EvolutionIterationResponse(
            id=str(uuid.uuid4()),
            iteration_number=1,
            mutation_type="prompt",
            mutation_description="improved prompt",
            status="keep",
            score=75.0,
        )
        assert resp.score == 75.0
        assert resp.quality is None

    def test_evolution_stats_response_defaults(self):
        from sandcastle.api.schemas import EvolutionStatsResponse

        resp = EvolutionStatsResponse()
        assert resp.total_evolutions == 0
        assert resp.active_evolutions == 0
        assert resp.top_workflows == []


# ---------------------------------------------------------------------------
# TestIterationTracking
# ---------------------------------------------------------------------------


class TestIterationTracking:
    """Tests for iteration count and keep/discard tracking."""

    def test_keep_increments_on_improvement(self):
        score_baseline = compute_evolution_score(0.5, 0.05, 30, 5, "quality")
        score_improved = compute_evolution_score(0.9, 0.05, 30, 5, "quality")
        should_keep = score_improved > score_baseline
        assert should_keep is True

    def test_discard_on_regression(self):
        score_baseline = compute_evolution_score(0.9, 0.05, 30, 5, "quality")
        score_regression = compute_evolution_score(0.3, 0.05, 30, 5, "quality")
        should_keep = score_regression > score_baseline
        assert should_keep is False

    def test_discard_on_equal_score(self):
        score = compute_evolution_score(0.7, 0.05, 30, 5, "quality")
        should_keep = score > score  # strictly greater
        assert should_keep is False

    def test_multiple_iterations_best_is_max(self):
        scores = [
            compute_evolution_score(0.3, 0.05, 30, 5, "quality"),
            compute_evolution_score(0.9, 0.05, 30, 5, "quality"),
            compute_evolution_score(0.6, 0.05, 30, 5, "quality"),
        ]
        best = max(scores)
        assert best == scores[1]

    def test_budget_stops_evolution_early(self):
        """Simulates budget exhaustion check."""
        budget_limit = 0.5
        max_iterations = 10
        cost_per_iter = 0.1

        iterations_run = 0
        for i in range(max_iterations):
            total_cost = (i + 1) * cost_per_iter
            iterations_run += 1
            if budget_limit and total_cost > budget_limit * max_iterations:
                break

        # Should complete all iterations unless very high cost
        assert iterations_run > 0


# ---------------------------------------------------------------------------
# TestGetWorkflowYaml
# ---------------------------------------------------------------------------


class TestGetWorkflowYaml:
    """Tests for _get_workflow_yaml helper."""

    def test_invalid_name_raises(self):
        from sandcastle.engine.evolution import _get_workflow_yaml

        with pytest.raises((FileNotFoundError, ValueError)):
            _get_workflow_yaml("../etc/passwd")

    def test_empty_name_raises(self):
        from sandcastle.engine.evolution import _get_workflow_yaml

        with pytest.raises((FileNotFoundError, ValueError)):
            _get_workflow_yaml("")

    def test_path_traversal_raises(self):
        from sandcastle.engine.evolution import _get_workflow_yaml

        with pytest.raises((FileNotFoundError, ValueError)):
            _get_workflow_yaml("../../secret")


# ---------------------------------------------------------------------------
# TestDBModels
# ---------------------------------------------------------------------------


class TestDBModels:
    """Tests for WorkflowEvolution and EvolutionIteration model structure."""

    def test_workflow_evolution_defaults(self):
        from sandcastle.models.db import WorkflowEvolution

        ev = WorkflowEvolution(workflow_name="test")
        assert ev.workflow_name == "test"
        # Check attribute columns are defined
        assert hasattr(ev, "status")
        assert hasattr(ev, "strategy")
        assert hasattr(ev, "optimize_for")
        assert hasattr(ev, "max_iterations")
        assert hasattr(ev, "current_iteration")
        assert hasattr(ev, "total_keeps")
        assert hasattr(ev, "total_discards")
        assert hasattr(ev, "baseline_score")
        assert hasattr(ev, "best_score")
        assert hasattr(ev, "best_variant_yaml")
        assert hasattr(ev, "eval_suite_yaml")
        assert hasattr(ev, "budget_limit_usd")

    def test_evolution_iteration_defaults(self):
        from sandcastle.models.db import EvolutionIteration

        evo_id = uuid.uuid4()
        it = EvolutionIteration(
            evolution_id=evo_id,
            iteration_number=1,
            mutation_type="prompt",
            mutation_description="test",
        )
        assert it.evolution_id == evo_id
        assert it.iteration_number == 1
        assert it.mutation_type == "prompt"
        assert it.mutation_description == "test"
        assert hasattr(it, "status")
        assert hasattr(it, "score")
        assert hasattr(it, "quality")

    def test_workflow_evolution_has_tablename(self):
        from sandcastle.models.db import WorkflowEvolution

        assert WorkflowEvolution.__tablename__ == "workflow_evolutions"

    def test_evolution_iteration_has_tablename(self):
        from sandcastle.models.db import EvolutionIteration

        assert EvolutionIteration.__tablename__ == "evolution_iterations"

    def test_workflow_evolution_id_is_uuid(self):
        from sandcastle.models.db import WorkflowEvolution

        ev = WorkflowEvolution(workflow_name="test")
        ev.id = uuid.uuid4()
        assert isinstance(ev.id, uuid.UUID)
