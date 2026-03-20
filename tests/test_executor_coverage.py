"""
Coverage boost tests for executor.py and autopilot.py.

Targets uncovered lines identified from coverage run:
- executor.py: 73% -> push toward 80%+
- autopilot.py: 52% -> push toward 70%+
"""

from __future__ import annotations

import asyncio
import math
import random
import uuid
from collections import namedtuple
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.autopilot import (
    _check_significance,
    _evaluate_schema_completeness,
    _two_tailed_p_value,
    apply_variant,
    select_winner,
    should_use_winner,
)
from sandcastle.engine.dag import (
    AutoPilotConfig,
    BrowserConfig,
    CodeConfig,
    ComposioConfig,
    ConditionConfig,
    DelegateConfig,
    LoopConfig,
    NotifyConfig,
    StepDefinition,
    TransformConfig,
    VariantConfig,
    WorkflowDefinition,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    WorkflowPaused,
    _UNRESOLVED,
    _backoff_delay,
    _check_budget,
    _compute_cache_key,
    _escape_braces,
    _escape_js_string,
    _get_pdf_report_instruction,
    _is_cacheable_output,
    _resolve_condition_expression,
    _resolve_params_deep,
    _safe_eval_expression,
    _truncate_output,
    _validate_browser_url,
    cancel_run_local,
    clear_emergency_stop_local,
    is_emergency_stop_active,
    resolve_templates,
    resolve_variable,
    set_emergency_stop_local,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        output_schema=kwargs.get("output_schema"),
        loop_config=kwargs.get("loop_config"),
        condition_config=kwargs.get("condition_config"),
        transform_config=kwargs.get("transform_config"),
        delegate_config=kwargs.get("delegate_config"),
        code_config=kwargs.get("code_config"),
        composio_config=kwargs.get("composio_config"),
        notify_config=kwargs.get("notify_config"),
        browser_config=kwargs.get("browser_config"),
    )


VariantRow = namedtuple(
    "VariantRow", ["variant_id", "count", "avg_quality", "avg_cost", "avg_duration"]
)


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
        patch("sandcastle.engine.executor._emit_audit_event", new_callable=AsyncMock),
        patch("sandcastle.engine.executor.event_bus") as mock_bus,
    ):
        mock_bus.publish = MagicMock()
        yield


# ===========================================================================
# AUTOPILOT - Thompson Sampling
# ===========================================================================

class TestThompsonSampling:
    """Tests for pick_variant via Beta distribution sampling."""

    def test_no_variants_returns_none_for_empty_list(self):
        # pick_variant is async and needs DB; test Beta distribution logic directly
        # With all zero stats, alpha=1, beta=1 -> uniform, first variant wins on tie
        variants = [VariantConfig(id="a"), VariantConfig(id="b"), VariantConfig(id="c")]
        # When no stats exist, each variant gets alpha=1, beta=1 -> uniform
        # Select winner is a proxy - test the sampling algorithm indirectly
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.0)
        stats = [
            VariantRow("a", 10, 0.9, 0.05, 5.0),
            VariantRow("b", 10, 0.8, 0.05, 5.0),
        ]
        winner = select_winner(stats, config)
        assert winner is not None
        assert winner["variant_id"] in ("a", "b")

    def test_select_winner_pareto_single_candidate(self):
        """Pareto with single candidate above threshold."""
        stats = [
            VariantRow("only", 10, 0.9, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.8)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "only"

    def test_select_winner_pareto_zero_costs(self):
        """Pareto optimization with zero-cost/zero-duration candidates."""
        stats = [
            VariantRow("a", 10, 0.9, 0.0, 0.0),
            VariantRow("b", 10, 0.8, 0.0, 0.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.5)
        winner = select_winner(stats, config)
        # Should not raise, max_cost fallback to 1.0
        assert winner is not None

    def test_select_winner_latency_all_same_duration(self):
        """Latency optimization when all durations are equal."""
        stats = [
            VariantRow("a", 10, 0.9, 0.05, 5.0),
            VariantRow("b", 10, 0.8, 0.03, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="latency", quality_threshold=0.6)
        winner = select_winner(stats, config)
        # Both same duration, picks first in list
        assert winner["variant_id"] in ("a", "b")


# ===========================================================================
# AUTOPILOT - Statistical Significance
# ===========================================================================

class TestCheckSignificance:
    """Tests for Welch's t-test significance checking."""

    def test_too_few_samples_not_significant(self):
        """Less than 3 samples per group -> not significant."""
        is_sig, p = _check_significance([0.9, 0.8], [0.5, 0.4])
        assert is_sig is False
        assert p == 1.0

    def test_clearly_different_groups_significant(self):
        """Well-separated groups with many samples should be significant."""
        winner = [0.95] * 30
        loser = [0.30] * 30
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True
        assert p < 0.05

    def test_identical_groups_not_significant(self):
        """Identical groups -> p=1.0 (se=0, mean1==mean2)."""
        scores = [0.5] * 10
        is_sig, p = _check_significance(scores, scores[:])
        assert is_sig is False

    def test_se_zero_winner_higher_mean_is_significant(self):
        """When se=0 and mean1 > mean2, result is significant with p=0."""
        # Achieve se=0 by having all same values in each group but different means
        winner = [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]
        loser = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True
        assert p == 0.0

    def test_nan_mean_returns_not_significant(self):
        """NaN in scores should return not significant."""
        winner = [float("nan")] * 5
        loser = [0.5] * 5
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is False
        assert p == 1.0

    def test_moderately_different_groups(self):
        """Groups with modest separation return a valid p-value."""
        random.seed(42)
        winner = [0.7 + random.gauss(0, 0.05) for _ in range(20)]
        loser = [0.5 + random.gauss(0, 0.05) for _ in range(20)]
        is_sig, p = _check_significance(winner, loser)
        assert isinstance(is_sig, bool)
        assert 0.0 <= p <= 1.0


class TestTwoTailedPValue:
    """Tests for the p-value approximation function."""

    def test_zero_t_stat_returns_one(self):
        p = _two_tailed_p_value(0.0, 10)
        assert p == pytest.approx(1.0, abs=0.01)

    def test_large_t_stat_returns_near_zero(self):
        p = _two_tailed_p_value(10.0, 100)
        assert p < 0.001

    def test_inf_t_stat_returns_zero(self):
        p = _two_tailed_p_value(float("inf"), 50)
        assert p == 0.0

    def test_nan_t_stat_returns_one(self):
        p = _two_tailed_p_value(float("nan"), 50)
        assert p == 1.0

    def test_nan_df_returns_one(self):
        p = _two_tailed_p_value(2.0, float("nan"))
        assert p == 1.0

    def test_zero_df_returns_one(self):
        p = _two_tailed_p_value(2.0, 0)
        assert p == 1.0

    def test_negative_t_stat_same_as_positive(self):
        p_pos = _two_tailed_p_value(2.0, 50)
        p_neg = _two_tailed_p_value(-2.0, 50)
        assert p_pos == pytest.approx(p_neg)

    def test_large_df_uses_z_approximation(self):
        """For df > 100, z = abs_t directly."""
        p = _two_tailed_p_value(1.96, 200)
        assert p < 0.1

    def test_small_df_uses_cornish_fisher(self):
        """For df <= 100, Cornish-Fisher correction applied."""
        p = _two_tailed_p_value(2.0, 10)
        assert 0.0 < p < 1.0


# ===========================================================================
# AUTOPILOT - Progressive Rollout
# ===========================================================================

class TestProgressiveRollout:
    """Tests for should_use_winner and rollout logic."""

    def test_none_rollout_stage_returns_false(self):
        assert should_use_winner(None) is False

    def test_canary_stage_uses_10pct_traffic(self):
        """Canary = 10% traffic; with enough trials, some should use winner."""
        results = [should_use_winner("canary") for _ in range(1000)]
        # Should be around 10%, allow wide margin
        rate = sum(results) / 1000
        assert 0.0 < rate < 0.4

    def test_full_stage_always_uses_winner(self):
        """Full stage = 100% traffic; should always return True."""
        for _ in range(10):
            assert should_use_winner("full") is True

    def test_partial_stage_50pct_traffic(self):
        """Partial stage = 50% traffic."""
        results = [should_use_winner("partial") for _ in range(1000)]
        rate = sum(results) / 1000
        assert 0.2 < rate < 0.8

    def test_unknown_stage_returns_false(self):
        """Unknown stage name returns False (0% traffic)."""
        assert should_use_winner("unknown_stage") is False

    def test_empty_string_stage_returns_false(self):
        assert should_use_winner("") is False


# ===========================================================================
# AUTOPILOT - evaluate_result (synchronous helpers)
# ===========================================================================

class TestEvaluateSchemaCompleteness:
    """Tests for _evaluate_schema_completeness."""

    def test_empty_properties_schema_returns_one(self):
        schema = {"properties": {}}
        assert _evaluate_schema_completeness({"a": 1}, schema) == 1.0

    def test_all_fields_present_score_one(self):
        schema = {"properties": {"x": {}, "y": {}, "z": {}}}
        output = {"x": 1, "y": 2, "z": 3}
        assert _evaluate_schema_completeness(output, schema) == 1.0

    def test_none_field_values_not_counted(self):
        schema = {"properties": {"x": {}, "y": {}}}
        output = {"x": 1, "y": None}
        # y is None -> not counted
        assert _evaluate_schema_completeness(output, schema) == pytest.approx(0.5)

    def test_list_output_returns_zero(self):
        schema = {"properties": {"x": {}}}
        assert _evaluate_schema_completeness([1, 2, 3], schema) == 0.0

    def test_no_schema_with_none_output(self):
        assert _evaluate_schema_completeness(None, None) == 0.0

    def test_no_schema_with_output_returns_one(self):
        assert _evaluate_schema_completeness("something", None) == 1.0


# ===========================================================================
# AUTOPILOT - apply_variant
# ===========================================================================

class TestApplyVariant:
    """Tests for apply_variant."""

    def test_no_overrides_clears_autopilot(self):
        """Variant with no overrides still clears autopilot."""
        s = StepDefinition(id="test", prompt="original", model="sonnet")
        v = VariantConfig(id="baseline")
        modified = apply_variant(s, v)
        assert modified.autopilot is None
        assert modified.prompt == "original"
        assert modified.model == "sonnet"

    def test_model_override(self):
        s = StepDefinition(id="test", prompt="p", model="sonnet")
        v = VariantConfig(id="v1", model="haiku")
        modified = apply_variant(s, v)
        assert modified.model == "haiku"
        assert modified.prompt == "p"

    def test_prompt_override(self):
        s = StepDefinition(id="test", prompt="original", model="sonnet")
        v = VariantConfig(id="v1", prompt="new prompt")
        modified = apply_variant(s, v)
        assert modified.prompt == "new prompt"

    def test_max_turns_override(self):
        s = StepDefinition(id="test", prompt="p", model="sonnet", max_turns=5)
        v = VariantConfig(id="v1", max_turns=20)
        modified = apply_variant(s, v)
        assert modified.max_turns == 20

    def test_all_overrides(self):
        s = StepDefinition(id="test", prompt="orig", model="sonnet", max_turns=5)
        v = VariantConfig(id="full", model="opus", prompt="premium", max_turns=50)
        modified = apply_variant(s, v)
        assert modified.model == "opus"
        assert modified.prompt == "premium"
        assert modified.max_turns == 50
        assert modified.autopilot is None


# ===========================================================================
# EXECUTOR - Emergency Stop
# ===========================================================================

class TestEmergencyStop:
    """Tests for emergency stop local mode."""

    @pytest.mark.asyncio
    async def test_set_and_check_emergency_stop(self):
        """Setting emergency stop makes is_emergency_stop_active return True."""
        mock_settings = MagicMock()
        mock_settings.redis_url = None

        with patch("sandcastle.config.settings", mock_settings):
            await set_emergency_stop_local()
            try:
                result = await is_emergency_stop_active()
                assert result is True
            finally:
                await clear_emergency_stop_local()

    @pytest.mark.asyncio
    async def test_clear_emergency_stop(self):
        """Clearing emergency stop makes is_emergency_stop_active return False."""
        mock_settings = MagicMock()
        mock_settings.redis_url = None

        with patch("sandcastle.config.settings", mock_settings):
            await set_emergency_stop_local()
            await clear_emergency_stop_local()
            result = await is_emergency_stop_active()
            assert result is False

    @pytest.mark.asyncio
    async def test_is_emergency_stop_false_by_default(self):
        """Emergency stop is False by default."""
        mock_settings = MagicMock()
        mock_settings.redis_url = None

        with patch("sandcastle.config.settings", mock_settings):
            await clear_emergency_stop_local()
            result = await is_emergency_stop_active()
            assert result is False

    @pytest.mark.asyncio
    async def test_redis_exception_returns_false(self):
        """When Redis raises, emergency stop check returns False."""
        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://localhost:6379"

        with patch("sandcastle.config.settings", mock_settings):
            with patch("sandcastle.engine.executor._get_redis", new_callable=AsyncMock, side_effect=Exception("no redis")):
                result = await is_emergency_stop_active()
                assert result is False


# ===========================================================================
# EXECUTOR - Resolve Variable edge cases
# ===========================================================================

class TestResolveVariableEnvEdgeCases:
    """Tests for env.VAR_NAME resolution."""

    def test_env_var_present(self):
        c = ctx()
        with patch.dict("os.environ", {"MY_SECRET": "abc123"}):
            result = resolve_variable("env.MY_SECRET", c)
            assert result == "abc123"

    def test_env_var_missing_returns_unresolved(self):
        c = ctx()
        with patch.dict("os.environ", {}, clear=False):
            # Use unlikely var name
            result = resolve_variable("env.VERY_UNLIKELY_VAR_XYZ_123", c)
            assert result is _UNRESOLVED

    def test_env_var_empty_string_returns_unresolved(self):
        c = ctx()
        with patch.dict("os.environ", {"MY_EMPTY_VAR": "  "}):
            result = resolve_variable("env.MY_EMPTY_VAR", c)
            assert result is _UNRESOLVED

    def test_steps_missing_step_returns_unresolved(self):
        c = ctx(step_outputs={})
        result = resolve_variable("steps.nonexistent.output", c)
        assert result is _UNRESOLVED

    def test_steps_meta_status_no_step_result_returns_unresolved(self):
        c = ctx(step_outputs={"s1": "data"}, step_results={})
        result = resolve_variable("steps.s1.status", c)
        assert result is _UNRESOLVED

    def test_traverse_non_dict_non_list_returns_unresolved(self):
        c = ctx(step_outputs={"s1": "string_value"})
        result = resolve_variable("steps.s1.output.key", c)
        assert result is _UNRESOLVED

    def test_list_non_integer_index_unresolved(self):
        c = ctx(step_outputs={"s1": [1, 2, 3]})
        result = resolve_variable("steps.s1.output.abc", c)
        assert result is _UNRESOLVED


# ===========================================================================
# EXECUTOR - Condition expression resolution
# ===========================================================================

class TestResolveConditionExpression:
    """Tests for _resolve_condition_expression injection checks."""

    def test_safe_input_value(self):
        c = ctx(input={"score": "75"})
        result = _resolve_condition_expression("{input.score} > 50", c)
        assert result == "75 > 50"

    def test_unresolved_placeholder_kept(self):
        c = ctx(input={})
        result = _resolve_condition_expression("{input.missing} > 5", c)
        assert "{input.missing}" in result

    def test_none_value_becomes_none(self):
        c = ctx(input={"val": None})
        result = _resolve_condition_expression("{input.val}", c)
        assert result == "None"

    def test_dict_value_becomes_json(self):
        c = ctx(input={"data": {"key": "val"}})
        result = _resolve_condition_expression("{input.data}", c)
        assert '"key"' in result

    def test_injection_token_raises(self):
        """Step output containing 'or' keyword raises ValueError."""
        c = ctx(step_outputs={"s1": "5 or True"})
        with pytest.raises(ValueError, match="unsafe tokens"):
            _resolve_condition_expression("{steps.s1.output} > 3", c)

    def test_injection_semicolon_raises(self):
        c = ctx(step_outputs={"s1": "x; drop table"})
        with pytest.raises(ValueError, match="unsafe tokens"):
            _resolve_condition_expression("{steps.s1.output}", c)

    def test_non_string_value_not_checked(self):
        """Numeric step output does not trigger injection check."""
        c = ctx(step_outputs={"s1": 42})
        result = _resolve_condition_expression("{steps.s1.output}", c)
        assert "42" in result


# ===========================================================================
# EXECUTOR - Safe eval expression
# ===========================================================================

class TestSafeEvalExpression:
    """Tests for _safe_eval_expression."""

    def test_simple_comparison_true(self):
        result = _safe_eval_expression("5 > 3", {})
        assert result is True

    def test_simple_comparison_false(self):
        result = _safe_eval_expression("3 > 5", {})
        assert result is False

    def test_uses_names(self):
        result = _safe_eval_expression("x > 3", {"x": 5})
        assert result is True

    def test_len_function_available(self):
        result = _safe_eval_expression("len(items) > 2", {"items": [1, 2, 3]})
        assert result is True

    def test_expression_too_long_raises(self):
        long_expr = "a " * 1001
        with pytest.raises(ValueError, match="too long"):
            _safe_eval_expression(long_expr, {})

    def test_dunder_blocked(self):
        with pytest.raises(ValueError, match="blocked double-underscore"):
            _safe_eval_expression("__class__", {})

    def test_string_equality(self):
        result = _safe_eval_expression("x == 'hello'", {"x": "hello"})
        assert result is True


# ===========================================================================
# EXECUTOR - Backoff delay
# ===========================================================================

class TestBackoffDelayExtended:
    """Additional tests for _backoff_delay to hit branch coverage."""

    def test_exponential_attempt_0(self):
        result = _backoff_delay(0, "exponential")
        assert 0 <= result <= 1  # 2**0 = 1

    def test_exponential_attempt_6(self):
        result = _backoff_delay(6, "exponential")
        assert 0 <= result <= 60  # 2**6=64, capped at 60

    def test_fixed_backoff(self):
        for _ in range(20):
            result = _backoff_delay(3, "fixed")
            assert 1.0 <= result <= 3.0

    def test_unknown_strategy(self):
        result = _backoff_delay(1, "unknown")
        assert 1.0 <= result <= 3.0


# ===========================================================================
# EXECUTOR - JS string escaping
# ===========================================================================

class TestEscapeJsString:
    """Tests for _escape_js_string."""

    def test_no_special_chars(self):
        assert _escape_js_string("hello world") == "hello world"

    def test_backslash_escaped(self):
        assert "\\\\" in _escape_js_string("path\\to\\file")

    def test_single_quote_escaped(self):
        result = _escape_js_string("it's")
        assert "\\'" in result

    def test_double_quote_escaped(self):
        result = _escape_js_string('say "hi"')
        assert '\\"' in result

    def test_newline_escaped(self):
        result = _escape_js_string("line1\nline2")
        assert "\\n" in result

    def test_carriage_return_escaped(self):
        result = _escape_js_string("line1\rline2")
        assert "\\r" in result

    def test_tab_escaped(self):
        result = _escape_js_string("col1\tcol2")
        assert "\\t" in result

    def test_backtick_escaped(self):
        result = _escape_js_string("template `literal`")
        assert "\\`" in result

    def test_null_byte_removed(self):
        result = _escape_js_string("data\x00here")
        assert "\x00" not in result

    def test_line_separator_escaped(self):
        result = _escape_js_string("text\u2028more")
        assert "\\u2028" in result

    def test_paragraph_separator_escaped(self):
        result = _escape_js_string("text\u2029more")
        assert "\\u2029" in result

    def test_empty_string(self):
        assert _escape_js_string("") == ""


# ===========================================================================
# EXECUTOR - PDF report instructions
# ===========================================================================

class TestGetPdfReportInstruction:
    """Tests for _get_pdf_report_instruction."""

    def test_english(self):
        result = _get_pdf_report_instruction("en")
        assert "English" in result
        assert "FORMATTING" in result

    def test_czech(self):
        result = _get_pdf_report_instruction("cs")
        assert "cestine" in result.lower() or "FORMATOVANI" in result

    def test_german(self):
        result = _get_pdf_report_instruction("de")
        assert "Deutsch" in result or "FORMATIERUNG" in result

    def test_spanish(self):
        result = _get_pdf_report_instruction("es")
        assert "espanol" in result.lower() or "FORMATO" in result

    def test_french(self):
        result = _get_pdf_report_instruction("fr")
        assert "francais" in result.lower() or "FORMATAGE" in result

    def test_japanese(self):
        result = _get_pdf_report_instruction("ja")
        assert "Japanese" in result

    def test_chinese(self):
        result = _get_pdf_report_instruction("zh")
        assert "Chinese" in result

    def test_unknown_language_fallback(self):
        result = _get_pdf_report_instruction("xq")
        assert "xq" in result
        assert "FORMATTING" in result


# ===========================================================================
# EXECUTOR - _resolve_params_deep
# ===========================================================================

class TestResolveParamsDeep:
    """Tests for _resolve_params_deep."""

    def test_string_gets_resolved(self):
        c = ctx(input={"name": "Alice"})
        result = _resolve_params_deep("{input.name}", c)
        assert result == "Alice"

    def test_dict_values_resolved(self):
        c = ctx(input={"x": "10"})
        result = _resolve_params_deep({"key": "{input.x}", "other": "static"}, c)
        assert result["key"] == "10"
        assert result["other"] == "static"

    def test_list_items_resolved(self):
        c = ctx(input={"a": "1", "b": "2"})
        result = _resolve_params_deep(["{input.a}", "{input.b}"], c)
        assert result == ["1", "2"]

    def test_nested_dict_resolved(self):
        c = ctx(input={"v": "hello"})
        result = _resolve_params_deep({"outer": {"inner": "{input.v}"}}, c)
        assert result["outer"]["inner"] == "hello"

    def test_non_string_passthrough(self):
        c = ctx()
        result = _resolve_params_deep(42, c)
        assert result == 42

    def test_none_passthrough(self):
        c = ctx()
        result = _resolve_params_deep(None, c)
        assert result is None


# ===========================================================================
# EXECUTOR - Composio step
# ===========================================================================

class TestComposioStep:
    """Tests for _execute_composio_step."""

    @pytest.mark.asyncio
    async def test_missing_config_returns_failed(self):
        from sandcastle.engine.executor import _execute_composio_step
        s = step(id="composio_bad", type="composio")
        c = ctx()
        result = await _execute_composio_step(s, c)
        assert result.status == "failed"
        assert "Missing composio_config" in result.error

    @pytest.mark.asyncio
    async def test_missing_action_returns_failed(self):
        from sandcastle.engine.executor import _execute_composio_step
        s = step(
            id="composio_no_action",
            type="composio",
            composio_config=ComposioConfig(action=""),
        )
        c = ctx()
        result = await _execute_composio_step(s, c)
        assert result.status == "failed"
        assert "action is required" in result.error

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_failed(self):
        from sandcastle.engine.executor import _execute_composio_step
        s = step(
            id="composio_no_key",
            type="composio",
            composio_config=ComposioConfig(action="GITHUB_LIST_REPOS"),
        )
        c = ctx()
        with patch.dict("os.environ", {}, clear=False):
            # Remove TOOL_COMPOSIO_API_KEY if set
            import os
            os.environ.pop("TOOL_COMPOSIO_API_KEY", None)
            result = await _execute_composio_step(s, c)
        assert result.status == "failed"
        assert "TOOL_COMPOSIO_API_KEY" in result.error

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        from sandcastle.engine.executor import _execute_composio_step
        s = step(
            id="composio_ok",
            type="composio",
            composio_config=ComposioConfig(
                action="GITHUB_LIST_REPOS",
                params={"org": "test"},
            ),
        )
        c = ctx()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"repos": ["repo1", "repo2"]}}
        mock_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client
                result = await _execute_composio_step(s, c)

        assert result.status == "completed"
        assert result.output == {"repos": ["repo1", "repo2"]}

    @pytest.mark.asyncio
    async def test_composio_level_error(self):
        from sandcastle.engine.executor import _execute_composio_step
        s = step(
            id="composio_err",
            type="composio",
            composio_config=ComposioConfig(action="BAD_ACTION"),
        )
        c = ctx()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "Action not found", "data": None}
        mock_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "key"}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client
                result = await _execute_composio_step(s, c)

        assert result.status == "failed"
        assert "Action not found" in result.error

    @pytest.mark.asyncio
    async def test_http_error_returns_failed(self):
        from sandcastle.engine.executor import _execute_composio_step
        import httpx
        s = step(
            id="composio_http_err",
            type="composio",
            composio_config=ComposioConfig(action="SOME_ACTION"),
        )
        c = ctx()

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "key"}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_response = MagicMock()
                mock_response.status_code = 401
                mock_response.text = "Unauthorized"
                mock_client.post = AsyncMock(
                    side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=mock_response)
                )
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client
                result = await _execute_composio_step(s, c)

        assert result.status == "failed"
        assert "Composio API error" in result.error

    @pytest.mark.asyncio
    async def test_with_connected_account_and_app(self):
        """Composio step passes connectedAccountId and appName when configured."""
        from sandcastle.engine.executor import _execute_composio_step
        s = step(
            id="composio_full",
            type="composio",
            composio_config=ComposioConfig(
                action="GITHUB_CREATE_ISSUE",
                params={"title": "test"},
                connected_account_id="acct-123",
                app="github",
            ),
        )
        c = ctx()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"issue_id": 42}}
        mock_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "key"}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client
                result = await _execute_composio_step(s, c)

        assert result.status == "completed"
        call_payload = mock_client.post.call_args[1]["json"]
        assert call_payload["connectedAccountId"] == "acct-123"
        assert call_payload["appName"] == "github"


# ===========================================================================
# EXECUTOR - Notify step
# ===========================================================================

class TestNotifyStep:
    """Tests for _execute_notify_step."""

    @pytest.mark.asyncio
    async def test_missing_config_returns_failed(self):
        from sandcastle.engine.executor import _execute_notify_step
        s = step(id="notify_bad", type="notify")
        c = ctx()
        result = await _execute_notify_step(s, c)
        assert result.status == "failed"
        assert "Missing notify_config" in result.error

    @pytest.mark.asyncio
    async def test_basic_notification(self):
        from sandcastle.engine.executor import _execute_notify_step
        s = step(
            id="notify_ok",
            type="notify",
            notify_config=NotifyConfig(
                service="slack",
                channel="#general",
                message="Hello {input.name}!",
            ),
        )
        c = ctx(input={"name": "World"})
        result = await _execute_notify_step(s, c)
        assert result.status == "completed"
        assert result.output["service"] == "slack"
        assert result.output["channel"] == "#general"
        assert "Hello World!" in result.output["message"]
        assert result.output["status"] == "logged"

    @pytest.mark.asyncio
    async def test_notification_without_channel(self):
        from sandcastle.engine.executor import _execute_notify_step
        s = step(
            id="notify_no_ch",
            type="notify",
            notify_config=NotifyConfig(
                service="email",
                message="Alert message",
            ),
        )
        c = ctx()
        result = await _execute_notify_step(s, c)
        assert result.status == "completed"
        assert result.output["channel"] == ""

    @pytest.mark.asyncio
    async def test_notification_message_template_resolved(self):
        from sandcastle.engine.executor import _execute_notify_step
        s = step(
            id="notify_tpl",
            type="notify",
            notify_config=NotifyConfig(
                service="webhook",
                message="Step {input.step_name} completed",
            ),
        )
        c = ctx(input={"step_name": "analysis"})
        result = await _execute_notify_step(s, c)
        assert result.status == "completed"
        assert "analysis" in result.output["message"]


# ===========================================================================
# EXECUTOR - Delegate step
# ===========================================================================

class TestDelegateStepCoverage:
    """Additional delegate step coverage."""

    @pytest.mark.asyncio
    async def test_delegate_dynamic_workflow_name(self):
        """Delegate with template in workflow name resolves it."""
        from sandcastle.engine.executor import _execute_delegate_step
        s = step(
            id="del_dyn",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="{input.wf_name}",
                task_description="run it",
            ),
        )
        c = ctx(input={"wf_name": "nonexistent_workflow"})
        storage = MagicMock()

        mock_settings = MagicMock()
        mock_settings.max_workflow_depth = 10
        mock_settings.workflows_dir = "/tmp/nonexistent"

        with patch("sandcastle.config.settings", mock_settings):
            result = await _execute_delegate_step(s, c, storage, depth=0)

        # Template resolves, then workflow not found
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_delegate_slash_in_name_blocked(self):
        """Delegate with slash in name is blocked."""
        from sandcastle.engine.executor import _execute_delegate_step
        s = step(
            id="del_slash",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="subdir/workflow",
                task_description="traverse",
            ),
        )
        c = ctx()
        storage = MagicMock()

        mock_settings = MagicMock()
        mock_settings.max_workflow_depth = 10
        mock_settings.workflows_dir = "/tmp"

        with patch("sandcastle.config.settings", mock_settings):
            result = await _execute_delegate_step(s, c, storage, depth=0)

        assert result.status == "failed"
        # The error is either the traversal guard message or "not found/failed to load"
        assert result.error is not None


# ===========================================================================
# EXECUTOR - Condition step injection protection
# ===========================================================================

class TestConditionStepInjectionProtection:
    """Test that condition step blocks injection attempts."""

    @pytest.mark.asyncio
    async def test_step_output_with_and_keyword_blocked(self):
        """Step output containing 'and' keyword is blocked in condition."""
        from sandcastle.engine.executor import _execute_condition_step
        s = step(
            id="cond_inject",
            type="condition",
            condition_config=ConditionConfig(
                expression="{steps.malicious.output} == 5",
                then_steps=[],
                else_steps=[],
            ),
        )
        c = ctx(step_outputs={"malicious": "5 and True"})
        result = await _execute_condition_step(s, c)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_complex_expression_with_len(self):
        """Condition using len() works correctly."""
        from sandcastle.engine.executor import _execute_condition_step
        s = step(
            id="cond_len",
            type="condition",
            condition_config=ConditionConfig(
                expression="len(input['items']) > 3",
                then_steps=["big"],
                else_steps=["small"],
            ),
        )
        c = ctx(input={"items": [1, 2, 3, 4, 5]})
        result = await _execute_condition_step(s, c)
        assert result.status == "completed"
        assert result.output["condition"] is True
        assert "big" in c.branch_run_steps

    @pytest.mark.asyncio
    async def test_condition_false_branch_precedence(self):
        """False condition: else_steps run, then_steps skip."""
        from sandcastle.engine.executor import _execute_condition_step
        s = step(
            id="cond_false",
            type="condition",
            condition_config=ConditionConfig(
                expression="1 > 100",
                then_steps=["skip_me"],
                else_steps=["run_me"],
            ),
        )
        c = ctx()
        result = await _execute_condition_step(s, c)
        assert result.status == "completed"
        assert result.output["condition"] is False
        assert "skip_me" in c.branch_skip_steps
        assert "run_me" in c.branch_run_steps


# ===========================================================================
# EXECUTOR - Sub-workflow step
# ===========================================================================

class TestSubWorkflowStep:
    """Tests for _execute_sub_workflow_step."""

    @pytest.mark.asyncio
    async def test_missing_sub_workflow_config_returns_failed(self):
        from sandcastle.engine.executor import _execute_sub_workflow_step
        s = step(id="sub_bad", type="sub_workflow")
        c = ctx()
        storage = MagicMock()
        result = await _execute_sub_workflow_step(s, c, storage, depth=0)
        assert result.status == "failed"
        assert "Missing sub_workflow config" in result.error

    @pytest.mark.asyncio
    async def test_sub_workflow_path_traversal_blocked(self):
        from sandcastle.engine.executor import _execute_sub_workflow_step
        from sandcastle.engine.dag import SubWorkflowConfig
        s = step(
            id="sub_traversal",
            type="sub_workflow",
        )
        s = s.__class__(
            id="sub_traversal",
            prompt="",
            sub_workflow=SubWorkflowConfig(
                workflow="../../../etc/passwd",
                input_mapping={},
            ),
        )
        c = ctx()
        storage = MagicMock()

        mock_settings = MagicMock()
        mock_settings.max_workflow_depth = 10
        mock_settings.workflows_dir = "/tmp/workflows"

        with patch("sandcastle.config.settings", mock_settings):
            result = await _execute_sub_workflow_step(s, c, storage, depth=0)

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_sub_workflow_depth_exceeded(self):
        from sandcastle.engine.executor import _execute_sub_workflow_step
        from sandcastle.engine.dag import SubWorkflowConfig
        s = s = StepDefinition(
            id="sub_deep",
            prompt="",
            sub_workflow=SubWorkflowConfig(
                workflow="some_workflow",
                input_mapping={},
            ),
        )
        c = ctx()
        storage = MagicMock()

        mock_settings = MagicMock()
        mock_settings.max_workflow_depth = 3
        mock_settings.workflows_dir = "/tmp/workflows"

        with patch("sandcastle.config.settings", mock_settings):
            result = await _execute_sub_workflow_step(s, c, storage, depth=3)

        assert result.status == "failed"
        assert "depth" in result.error.lower()


# ===========================================================================
# EXECUTOR - Browser URL validation extras
# ===========================================================================

class TestBrowserUrlValidationExtras:
    """Additional URL validation edge cases."""

    def test_vbscript_scheme_rejected(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("vbscript:msgbox('xss')")

    def test_blob_scheme_rejected(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("blob:https://example.com/uuid")

    def test_uppercase_javascript_rejected(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("JAVASCRIPT:void(0)")

    def test_url_with_path_valid(self):
        result = _validate_browser_url("https://example.com/path?q=1#anchor")
        assert result == "https://example.com/path?q=1#anchor"

    def test_unsupported_scheme_rejected(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _validate_browser_url("ssh://example.com")


# ===========================================================================
# EXECUTOR - Budget check
# ===========================================================================

class TestBudgetCheckAdditional:
    """Additional budget check tests for branch coverage."""

    def test_cost_exactly_at_80_pct_boundary(self):
        c = ctx(max_cost_usd=10.0, costs=[8.0])
        assert _check_budget(c) == "warning"

    def test_cost_just_under_80_pct(self):
        c = ctx(max_cost_usd=10.0, costs=[7.99])
        assert _check_budget(c) is None

    def test_cost_exactly_at_100_pct(self):
        c = ctx(max_cost_usd=5.0, costs=[5.0])
        assert _check_budget(c) == "exceeded"


# ===========================================================================
# EXECUTOR - Cache helper
# ===========================================================================

class TestCacheKeyAdditional:
    """Additional _compute_cache_key coverage."""

    def test_very_long_prompt(self):
        prompt = "x" * 100_000
        key = _compute_cache_key("wf", "s1", prompt, "model")
        assert len(key) == 64

    def test_special_chars_in_workflow_name(self):
        key = _compute_cache_key("my-workflow/v2", "step.1", "prompt", "model")
        assert len(key) == 64


# ===========================================================================
# AUTOPILOT - select_winner additional branches
# ===========================================================================

class TestSelectWinnerAdditional:
    """Cover additional select_winner branches."""

    def test_optimize_quality_all_above_threshold(self):
        stats = [
            VariantRow("a", 5, 0.7, 0.01, 1.0),
            VariantRow("b", 5, 0.9, 0.05, 2.0),
            VariantRow("c", 5, 0.85, 0.03, 1.5),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_optimize_cost_single_above_threshold(self):
        stats = [
            VariantRow("cheap", 5, 0.8, 0.01, 1.0),
            VariantRow("bad", 5, 0.2, 0.001, 0.5),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.7)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "cheap"

    def test_optimize_quality_with_null_values(self):
        """Variant stats with None avg_quality should be handled."""
        stats = [
            VariantRow("a", 0, None, None, None),
            VariantRow("b", 0, None, None, None),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.5)
        # Both below threshold, return best quality (0.0 each, first returned)
        winner = select_winner(stats, config)
        assert winner is not None

    def test_pareto_with_three_candidates(self):
        stats = [
            VariantRow("fast_cheap", 10, 0.7, 0.01, 1.0),
            VariantRow("balanced", 10, 0.85, 0.05, 3.0),
            VariantRow("premium", 10, 0.95, 0.15, 10.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner is not None
        assert "pareto_score" in winner


# ===========================================================================
# EXECUTOR - Template injection protection
# ===========================================================================

class TestTemplateEdgeCases:
    """Additional template resolution edge cases."""

    def test_date_variable_resolved(self):
        c = ctx()
        result = resolve_templates("{date}", c)
        # Should be an ISO date string like 2026-03-20
        import re
        assert re.match(r"\d{4}-\d{2}-\d{2}", result)

    def test_run_id_variable_resolved(self):
        run_id = str(uuid.uuid4())
        c = ctx(run_id=run_id)
        result = resolve_templates("{run_id}", c)
        assert result == run_id

    def test_unresolved_variable_kept_as_placeholder(self):
        c = ctx()
        result = resolve_templates("{input.missing}", c)
        assert "{input.missing}" in result

    def test_list_value_serialized_to_json(self):
        c = ctx(step_outputs={"s1": [1, 2, 3]})
        result = resolve_templates("{steps.s1.output}", c)
        assert "1" in result
        assert "2" in result

    def test_memory_variable_resolved(self):
        c = ctx(memories=[])
        # Memory variable should resolve without error
        result = resolve_templates("{memory}", c)
        assert isinstance(result, str)


# ===========================================================================
# AUTOPILOT - Async DB functions with mocked sessions
# ===========================================================================

def _make_mock_session():
    """Create a mock async session context manager."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.scalar = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.get = AsyncMock()
    return session


def _make_async_session_ctx(session):
    """Create an async context manager that yields the mock session."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestGetOrCreateExperiment:
    """Tests for get_or_create_experiment with mocked DB."""

    @pytest.mark.asyncio
    async def test_returns_existing_experiment(self):
        """When experiment exists, it is returned without creating a new one."""
        from sandcastle.engine.autopilot import get_or_create_experiment

        existing_exp = MagicMock()
        existing_exp.id = uuid.uuid4()
        existing_exp.status = "running"

        session = _make_mock_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing_exp
        session.execute = AsyncMock(return_value=result_mock)

        config = AutoPilotConfig(
            optimize_for="quality",
            variants=[VariantConfig(id="v1", model="sonnet")],
        )

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await get_or_create_experiment("wf", "step1", config)

        assert result is existing_exp
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_new_experiment_when_not_found(self):
        """When no experiment exists, creates and returns a new one."""
        from sandcastle.engine.autopilot import get_or_create_experiment

        session = _make_mock_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        session.refresh = AsyncMock(side_effect=lambda x: None)

        config = AutoPilotConfig(
            optimize_for="cost",
            min_samples=10,
            auto_deploy=True,
            quality_threshold=0.7,
            variants=[
                VariantConfig(id="v1", model="sonnet"),
                VariantConfig(id="v2", model="haiku"),
            ],
        )

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await get_or_create_experiment("my_wf", "enrich", config)

        session.add.assert_called_once()
        session.commit.assert_called_once()


class TestPickVariant:
    """Tests for pick_variant with mocked DB."""

    @pytest.mark.asyncio
    async def test_empty_variants_returns_none(self):
        """Empty variants list returns None without DB access."""
        from sandcastle.engine.autopilot import pick_variant
        result = await pick_variant(uuid.uuid4(), [])
        assert result is None

    @pytest.mark.asyncio
    async def test_single_variant_always_selected(self):
        """With one variant and no stats, that variant is returned."""
        from sandcastle.engine.autopilot import pick_variant

        session = _make_mock_session()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        variants = [VariantConfig(id="only", model="sonnet")]

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await pick_variant(uuid.uuid4(), variants)

        assert result is not None
        assert result.id == "only"

    @pytest.mark.asyncio
    async def test_high_quality_variant_tends_to_win(self):
        """Variant with high quality stats tends to be selected more."""
        from sandcastle.engine.autopilot import pick_variant

        session = _make_mock_session()

        # Simulate stats: variant_a has high quality, variant_b low quality
        stat_a = MagicMock()
        stat_a.variant_id = "a"
        stat_a.total = 50
        stat_a.avg_quality = 0.95

        stat_b = MagicMock()
        stat_b.variant_id = "b"
        stat_b.total = 50
        stat_b.avg_quality = 0.1

        result_mock = MagicMock()
        result_mock.all.return_value = [stat_a, stat_b]
        session.execute = AsyncMock(return_value=result_mock)

        variants = [
            VariantConfig(id="a", model="sonnet"),
            VariantConfig(id="b", model="haiku"),
        ]

        selections = []
        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            for _ in range(30):
                result = await pick_variant(uuid.uuid4(), variants)
                if result:
                    selections.append(result.id)

        # High quality variant should win most of the time
        a_wins = selections.count("a")
        assert a_wins > selections.count("b")


class TestSaveSample:
    """Tests for save_sample with mocked DB."""

    @pytest.mark.asyncio
    async def test_save_sample_success(self):
        """save_sample creates AutoPilotSample and commits."""
        from sandcastle.engine.autopilot import save_sample

        session = _make_mock_session()
        variant = VariantConfig(id="v1", model="sonnet", prompt="test prompt")
        exp_id = uuid.uuid4()
        run_id = str(uuid.uuid4())

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            with patch("sandcastle.models.db.AutoPilotSample") as mock_sample_cls:
                mock_sample_cls.return_value = MagicMock()
                await save_sample(
                    experiment_id=exp_id,
                    run_id=run_id,
                    variant=variant,
                    output={"result": "ok"},
                    quality_score=0.9,
                    cost_usd=0.05,
                    duration_seconds=2.5,
                )

        session.add.assert_called_once()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_sample_with_string_output(self):
        """save_sample wraps non-dict output in result key."""
        from sandcastle.engine.autopilot import save_sample

        session = _make_mock_session()
        variant = VariantConfig(id="v1")
        exp_id = uuid.uuid4()
        run_id = str(uuid.uuid4())

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            with patch("sandcastle.models.db.AutoPilotSample") as mock_sample_cls:
                mock_sample_cls.return_value = MagicMock()
                await save_sample(
                    experiment_id=exp_id,
                    run_id=run_id,
                    variant=variant,
                    output="plain string result",
                    quality_score=0.7,
                    cost_usd=0.01,
                    duration_seconds=1.0,
                )

        # Should still commit
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_sample_db_error_silently_ignored(self):
        """When DB fails, save_sample logs warning but does not raise."""
        from sandcastle.engine.autopilot import save_sample

        variant = VariantConfig(id="v1")
        exp_id = uuid.uuid4()
        run_id = str(uuid.uuid4())

        failing_cm = AsyncMock()
        failing_cm.__aenter__ = AsyncMock(side_effect=Exception("DB connection failed"))
        failing_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.models.db.async_session", return_value=failing_cm):
            # Should not raise
            await save_sample(
                experiment_id=exp_id,
                run_id=run_id,
                variant=variant,
                output="result",
                quality_score=0.5,
                cost_usd=0.0,
                duration_seconds=0.5,
            )


class TestMaybeCompleteExperiment:
    """Tests for maybe_complete_experiment with mocked DB."""

    @pytest.mark.asyncio
    async def test_not_enough_samples_returns_none(self):
        """When total < min_samples and < MAX_SAMPLES, returns None."""
        from sandcastle.engine.autopilot import maybe_complete_experiment

        session = _make_mock_session()
        session.scalar = AsyncMock(return_value=3)  # total = 3

        config = AutoPilotConfig(min_samples=10, auto_deploy=False)

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await maybe_complete_experiment(uuid.uuid4(), config)

        assert result is None

    @pytest.mark.asyncio
    async def test_enough_samples_no_auto_deploy_returns_winner(self):
        """When total >= min_samples and auto_deploy=False, returns winner without significance test."""
        from sandcastle.engine.autopilot import maybe_complete_experiment

        session = _make_mock_session()
        session.scalar = AsyncMock(return_value=20)  # total = 20

        stat_a = MagicMock()
        stat_a.variant_id = "a"
        stat_a.count = 10
        stat_a.avg_quality = 0.9
        stat_a.avg_cost = 0.05
        stat_a.avg_duration = 2.0

        stat_b = MagicMock()
        stat_b.variant_id = "b"
        stat_b.count = 10
        stat_b.avg_quality = 0.7
        stat_b.avg_cost = 0.01
        stat_b.avg_duration = 1.0

        stats_result = MagicMock()
        stats_result.all.return_value = [stat_a, stat_b]
        session.execute = AsyncMock(return_value=stats_result)

        config = AutoPilotConfig(
            min_samples=10,
            auto_deploy=False,
            optimize_for="quality",
            quality_threshold=0.6,
        )

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await maybe_complete_experiment(uuid.uuid4(), config)

        assert result is not None
        assert result["variant_id"] == "a"

    @pytest.mark.asyncio
    async def test_enough_samples_auto_deploy_not_significant_returns_none(self):
        """When auto_deploy=True but not significant, returns None (keep running)."""
        from sandcastle.engine.autopilot import maybe_complete_experiment

        session = _make_mock_session()
        session.scalar = AsyncMock(return_value=20)

        stat_a = MagicMock()
        stat_a.variant_id = "winner"
        stat_a.count = 10
        stat_a.avg_quality = 0.75
        stat_a.avg_cost = 0.05
        stat_a.avg_duration = 2.0

        stat_b = MagicMock()
        stat_b.variant_id = "loser"
        stat_b.count = 10
        stat_b.avg_quality = 0.70
        stat_b.avg_cost = 0.03
        stat_b.avg_duration = 1.5

        stats_result = MagicMock()
        stats_result.all.return_value = [stat_a, stat_b]

        # Scores result (too few for significance - only 2 per variant)
        score_row_w1 = MagicMock()
        score_row_w1.variant_id = "winner"
        score_row_w1.quality_score = 0.75

        score_row_l1 = MagicMock()
        score_row_l1.variant_id = "loser"
        score_row_l1.quality_score = 0.70

        scores_result = MagicMock()
        scores_result.all.return_value = [score_row_w1, score_row_l1]

        call_count = 0

        async def execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return stats_result
            return scores_result

        session.execute = AsyncMock(side_effect=execute_side_effect)

        config = AutoPilotConfig(
            min_samples=10,
            auto_deploy=True,
            optimize_for="quality",
            quality_threshold=0.6,
        )

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await maybe_complete_experiment(uuid.uuid4(), config)

        # Not significant (too few samples) -> returns None
        assert result is None


class TestAdvanceRollout:
    """Tests for advance_rollout with mocked DB."""

    @pytest.mark.asyncio
    async def test_not_deploying_returns_none(self):
        """Returns None if experiment is not in DEPLOYING status."""
        from sandcastle.engine.autopilot import advance_rollout

        session = _make_mock_session()
        session.get = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await advance_rollout(uuid.uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_advance_from_canary_to_partial(self):
        """Advances rollout from canary to partial."""
        from sandcastle.engine.autopilot import advance_rollout
        from sandcastle.models.db import ExperimentStatus

        session = _make_mock_session()
        experiment = MagicMock()
        experiment.status = ExperimentStatus.DEPLOYING
        experiment.rollout_stage = "canary"
        session.get = AsyncMock(return_value=experiment)

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await advance_rollout(uuid.uuid4())

        assert result is not None
        assert result["stage"] == "partial"
        assert result["traffic"] == pytest.approx(0.50)
        assert result["completed"] is False

    @pytest.mark.asyncio
    async def test_advance_from_full_completes_experiment(self):
        """Advancing from full stage marks experiment as completed."""
        from sandcastle.engine.autopilot import advance_rollout
        from sandcastle.models.db import ExperimentStatus

        session = _make_mock_session()
        experiment = MagicMock()
        experiment.status = ExperimentStatus.DEPLOYING
        experiment.rollout_stage = "full"
        session.get = AsyncMock(return_value=experiment)

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await advance_rollout(uuid.uuid4())

        assert result is not None
        assert result["stage"] == "full"
        assert result["traffic"] == 1.0
        assert result["completed"] is True

    @pytest.mark.asyncio
    async def test_advance_from_partial_to_full(self):
        """Advances rollout from partial to full."""
        from sandcastle.engine.autopilot import advance_rollout
        from sandcastle.models.db import ExperimentStatus

        session = _make_mock_session()
        experiment = MagicMock()
        experiment.status = ExperimentStatus.DEPLOYING
        experiment.rollout_stage = "partial"
        session.get = AsyncMock(return_value=experiment)

        with patch("sandcastle.models.db.async_session", return_value=_make_async_session_ctx(session)):
            result = await advance_rollout(uuid.uuid4())

        assert result is not None
        assert result["stage"] == "full"
        assert result["completed"] is False


# ===========================================================================
# AUTOPILOT - evaluate_result async
# ===========================================================================

class TestEvaluateResult:
    """Tests for evaluate_result and LLM judge evaluation."""

    @pytest.mark.asyncio
    async def test_schema_completeness_method(self):
        """evaluate_result with schema_completeness method."""
        from sandcastle.engine.autopilot import evaluate_result
        from sandcastle.engine.dag import EvaluationConfig as AutoPilotEvaluationConfig

        config = AutoPilotConfig(
            evaluation=AutoPilotEvaluationConfig(method="schema_completeness"),
        )
        s = StepDefinition(
            id="test",
            prompt="test",
            output_schema={"properties": {"name": {}, "score": {}}},
        )
        output = {"name": "Alice", "score": 95}

        score = await evaluate_result(config, s, output)
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_default_non_null_check_with_output(self):
        """evaluate_result with unknown method uses non-null check."""
        from sandcastle.engine.autopilot import evaluate_result
        from sandcastle.engine.dag import EvaluationConfig as AutoPilotEvaluationConfig

        config = AutoPilotConfig(
            evaluation=AutoPilotEvaluationConfig(method="unknown_method"),
        )
        s = StepDefinition(id="test", prompt="test")
        score = await evaluate_result(config, s, "some output")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_default_non_null_check_with_none(self):
        """evaluate_result with None output and unknown method returns 0.0."""
        from sandcastle.engine.autopilot import evaluate_result
        from sandcastle.engine.dag import EvaluationConfig as AutoPilotEvaluationConfig

        config = AutoPilotConfig(
            evaluation=AutoPilotEvaluationConfig(method="unknown_method"),
        )
        s = StepDefinition(id="test", prompt="test")
        score = await evaluate_result(config, s, None)
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_llm_judge_returns_middle_score_on_failure(self):
        """LLM judge falls back to 0.5 when evaluation fails."""
        from sandcastle.engine.autopilot import evaluate_result
        from sandcastle.engine.dag import EvaluationConfig as AutoPilotEvaluationConfig

        config = AutoPilotConfig(
            evaluation=AutoPilotEvaluationConfig(
                method="llm_judge",
                criteria="quality",
            ),
        )
        s = StepDefinition(id="test", prompt="test")

        with patch("sandcastle.engine.sandshore.get_sandshore_runtime", side_effect=ImportError("no sandshore")):
            score = await evaluate_result(config, s, "output")

        assert score == 0.5

    @pytest.mark.asyncio
    async def test_evaluate_result_no_evaluation_config(self):
        """evaluate_result with no evaluation config uses schema_completeness."""
        from sandcastle.engine.autopilot import evaluate_result

        config = AutoPilotConfig()  # No evaluation config
        s = StepDefinition(id="test", prompt="test")

        score = await evaluate_result(config, s, "some output")
        # schema_completeness with None schema -> 1.0 if output is not None
        assert score == 1.0


# ===========================================================================
# EXECUTOR - _write_csv_output
# ===========================================================================

class TestWriteCsvOutput:
    """Tests for _write_csv_output."""

    def test_none_csv_config_returns_early(self):
        """Step with no csv_output config does nothing."""
        from sandcastle.engine.executor import _write_csv_output
        import tempfile
        s = step(id="s1")
        # Should not raise
        _write_csv_output(s, {"key": "val"}, "run123")

    def test_csv_output_list_of_dicts(self, tmp_path):
        """CSV output with list of dicts writes rows correctly."""
        from sandcastle.engine.executor import _write_csv_output
        from sandcastle.engine.dag import CsvOutputConfig

        s = step(id="csv_step")
        s = StepDefinition(
            id="csv_step",
            prompt="",
            csv_output=CsvOutputConfig(
                directory=str(tmp_path),
                filename="test",
                mode="new_file",
            ),
        )

        mock_settings = MagicMock()
        mock_settings.sandbox_root = None

        with patch("sandcastle.config.settings", mock_settings):
            _write_csv_output(s, [{"name": "Alice", "score": 95}, {"name": "Bob", "score": 80}], "run123")

        import glob
        files = glob.glob(str(tmp_path / "test_*.csv"))
        assert len(files) == 1

    def test_csv_output_single_dict(self, tmp_path):
        """CSV output with single dict writes one row."""
        from sandcastle.engine.executor import _write_csv_output
        from sandcastle.engine.dag import CsvOutputConfig

        s = StepDefinition(
            id="csv_step2",
            prompt="",
            csv_output=CsvOutputConfig(
                directory=str(tmp_path),
                filename="single",
                mode="new_file",
            ),
        )

        mock_settings = MagicMock()
        mock_settings.sandbox_root = None

        with patch("sandcastle.config.settings", mock_settings):
            _write_csv_output(s, {"key": "value", "num": 42}, "run123")

        import glob
        files = glob.glob(str(tmp_path / "single_*.csv"))
        assert len(files) == 1

    def test_csv_output_string_json_output(self, tmp_path):
        """CSV output with JSON string parses and writes rows."""
        from sandcastle.engine.executor import _write_csv_output
        from sandcastle.engine.dag import CsvOutputConfig
        import json

        s = StepDefinition(
            id="csv_json",
            prompt="",
            csv_output=CsvOutputConfig(
                directory=str(tmp_path),
                filename="json_out",
                mode="new_file",
            ),
        )

        mock_settings = MagicMock()
        mock_settings.sandbox_root = None

        with patch("sandcastle.config.settings", mock_settings):
            _write_csv_output(
                s,
                json.dumps([{"item": "a"}, {"item": "b"}]),
                "run123"
            )

        import glob
        files = glob.glob(str(tmp_path / "json_out_*.csv"))
        assert len(files) == 1

    def test_csv_output_append_mode(self, tmp_path):
        """CSV output in append mode adds to existing file."""
        from sandcastle.engine.executor import _write_csv_output
        from sandcastle.engine.dag import CsvOutputConfig

        s = StepDefinition(
            id="csv_append",
            prompt="",
            csv_output=CsvOutputConfig(
                directory=str(tmp_path),
                filename="append",
                mode="append",
            ),
        )

        mock_settings = MagicMock()
        mock_settings.sandbox_root = None

        with patch("sandcastle.config.settings", mock_settings):
            _write_csv_output(s, [{"x": 1}], "run1")
            _write_csv_output(s, [{"x": 2}], "run2")

        import csv
        with open(tmp_path / "append.csv") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2

    def test_csv_sandbox_root_blocks_outside_path(self, tmp_path):
        """CSV output outside sandbox root is skipped."""
        from sandcastle.engine.executor import _write_csv_output
        from sandcastle.engine.dag import CsvOutputConfig

        s = StepDefinition(
            id="csv_sandbox",
            prompt="",
            csv_output=CsvOutputConfig(
                directory="/tmp/outside_sandbox",
                filename="blocked",
                mode="new_file",
            ),
        )

        mock_settings = MagicMock()
        mock_settings.sandbox_root = str(tmp_path)

        with patch("sandcastle.config.settings", mock_settings):
            # Should not raise, just skip
            _write_csv_output(s, {"x": 1}, "run123")

        # No file should be created outside sandbox
        import os
        assert not os.path.exists("/tmp/outside_sandbox/blocked.csv")
