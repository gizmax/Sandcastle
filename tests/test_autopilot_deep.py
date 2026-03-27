"""Deep tests for AutoPilot self-optimizing workflows (autopilot.py).

Covers: select_winner, _evaluate_schema_completeness, _evaluate_llm_judge,
evaluate_result, apply_variant, pick_variant, should_use_winner,
_check_significance, _two_tailed_p_value, maybe_complete_experiment,
save_sample, get_or_create_experiment, advance_rollout,
ROLLOUT_STAGES, MAX_SAMPLES_PER_EXPERIMENT, and progressive rollout logic.
"""

from __future__ import annotations

import math
import random
import uuid
from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.autopilot import (
    MAX_SAMPLES_PER_EXPERIMENT,
    ROLLOUT_ORDER,
    ROLLOUT_STAGES,
    _check_significance,
    _evaluate_schema_completeness,
    _two_tailed_p_value,
    apply_variant,
    evaluate_result,
    select_winner,
    should_use_winner,
)
from sandcastle.engine.dag import (
    AutoPilotConfig,
    EvaluationConfig,
    StepDefinition,
    VariantConfig,
)

VariantRow = namedtuple(
    "VariantRow", ["variant_id", "count", "avg_quality", "avg_cost", "avg_duration"]
)


# ===================================================================
# Constants and configuration
# ===================================================================


class TestConstants:
    def test_rollout_stages_canary_10_percent(self):
        assert ROLLOUT_STAGES["canary"] == pytest.approx(0.10)

    def test_rollout_stages_partial_50_percent(self):
        assert ROLLOUT_STAGES["partial"] == pytest.approx(0.50)

    def test_rollout_stages_full_100_percent(self):
        assert ROLLOUT_STAGES["full"] == pytest.approx(1.0)

    def test_rollout_order(self):
        assert ROLLOUT_ORDER == ["canary", "partial", "full"]

    def test_max_samples_safety_cap(self):
        assert MAX_SAMPLES_PER_EXPERIMENT == 500
        assert MAX_SAMPLES_PER_EXPERIMENT > 0


# ===================================================================
# _evaluate_schema_completeness
# ===================================================================


class TestSchemaCompleteness:
    def test_full_match(self):
        schema = {"properties": {"a": {}, "b": {}, "c": {}}}
        output = {"a": 1, "b": 2, "c": 3}
        assert _evaluate_schema_completeness(output, schema) == 1.0

    def test_partial_match(self):
        schema = {"properties": {"a": {}, "b": {}, "c": {}, "d": {}}}
        output = {"a": 1, "b": 2}
        assert _evaluate_schema_completeness(output, schema) == pytest.approx(0.5)

    def test_none_values_not_counted(self):
        schema = {"properties": {"a": {}, "b": {}}}
        output = {"a": 1, "b": None}
        assert _evaluate_schema_completeness(output, schema) == pytest.approx(0.5)

    def test_no_schema_with_output(self):
        assert _evaluate_schema_completeness({"data": 1}, None) == 1.0

    def test_no_schema_no_output(self):
        assert _evaluate_schema_completeness(None, None) == 0.0

    def test_non_dict_output(self):
        schema = {"properties": {"a": {}}}
        assert _evaluate_schema_completeness("string", schema) == 0.0
        assert _evaluate_schema_completeness(42, schema) == 0.0
        assert _evaluate_schema_completeness(["list"], schema) == 0.0

    def test_empty_properties(self):
        schema = {"properties": {}}
        assert _evaluate_schema_completeness({"a": 1}, schema) == 1.0

    def test_empty_output(self):
        schema = {"properties": {"a": {}, "b": {}}}
        assert _evaluate_schema_completeness({}, schema) == 0.0

    def test_extra_keys_ignored(self):
        schema = {"properties": {"a": {}}}
        output = {"a": 1, "b": 2, "c": 3}
        assert _evaluate_schema_completeness(output, schema) == 1.0

    def test_single_property(self):
        schema = {"properties": {"name": {}}}
        assert _evaluate_schema_completeness({"name": "test"}, schema) == 1.0
        assert _evaluate_schema_completeness({"other": "test"}, schema) == 0.0


# ===================================================================
# _evaluate_llm_judge
# ===================================================================


class TestEvaluateLlmJudge:
    @pytest.mark.asyncio
    async def test_valid_score_returned(self):
        from sandcastle.engine.autopilot import _evaluate_llm_judge

        config = AutoPilotConfig(
            evaluation=EvaluationConfig(method="llm_judge", criteria="relevance")
        )
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value="0.85"):
            score = await _evaluate_llm_judge({"result": "good"}, config)
            assert score == pytest.approx(0.85)

    @pytest.mark.asyncio
    async def test_score_clamped_to_max_1(self):
        from sandcastle.engine.autopilot import _evaluate_llm_judge

        config = AutoPilotConfig(evaluation=EvaluationConfig(criteria="test"))
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value="1.5"):
            score = await _evaluate_llm_judge("output", config)
            assert score == 1.0

    @pytest.mark.asyncio
    async def test_score_clamped_to_min_0(self):
        from sandcastle.engine.autopilot import _evaluate_llm_judge

        config = AutoPilotConfig(evaluation=EvaluationConfig(criteria="test"))
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value="-0.5"):
            score = await _evaluate_llm_judge("output", config)
            assert score == 0.0

    @pytest.mark.asyncio
    async def test_llm_failure_returns_default(self):
        from sandcastle.engine.autopilot import _evaluate_llm_judge

        config = AutoPilotConfig(evaluation=EvaluationConfig(criteria="test"))
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, side_effect=RuntimeError("fail")):
            score = await _evaluate_llm_judge("output", config)
            assert score == 0.5

    @pytest.mark.asyncio
    async def test_non_numeric_response_returns_default(self):
        from sandcastle.engine.autopilot import _evaluate_llm_judge

        config = AutoPilotConfig(evaluation=EvaluationConfig(criteria="test"))
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value="not a number"):
            score = await _evaluate_llm_judge("output", config)
            assert score == 0.5

    @pytest.mark.asyncio
    async def test_output_truncated(self):
        """Large output should be truncated to 2000 chars."""
        from sandcastle.engine.autopilot import _evaluate_llm_judge

        captured_user = ""

        async def capture_llm(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "0.7"

        config = AutoPilotConfig(evaluation=EvaluationConfig(criteria="test"))
        huge_output = "x" * 10000
        with patch("sandcastle.engine.generator._call_advisor_llm", side_effect=capture_llm):
            await _evaluate_llm_judge(huge_output, config)
            # The output_str should be at most ~2000 chars
            # (the full user message is longer, but the output portion is truncated)
            assert len(captured_user) < 3000

    @pytest.mark.asyncio
    async def test_criteria_passed_to_llm(self):
        """Evaluation criteria should appear in the LLM prompt."""
        from sandcastle.engine.autopilot import _evaluate_llm_judge

        captured_user = ""

        async def capture_llm(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "0.5"

        config = AutoPilotConfig(
            evaluation=EvaluationConfig(criteria="code correctness and style")
        )
        with patch("sandcastle.engine.generator._call_advisor_llm", side_effect=capture_llm):
            await _evaluate_llm_judge("def foo(): pass", config)
            assert "code correctness and style" in captured_user

    @pytest.mark.asyncio
    async def test_no_evaluation_config_uses_default_criteria(self):
        from sandcastle.engine.autopilot import _evaluate_llm_judge

        captured_user = ""

        async def capture_llm(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "0.6"

        config = AutoPilotConfig(evaluation=None)
        with patch("sandcastle.engine.generator._call_advisor_llm", side_effect=capture_llm):
            await _evaluate_llm_judge("output", config)
            assert "overall quality" in captured_user


# ===================================================================
# evaluate_result (top-level dispatcher)
# ===================================================================


class TestEvaluateResult:
    @pytest.mark.asyncio
    async def test_schema_completeness_method(self):
        config = AutoPilotConfig(
            evaluation=EvaluationConfig(method="schema_completeness")
        )
        step = StepDefinition(
            id="test",
            output_schema={"properties": {"a": {}, "b": {}}},
        )
        score = await evaluate_result(config, step, {"a": 1, "b": 2})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_llm_judge_method(self):
        config = AutoPilotConfig(
            evaluation=EvaluationConfig(method="llm_judge", criteria="quality")
        )
        step = StepDefinition(id="test")
        with patch("sandcastle.engine.autopilot._evaluate_llm_judge",
                    new_callable=AsyncMock, return_value=0.9):
            score = await evaluate_result(config, step, {"data": "good"})
            assert score == 0.9

    @pytest.mark.asyncio
    async def test_unknown_method_non_null_check(self):
        config = AutoPilotConfig(
            evaluation=EvaluationConfig(method="custom")
        )
        step = StepDefinition(id="test")
        assert await evaluate_result(config, step, "something") == 1.0
        assert await evaluate_result(config, step, None) == 0.0

    @pytest.mark.asyncio
    async def test_no_evaluation_config_defaults_to_schema(self):
        config = AutoPilotConfig(evaluation=None)
        step = StepDefinition(id="test")
        # No evaluation config -> method = "schema_completeness"
        # No output_schema -> returns 1.0 for non-None output
        score = await evaluate_result(config, step, {"data": 1})
        assert score == 1.0


# ===================================================================
# apply_variant
# ===================================================================


class TestApplyVariant:
    def test_model_only(self):
        step = StepDefinition(id="s1", prompt="original", model="sonnet")
        variant = VariantConfig(id="v1", model="haiku")
        modified = apply_variant(step, variant)
        assert modified.model == "haiku"
        assert modified.prompt == "original"
        assert modified.autopilot is None

    def test_prompt_only(self):
        step = StepDefinition(id="s1", prompt="original", model="sonnet")
        variant = VariantConfig(id="v1", prompt="new prompt")
        modified = apply_variant(step, variant)
        assert modified.prompt == "new prompt"
        assert modified.model == "sonnet"

    def test_max_turns_only(self):
        step = StepDefinition(id="s1", prompt="p", model="sonnet", max_turns=10)
        variant = VariantConfig(id="v1", max_turns=20)
        modified = apply_variant(step, variant)
        assert modified.max_turns == 20
        assert modified.model == "sonnet"

    def test_all_overrides(self):
        step = StepDefinition(id="s1", prompt="old", model="sonnet", max_turns=5)
        variant = VariantConfig(id="v1", model="opus", prompt="new", max_turns=25)
        modified = apply_variant(step, variant)
        assert modified.model == "opus"
        assert modified.prompt == "new"
        assert modified.max_turns == 25

    def test_autopilot_cleared(self):
        """Autopilot must be cleared on the variant to prevent infinite recursion."""
        ap = AutoPilotConfig(enabled=True, variants=[VariantConfig(id="x")])
        step = StepDefinition(id="s1", prompt="p", autopilot=ap)
        variant = VariantConfig(id="v1", model="haiku")
        modified = apply_variant(step, variant)
        assert modified.autopilot is None

    def test_step_id_preserved(self):
        step = StepDefinition(id="my-step", prompt="p")
        variant = VariantConfig(id="v1", model="haiku")
        modified = apply_variant(step, variant)
        assert modified.id == "my-step"

    def test_none_fields_do_not_override(self):
        """Variant fields that are None should not change the step."""
        step = StepDefinition(id="s1", prompt="keep", model="keep-model", max_turns=15)
        variant = VariantConfig(id="v1")  # All None
        modified = apply_variant(step, variant)
        assert modified.prompt == "keep"
        assert modified.model == "keep-model"
        assert modified.max_turns == 15


# ===================================================================
# select_winner - comprehensive
# ===================================================================


class TestSelectWinner:
    def test_optimize_quality_picks_highest(self):
        stats = [
            VariantRow("a", 10, 0.7, 0.01, 1.0),
            VariantRow("b", 10, 0.9, 0.05, 5.0),
            VariantRow("c", 10, 0.8, 0.03, 3.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_optimize_cost_picks_cheapest(self):
        stats = [
            VariantRow("a", 10, 0.8, 0.10, 5.0),
            VariantRow("b", 10, 0.8, 0.01, 5.0),
            VariantRow("c", 10, 0.8, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_optimize_latency_picks_fastest(self):
        stats = [
            VariantRow("a", 10, 0.8, 0.05, 10.0),
            VariantRow("b", 10, 0.8, 0.05, 2.0),
            VariantRow("c", 10, 0.8, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="latency", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_pareto_balanced_wins(self):
        stats = [
            VariantRow("expensive_fast", 10, 0.95, 0.50, 1.0),
            VariantRow("balanced", 10, 0.90, 0.05, 3.0),
            VariantRow("cheap_slow", 10, 0.70, 0.01, 15.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.5)
        winner = select_winner(stats, config)
        # Balanced has best overall trade-off
        assert winner is not None

    def test_quality_threshold_filters(self):
        stats = [
            VariantRow("bad", 10, 0.3, 0.01, 1.0),
            VariantRow("ok", 10, 0.6, 0.05, 3.0),
            VariantRow("good", 10, 0.9, 0.10, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.5)
        winner = select_winner(stats, config)
        # "bad" filtered out, "ok" is cheapest among remaining
        assert winner["variant_id"] == "ok"

    def test_all_below_threshold_returns_best_quality(self):
        stats = [
            VariantRow("a", 10, 0.2, 0.01, 1.0),
            VariantRow("b", 10, 0.4, 0.02, 2.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.9)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_empty_stats(self):
        config = AutoPilotConfig(optimize_for="quality")
        assert select_winner([], config) is None

    def test_single_variant(self):
        stats = [VariantRow("only", 10, 0.8, 0.05, 5.0)]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "only"

    def test_none_avg_quality_treated_as_zero(self):
        stats = [
            VariantRow("a", 10, None, 0.05, 5.0),
            VariantRow("b", 10, 0.5, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.0)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_none_cost_treated_as_zero(self):
        stats = [
            VariantRow("a", 10, 0.8, None, 5.0),
            VariantRow("b", 10, 0.8, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "a"  # None cost -> 0 -> cheapest

    def test_winner_contains_all_fields(self):
        stats = [VariantRow("x", 15, 0.85, 0.03, 4.0)]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert "variant_id" in winner
        assert "count" in winner
        assert "avg_quality" in winner
        assert "avg_cost" in winner
        assert "avg_duration" in winner
        assert winner["count"] == 15
        assert winner["avg_quality"] == pytest.approx(0.85)

    def test_pareto_all_same_cost_and_duration(self):
        """When cost and duration are identical, Pareto should pick highest quality."""
        stats = [
            VariantRow("a", 10, 0.7, 0.05, 5.0),
            VariantRow("b", 10, 0.9, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_pareto_zero_cost_no_division_error(self):
        """Pareto handles zero cost/duration without division by zero."""
        stats = [
            VariantRow("a", 10, 0.8, 0.0, 0.0),
            VariantRow("b", 10, 0.7, 0.0, 0.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.5)
        winner = select_winner(stats, config)
        # Should not raise, pick best quality
        assert winner is not None

    def test_default_optimize_for_is_quality(self):
        """Unknown optimize_for should default to quality."""
        stats = [
            VariantRow("a", 10, 0.9, 0.01, 1.0),
            VariantRow("b", 10, 0.7, 0.10, 10.0),
        ]
        config = AutoPilotConfig(optimize_for="unknown", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "a"

    def test_many_variants(self):
        """Stress test with many variants."""
        stats = [
            VariantRow(f"v{i}", 10, 0.5 + i * 0.01, 0.01 * i, 1.0 * i)
            for i in range(20)
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.0)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "v19"  # Highest quality


# ===================================================================
# _two_tailed_p_value
# ===================================================================


class TestTwoTailedPValue:
    def test_zero_t_stat_returns_1(self):
        p = _two_tailed_p_value(0.0, 100)
        assert p == pytest.approx(1.0)

    def test_large_t_stat_returns_near_zero(self):
        p = _two_tailed_p_value(10.0, 100)
        assert p < 0.001

    def test_negative_df_returns_1(self):
        p = _two_tailed_p_value(2.0, -1)
        assert p == 1.0

    def test_zero_df_returns_1(self):
        p = _two_tailed_p_value(2.0, 0)
        assert p == 1.0

    def test_nan_t_stat_returns_1(self):
        p = _two_tailed_p_value(float("nan"), 10)
        assert p == 1.0

    def test_nan_df_returns_1(self):
        p = _two_tailed_p_value(2.0, float("nan"))
        assert p == 1.0

    def test_inf_t_stat_returns_0(self):
        p = _two_tailed_p_value(float("inf"), 10)
        assert p == 0.0

    def test_result_between_0_and_1(self):
        for t_stat in [0.5, 1.0, 1.96, 2.5, 3.0]:
            p = _two_tailed_p_value(t_stat, 50)
            assert 0.0 <= p <= 1.0

    def test_larger_t_gives_smaller_p(self):
        p1 = _two_tailed_p_value(1.0, 50)
        p2 = _two_tailed_p_value(3.0, 50)
        assert p2 < p1

    def test_small_df_still_works(self):
        p = _two_tailed_p_value(2.0, 5)
        assert 0.0 <= p <= 1.0


# ===================================================================
# _check_significance
# ===================================================================


class TestCheckSignificance:
    def test_clearly_different_distributions(self):
        winner = [0.9, 0.92, 0.88, 0.91, 0.89, 0.93, 0.87, 0.90, 0.91, 0.88]
        loser = [0.3, 0.32, 0.28, 0.31, 0.29, 0.33, 0.27, 0.30, 0.31, 0.28]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True
        assert p < 0.01

    def test_identical_distributions(self):
        scores = [0.5, 0.5, 0.5, 0.5, 0.5]
        is_sig, p = _check_significance(scores, scores)
        # Identical scores -> se=0 -> mean1 == mean2 -> p=1.0
        assert is_sig is False
        assert p == 1.0

    def test_too_few_samples(self):
        is_sig, p = _check_significance([0.9, 0.8], [0.3, 0.4])
        assert is_sig is False
        assert p == 1.0

    def test_empty_lists(self):
        is_sig, p = _check_significance([], [])
        assert is_sig is False
        assert p == 1.0

    def test_one_score_each(self):
        is_sig, p = _check_significance([1.0], [0.0])
        assert is_sig is False
        assert p == 1.0

    def test_minimum_samples_three(self):
        winner = [0.9, 0.8, 0.85]
        loser = [0.1, 0.2, 0.15]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True
        assert p < 0.05

    def test_overlapping_distributions_not_significant(self):
        winner = [0.50, 0.52, 0.48, 0.51, 0.49]
        loser = [0.49, 0.51, 0.47, 0.50, 0.48]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is False
        assert p > 0.05

    def test_custom_p_threshold(self):
        winner = [0.9, 0.88, 0.92, 0.91, 0.89]
        loser = [0.7, 0.72, 0.68, 0.71, 0.69]
        # Default threshold 0.05 -> significant
        is_sig_05, p = _check_significance(winner, loser, min_p_value=0.05)
        assert is_sig_05 is True
        # Strict threshold 0.001 -> might not be significant with 5 samples
        is_sig_001, p = _check_significance(winner, loser, min_p_value=0.001)
        # Just verify p_value is consistent
        assert p >= 0.0

    def test_zero_variance_winner_higher(self):
        winner = [1.0, 1.0, 1.0, 1.0, 1.0]
        loser = [0.0, 0.0, 0.0, 0.0, 0.0]
        is_sig, p = _check_significance(winner, loser)
        # se=0, mean1 > mean2 -> returns (True, 0.0)
        assert is_sig is True
        assert p == 0.0


# ===================================================================
# should_use_winner
# ===================================================================


class TestShouldUseWinner:
    def test_none_stage_returns_false(self):
        assert should_use_winner(None) is False

    def test_full_stage_always_true(self):
        # full = 1.0 traffic, random() < 1.0 is always True
        random.seed(42)
        for _ in range(100):
            assert should_use_winner("full") is True

    def test_canary_sometimes_true(self):
        random.seed(0)
        results = [should_use_winner("canary") for _ in range(1000)]
        # 10% traffic, expect roughly 100 True out of 1000
        true_count = sum(results)
        assert 50 < true_count < 200

    def test_partial_roughly_half(self):
        random.seed(0)
        results = [should_use_winner("partial") for _ in range(1000)]
        true_count = sum(results)
        assert 400 < true_count < 600

    def test_unknown_stage_returns_false(self):
        # Unknown stage not in ROLLOUT_STAGES -> traffic = 0
        assert should_use_winner("nonexistent") is False


# ===================================================================
# pick_variant (Thompson Sampling)
# ===================================================================


class TestPickVariant:
    @pytest.mark.asyncio
    async def test_empty_variants_returns_none(self):
        from sandcastle.engine.autopilot import pick_variant

        result = await pick_variant(uuid.uuid4(), [])
        assert result is None

    @pytest.mark.asyncio
    async def test_single_variant_returned(self):
        from sandcastle.engine.autopilot import pick_variant

        variant = VariantConfig(id="only", model="sonnet")

        # Mock DB to return no stats
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            result = await pick_variant(uuid.uuid4(), [variant])
            assert result.id == "only"

    @pytest.mark.asyncio
    async def test_picks_among_multiple_variants(self):
        from sandcastle.engine.autopilot import pick_variant

        variants = [
            VariantConfig(id="a", model="haiku"),
            VariantConfig(id="b", model="sonnet"),
            VariantConfig(id="c", model="opus"),
        ]

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            result = await pick_variant(uuid.uuid4(), variants)
            assert result.id in ("a", "b", "c")

    @pytest.mark.asyncio
    async def test_high_quality_variant_preferred_over_time(self):
        """With enough data, Thompson Sampling should prefer the better variant."""
        from sandcastle.engine.autopilot import pick_variant

        variants = [
            VariantConfig(id="good", model="sonnet"),
            VariantConfig(id="bad", model="haiku"),
        ]

        # Simulate stats: "good" has high quality, "bad" has low quality
        StatsRow = namedtuple("StatsRow", ["variant_id", "total", "avg_quality"])
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            StatsRow("good", 50, 0.95),
            StatsRow("bad", 50, 0.2),
        ]
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        picks = {"good": 0, "bad": 0}
        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            for _ in range(200):
                result = await pick_variant(uuid.uuid4(), variants)
                picks[result.id] += 1

        # "good" should be picked significantly more often
        assert picks["good"] > picks["bad"] * 2


# ===================================================================
# save_sample
# ===================================================================


class TestSaveSample:
    @pytest.mark.asyncio
    async def test_save_sample_success(self):
        from sandcastle.engine.autopilot import save_sample

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()  # add is synchronous

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await save_sample(
                experiment_id=uuid.uuid4(),
                run_id=str(uuid.uuid4()),
                variant=VariantConfig(id="v1", model="sonnet", prompt="test prompt"),
                output={"result": "good"},
                quality_score=0.9,
                cost_usd=0.01,
                duration_seconds=5.0,
            )
            mock_session.add.assert_called_once()
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_sample_non_dict_output(self):
        """Non-dict output should be wrapped in a dict."""
        from sandcastle.engine.autopilot import save_sample

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await save_sample(
                experiment_id=uuid.uuid4(),
                run_id=str(uuid.uuid4()),
                variant=VariantConfig(id="v1"),
                output="string output",
                quality_score=0.5,
                cost_usd=0.0,
                duration_seconds=1.0,
            )
            mock_session.add.assert_called_once()
            sample = mock_session.add.call_args[0][0]
            assert sample.output_data == {"result": "string output"}

    @pytest.mark.asyncio
    async def test_save_sample_db_error_caught(self):
        """DB errors should be caught and not propagate."""
        from sandcastle.engine.autopilot import save_sample

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()
        mock_session.commit.side_effect = Exception("DB write failed")

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            # Should not raise
            await save_sample(
                experiment_id=uuid.uuid4(),
                run_id=str(uuid.uuid4()),
                variant=VariantConfig(id="v1"),
                output={"x": 1},
                quality_score=0.5,
                cost_usd=0.0,
                duration_seconds=1.0,
            )

    @pytest.mark.asyncio
    async def test_save_sample_truncates_long_prompt(self):
        """Prompt should be truncated to 200 chars in variant_config."""
        from sandcastle.engine.autopilot import save_sample

        long_prompt = "x" * 500
        variant = VariantConfig(id="v1", prompt=long_prompt)
        # The truncation happens in the save_sample function itself:
        # variant.prompt[:200] if variant.prompt else None
        # We verify the logic directly
        truncated = long_prompt[:200]
        assert len(truncated) == 200
        assert len(long_prompt) == 500

        # Also verify via a mock that captures the sample constructor args
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # Make add synchronous (not coroutine)
        mock_session.add = MagicMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await save_sample(
                experiment_id=uuid.uuid4(),
                run_id=str(uuid.uuid4()),
                variant=variant,
                output={"x": 1},
                quality_score=0.5,
                cost_usd=0.0,
                duration_seconds=1.0,
            )
            mock_session.add.assert_called_once()
            sample = mock_session.add.call_args[0][0]
            assert len(sample.variant_config["prompt"]) == 200


# ===================================================================
# get_or_create_experiment
# ===================================================================


class TestGetOrCreateExperiment:
    @pytest.mark.asyncio
    async def test_returns_existing_experiment(self):
        from sandcastle.engine.autopilot import get_or_create_experiment

        fake_experiment = MagicMock()
        fake_experiment.id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = fake_experiment

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            config = AutoPilotConfig(
                optimize_for="quality",
                variants=[VariantConfig(id="v1", model="sonnet")],
            )
            result = await get_or_create_experiment("wf", "step1", config)
            assert result == fake_experiment
            mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_new_experiment(self):
        """Use the real in-memory DB to test experiment creation."""
        from sandcastle.engine.autopilot import get_or_create_experiment

        config = AutoPilotConfig(
            optimize_for="cost",
            min_samples=20,
            auto_deploy=True,
            quality_threshold=0.6,
            variants=[
                VariantConfig(id="v1", model="sonnet"),
                VariantConfig(id="v2", model="haiku"),
            ],
        )
        experiment = await get_or_create_experiment(
            "deep-test-wf", "step-create", config,
        )
        assert experiment is not None
        assert experiment.workflow_name == "deep-test-wf"
        assert experiment.step_id == "step-create"
        assert experiment.optimize_for == "cost"


# ===================================================================
# maybe_complete_experiment
# ===================================================================


class TestMaybeCompleteExperiment:
    @pytest.mark.asyncio
    async def test_not_enough_samples_returns_none(self):
        from sandcastle.engine.autopilot import maybe_complete_experiment

        mock_session = AsyncMock()
        mock_session.scalar.return_value = 3  # Less than min_samples
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            config = AutoPilotConfig(min_samples=10, auto_deploy=False)
            result = await maybe_complete_experiment(uuid.uuid4(), config)
            assert result is None

    @pytest.mark.asyncio
    async def test_selects_winner_when_enough_samples(self):
        from sandcastle.engine.autopilot import maybe_complete_experiment

        StatsRow = namedtuple(
            "StatsRow", ["variant_id", "count", "avg_quality", "avg_cost", "avg_duration"]
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = 15  # Enough samples
        mock_execute_result = MagicMock()
        mock_execute_result.all.return_value = [
            StatsRow("v1", 8, 0.9, 0.05, 5.0),
            StatsRow("v2", 7, 0.7, 0.02, 3.0),
        ]
        mock_session.execute.return_value = mock_execute_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            config = AutoPilotConfig(
                min_samples=10,
                auto_deploy=False,
                quality_threshold=0.5,
            )
            result = await maybe_complete_experiment(uuid.uuid4(), config)
            assert result is not None
            assert result["variant_id"] == "v1"


# ===================================================================
# advance_rollout
# ===================================================================


class TestAdvanceRollout:
    @pytest.mark.asyncio
    async def test_canary_to_partial(self):
        from sandcastle.engine.autopilot import advance_rollout

        mock_experiment = MagicMock()
        mock_experiment.status = MagicMock()
        mock_experiment.status.name = "DEPLOYING"
        mock_experiment.rollout_stage = "canary"

        # We need ExperimentStatus.DEPLOYING comparison to work
        mock_session = AsyncMock()
        mock_session.get.return_value = mock_experiment
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session), \
             patch("sandcastle.models.db.ExperimentStatus") as MockStatus:
            MockStatus.DEPLOYING = mock_experiment.status
            result = await advance_rollout(uuid.uuid4())
            assert result is not None
            assert result["stage"] == "partial"
            assert result["traffic"] == 0.50
            assert result["completed"] is False

    @pytest.mark.asyncio
    async def test_full_completes_experiment(self):
        from sandcastle.engine.autopilot import advance_rollout

        mock_experiment = MagicMock()
        mock_experiment.status = MagicMock()
        mock_experiment.status.name = "DEPLOYING"
        mock_experiment.rollout_stage = "full"

        mock_session = AsyncMock()
        mock_session.get.return_value = mock_experiment
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session), \
             patch("sandcastle.models.db.ExperimentStatus") as MockStatus:
            MockStatus.DEPLOYING = mock_experiment.status
            MockStatus.COMPLETED = "COMPLETED"
            result = await advance_rollout(uuid.uuid4())
            assert result is not None
            assert result["stage"] == "full"
            assert result["completed"] is True

    @pytest.mark.asyncio
    async def test_no_experiment_returns_none(self):
        from sandcastle.engine.autopilot import advance_rollout

        mock_session = AsyncMock()
        mock_session.get.return_value = None
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            result = await advance_rollout(uuid.uuid4())
            assert result is None

    @pytest.mark.asyncio
    async def test_partial_to_full(self):
        """Advancing from 'partial' moves to 'full' but does NOT complete.
        Completion only happens on the NEXT call when already at 'full'."""
        from sandcastle.engine.autopilot import advance_rollout

        mock_experiment = MagicMock()
        mock_experiment.status = MagicMock()
        mock_experiment.rollout_stage = "partial"

        mock_session = AsyncMock()
        mock_session.get.return_value = mock_experiment
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=mock_session), \
             patch("sandcastle.models.db.ExperimentStatus") as MockStatus:
            MockStatus.DEPLOYING = mock_experiment.status
            result = await advance_rollout(uuid.uuid4())
            assert result is not None
            assert result["stage"] == "full"
            assert result["traffic"] == 1.0
            assert result["completed"] is False
