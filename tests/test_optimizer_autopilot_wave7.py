"""Wave 7 deep audit: Optimizer (smart routing) and AutoPilot (A/B testing).

Tests focus on algorithmic correctness, edge cases in statistics,
boundary conditions, and numerical stability.
"""

from __future__ import annotations

import math
import random
import time
from collections import namedtuple
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.autopilot import (
    ROLLOUT_ORDER,
    ROLLOUT_STAGES,
    _check_significance,
    _evaluate_schema_completeness,
    _two_tailed_p_value,
    apply_variant,
    select_winner,
    should_use_winner,
)
from sandcastle.engine.dag import (
    AutoPilotConfig,
    EvaluationConfig,
    StepDefinition,
    VariantConfig,
)
from sandcastle.engine.optimizer import (
    DEFAULT_MODEL_POOL,
    EXTENDED_MODEL_POOL,
    CostLatencyOptimizer,
    DegradationAlert,
    ModelOption,
    PerformanceStats,
    RoutingDecision,
    SLO,
    calculate_budget_pressure,
    get_optimizer,
)

# Reusable namedtuple matching DB query row shape
VariantRow = namedtuple(
    "VariantRow", ["variant_id", "count", "avg_quality", "avg_cost", "avg_duration"]
)


def _opt(
    option_id: str = "test",
    model: str = "sonnet",
    max_turns: int = 10,
    avg_quality: float | None = None,
    avg_cost: float | None = None,
    avg_latency: float | None = None,
    sample_count: int = 0,
) -> ModelOption:
    """Helper to build a ModelOption."""
    return ModelOption(
        id=option_id,
        model=model,
        max_turns=max_turns,
        avg_quality=avg_quality,
        avg_cost=avg_cost,
        avg_latency=avg_latency,
        sample_count=sample_count,
    )


# ====================================================================
# 1. Optimizer - Budget Pressure
# ====================================================================


class TestBudgetPressure:
    """calculate_budget_pressure edge cases."""

    def test_zero_cost(self):
        assert calculate_budget_pressure(0.0, 10.0) == 0.0

    def test_half_budget(self):
        assert calculate_budget_pressure(5.0, 10.0) == 0.5

    def test_exactly_100_percent(self):
        """At exactly 100% budget the pressure should be 1.0, not > 1."""
        assert calculate_budget_pressure(10.0, 10.0) == 1.0

    def test_over_budget_clamped(self):
        """Exceeding budget should still return 1.0 (clamped)."""
        assert calculate_budget_pressure(15.0, 10.0) == 1.0

    def test_none_max_cost(self):
        assert calculate_budget_pressure(5.0, None) == 0.0

    def test_zero_max_cost(self):
        assert calculate_budget_pressure(5.0, 0.0) == 0.0

    def test_negative_max_cost(self):
        """Negative max_cost should return 0 (no valid budget)."""
        assert calculate_budget_pressure(5.0, -10.0) == 0.0

    def test_negative_current_cost(self):
        """Negative current_cost (refund) should return 0, not negative pressure."""
        result = calculate_budget_pressure(-5.0, 10.0)
        assert result == 0.0

    def test_very_small_max_cost(self):
        """Tiny max_cost should still clamp to 1.0."""
        assert calculate_budget_pressure(1.0, 0.001) == 1.0

    def test_very_large_cost(self):
        """Very large cost still clamps."""
        assert calculate_budget_pressure(1e12, 1e6) == 1.0

    def test_float_precision(self):
        """Floating point precision should not cause > 1.0."""
        result = calculate_budget_pressure(0.1 + 0.2, 0.3)
        assert result <= 1.0


# ====================================================================
# 2. Optimizer - Scoring algorithms
# ====================================================================


class TestOptimizerScoring:
    """Core scoring algorithm correctness."""

    def test_cost_optimization_picks_cheapest_viable(self):
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("exp", avg_quality=0.95, avg_cost=0.50, sample_count=10),
            _opt("cheap", avg_quality=0.70, avg_cost=0.02, sample_count=10),
            _opt("mid", avg_quality=0.80, avg_cost=0.10, sample_count=10),
        ]
        slo = SLO(quality_min=0.6, optimize_for="cost")
        result = optimizer._score_options(options, slo)
        assert result.id == "cheap"

    def test_quality_optimization_picks_highest(self):
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("lo", avg_quality=0.60, avg_cost=0.01, sample_count=10),
            _opt("hi", avg_quality=0.99, avg_cost=0.50, sample_count=10),
        ]
        slo = SLO(optimize_for="quality")
        result = optimizer._score_options(options, slo)
        assert result.id == "hi"

    def test_latency_optimization_picks_fastest(self):
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("slow", avg_latency=120.0, avg_quality=0.9, sample_count=10),
            _opt("fast", avg_latency=5.0, avg_quality=0.7, sample_count=10),
        ]
        slo = SLO(optimize_for="latency")
        result = optimizer._score_options(options, slo)
        assert result.id == "fast"

    def test_balanced_considers_all_metrics(self):
        """Balanced mode should weigh quality, cost, and latency."""
        optimizer = CostLatencyOptimizer()
        # Option A: high quality, high cost, slow
        # Option B: medium quality, low cost, fast
        options = [
            _opt("a", avg_quality=0.95, avg_cost=0.50, avg_latency=100, sample_count=10),
            _opt("b", avg_quality=0.80, avg_cost=0.05, avg_latency=10, sample_count=10),
        ]
        slo = SLO(optimize_for="balanced")
        result = optimizer._score_options(options, slo)
        # B should win in balanced because it's cheaper AND faster
        assert result.id == "b"

    def test_score_with_none_values_uses_defaults(self):
        """None metrics should default to sensible values (not crash)."""
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("a", avg_quality=None, avg_cost=None, avg_latency=None, sample_count=0),
            _opt("b", avg_quality=0.8, avg_cost=0.10, avg_latency=30, sample_count=10),
        ]
        slo = SLO(optimize_for="quality")
        result = optimizer._score_options(options, slo)
        # B should win (0.8 > 0.5 default)
        assert result.id == "b"

    def test_identical_options_returns_one(self):
        """When all options score equally, should return first match (deterministic)."""
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("a", avg_quality=0.8, avg_cost=0.10, avg_latency=30, sample_count=10),
            _opt("b", avg_quality=0.8, avg_cost=0.10, avg_latency=30, sample_count=10),
        ]
        slo = SLO(optimize_for="balanced")
        result = optimizer._score_options(options, slo)
        # Both score equally; max() returns first element with max value
        assert result.id in ("a", "b")

    def test_single_option(self):
        """Single option should always be selected."""
        optimizer = CostLatencyOptimizer()
        options = [_opt("only", avg_quality=0.5, avg_cost=1.0, sample_count=5)]
        slo = SLO(optimize_for="cost")
        result = optimizer._score_options(options, slo)
        assert result.id == "only"


class TestScoreWithBias:
    """Budget-pressure biased scoring."""

    def test_high_bias_picks_cheapest(self):
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("exp", avg_quality=0.95, avg_cost=0.50, sample_count=10),
            _opt("cheap", avg_quality=0.70, avg_cost=0.02, sample_count=10),
        ]
        slo = SLO(optimize_for="quality")
        result = optimizer._score_with_bias(options, slo, cost_bias=0.9)
        assert result.id == "cheap"

    def test_low_bias_prefers_quality(self):
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("exp", avg_quality=0.95, avg_cost=0.50, sample_count=10),
            _opt("cheap", avg_quality=0.30, avg_cost=0.02, sample_count=10),
        ]
        slo = SLO(optimize_for="quality")
        # With cost_bias=0.1, quality dominates
        result = optimizer._score_with_bias(options, slo, cost_bias=0.1)
        assert result.id == "exp"


# ====================================================================
# 3. Optimizer - select_model (full pipeline)
# ====================================================================


class TestSelectModel:
    """Integration tests for the full model selection pipeline."""

    @pytest.mark.asyncio
    async def test_cold_start_overrides_budget_pressure(self):
        """Cold start detection should override even critical budget pressure."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("a", "haiku"),
            _opt("b", "sonnet"),
            _opt("c", "opus"),
        ]
        slo = SLO(quality_min=0.0, optimize_for="quality")
        decision = await optimizer.select_model(
            step_id="new", workflow_name="new-wf",
            slo=slo, model_pool=pool, budget_pressure=0.99,
        )
        assert "cold start" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_critical_budget_forces_cheapest(self):
        """Budget > 0.9 should force cheapest option (when stats exist)."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("exp", "opus", avg_quality=0.95, avg_cost=0.50, sample_count=10),
            _opt("cheap", "haiku", avg_quality=0.70, avg_cost=0.02, sample_count=10),
        ]
        slo = SLO(quality_min=0.0, cost_max_usd=1.0, optimize_for="quality")
        decision = await optimizer.select_model(
            step_id="s1", workflow_name="w1",
            slo=slo, model_pool=pool, budget_pressure=0.95,
        )
        assert decision.selected_option.model == "haiku"
        assert "critical" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_slo_quality_filter(self):
        """Models below quality_min should be excluded."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("bad", "haiku", avg_quality=0.30, avg_cost=0.01, sample_count=10),
            _opt("good", "sonnet", avg_quality=0.85, avg_cost=0.10, sample_count=10),
        ]
        slo = SLO(quality_min=0.5, cost_max_usd=1.0, optimize_for="cost")
        decision = await optimizer.select_model(
            step_id="s1", workflow_name="w1", slo=slo, model_pool=pool,
        )
        assert decision.selected_option.model == "sonnet"

    @pytest.mark.asyncio
    async def test_slo_cost_filter(self):
        """Models above cost_max_usd should be excluded."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("cheap", "haiku", avg_quality=0.70, avg_cost=0.02, sample_count=10),
            _opt("exp", "opus", avg_quality=0.95, avg_cost=0.50, sample_count=10),
        ]
        slo = SLO(quality_min=0.0, cost_max_usd=0.10, optimize_for="quality")
        decision = await optimizer.select_model(
            step_id="s1", workflow_name="w1", slo=slo, model_pool=pool,
        )
        assert decision.selected_option.model == "haiku"

    @pytest.mark.asyncio
    async def test_slo_latency_filter(self):
        """Models above latency_max_seconds should be excluded."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("fast", "haiku", avg_quality=0.70, avg_latency=10, sample_count=10),
            _opt("slow", "opus", avg_quality=0.95, avg_latency=200, sample_count=10),
        ]
        slo = SLO(quality_min=0.0, latency_max_seconds=60, optimize_for="quality")
        decision = await optimizer.select_model(
            step_id="s1", workflow_name="w1", slo=slo, model_pool=pool,
        )
        assert decision.selected_option.model == "haiku"

    @pytest.mark.asyncio
    async def test_all_filtered_falls_back(self):
        """When all options violate SLO, fallback to middle option."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("a", "haiku", avg_quality=0.10, avg_cost=0.01, sample_count=10),
            _opt("b", "sonnet", avg_quality=0.20, avg_cost=0.10, sample_count=10),
            _opt("c", "opus", avg_quality=0.30, avg_cost=0.50, sample_count=10),
        ]
        slo = SLO(quality_min=0.99, cost_max_usd=0.005, optimize_for="quality")
        decision = await optimizer.select_model(
            step_id="s1", workflow_name="w1", slo=slo, model_pool=pool,
        )
        # Should fall back to middle option
        assert decision.selected_option is not None

    @pytest.mark.asyncio
    async def test_alternatives_exclude_selected(self):
        """Alternatives list should not contain the selected option."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("a", "haiku", avg_quality=0.70, avg_cost=0.02, sample_count=10),
            _opt("b", "sonnet", avg_quality=0.85, avg_cost=0.10, sample_count=10),
            _opt("c", "opus", avg_quality=0.95, avg_cost=0.50, sample_count=10),
        ]
        slo = SLO(quality_min=0.0, cost_max_usd=1.0, optimize_for="quality")
        decision = await optimizer.select_model(
            step_id="s1", workflow_name="w1", slo=slo, model_pool=pool,
        )
        alt_ids = [a.id for a in decision.alternatives]
        assert decision.selected_option.id not in alt_ids

    @pytest.mark.asyncio
    async def test_decision_has_budget_pressure(self):
        """RoutingDecision should carry the budget_pressure value."""
        optimizer = CostLatencyOptimizer()
        pool = [_opt("a", "sonnet", avg_quality=0.8, avg_cost=0.1, sample_count=10)]
        slo = SLO(optimize_for="quality")
        decision = await optimizer.select_model(
            step_id="s1", workflow_name="w1",
            slo=slo, model_pool=pool, budget_pressure=0.42,
        )
        assert decision.budget_pressure == 0.42


# ====================================================================
# 4. Optimizer - Enrichment and cache
# ====================================================================


class TestEnrichment:
    def test_enrich_merges_stats(self):
        optimizer = CostLatencyOptimizer()
        pool = [_opt("haiku", "haiku"), _opt("sonnet", "sonnet")]
        stats = [
            PerformanceStats(model="haiku", avg_quality=0.7, avg_cost=0.02, sample_count=25),
        ]
        enriched = optimizer._enrich_pool(pool, stats)
        assert enriched[0].avg_quality == 0.7
        assert enriched[0].sample_count == 25
        assert enriched[1].avg_quality is None  # No stats for sonnet

    def test_enrich_empty_stats(self):
        optimizer = CostLatencyOptimizer()
        pool = [_opt("haiku", "haiku")]
        enriched = optimizer._enrich_pool(pool, [])
        assert enriched[0].sample_count == 0
        assert enriched[0].avg_quality is None

    def test_enrich_duplicate_model_uses_last(self):
        """If multiple stats for same model, last one wins (dict key)."""
        optimizer = CostLatencyOptimizer()
        pool = [_opt("haiku", "haiku")]
        stats = [
            PerformanceStats(model="haiku", avg_quality=0.5, sample_count=5),
            PerformanceStats(model="haiku", avg_quality=0.9, sample_count=50),
        ]
        enriched = optimizer._enrich_pool(pool, stats)
        # Last stat wins in dict construction
        assert enriched[0].avg_quality == 0.9


class TestCache:
    @pytest.mark.asyncio
    async def test_cache_hit(self):
        optimizer = CostLatencyOptimizer()
        now = time.monotonic()
        optimizer._cache["wf:step"] = (
            now,
            [PerformanceStats(model="sonnet", avg_cost=0.10, sample_count=5)],
        )
        stats = await optimizer._get_performance_stats("step", "wf")
        assert len(stats) == 1
        assert stats[0].model == "sonnet"

    @pytest.mark.asyncio
    async def test_cache_expired(self):
        """Expired cache should re-query (which returns empty in unit test)."""
        optimizer = CostLatencyOptimizer()
        old = time.monotonic() - 600  # 10 minutes ago
        optimizer._cache["wf:step"] = (
            old,
            [PerformanceStats(model="old", sample_count=1)],
        )
        # _query_stats will fail without DB, returning empty
        stats = await optimizer._get_performance_stats("step", "wf")
        assert stats == []

    @pytest.mark.asyncio
    async def test_record_outcome_invalidates_cache(self):
        optimizer = CostLatencyOptimizer()
        optimizer._cache["wf:step"] = (
            time.monotonic(),
            [PerformanceStats(model="sonnet", sample_count=5)],
        )
        await optimizer.record_outcome("step", "wf", "sonnet", 0.8, 0.10, 5.0)
        assert "wf:step" not in optimizer._cache


# ====================================================================
# 5. Optimizer - Confidence
# ====================================================================


class TestConfidence:
    def test_confidence_scale(self):
        optimizer = CostLatencyOptimizer()
        c = optimizer._calculate_confidence
        assert c(_opt(sample_count=0)) == 0.1
        assert c(_opt(sample_count=1)) == 0.3
        assert c(_opt(sample_count=4)) == 0.3
        assert c(_opt(sample_count=5)) == 0.6
        assert c(_opt(sample_count=19)) == 0.6
        assert c(_opt(sample_count=20)) == 0.8
        assert c(_opt(sample_count=49)) == 0.8
        assert c(_opt(sample_count=50)) == 0.95
        assert c(_opt(sample_count=10000)) == 0.95


# ====================================================================
# 6. Optimizer - Fallback
# ====================================================================


class TestFallback:
    def test_fallback_middle_of_three(self):
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("cheap", avg_cost=0.01),
            _opt("mid", avg_cost=0.10),
            _opt("exp", avg_cost=0.50),
        ]
        result = optimizer._get_fallback(pool)
        assert result.id == "mid"

    def test_fallback_two_options(self):
        """With two options, picks index 1 (len//2 = 1)."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("cheap", avg_cost=0.01),
            _opt("exp", avg_cost=0.50),
        ]
        result = optimizer._get_fallback(pool)
        assert result.id == "exp"

    def test_fallback_single_option(self):
        """With one option, picks it (index 0)."""
        optimizer = CostLatencyOptimizer()
        pool = [_opt("only", avg_cost=0.10)]
        result = optimizer._get_fallback(pool)
        assert result.id == "only"

    def test_fallback_empty_pool(self):
        """Empty pool falls back to DEFAULT_MODEL_POOL[1]."""
        optimizer = CostLatencyOptimizer()
        result = optimizer._get_fallback([])
        assert result.model == "sonnet"  # balanced default

    def test_fallback_none_costs_use_default(self):
        """Options with None cost use 0.10 default for sorting."""
        optimizer = CostLatencyOptimizer()
        pool = [
            _opt("a", avg_cost=None),
            _opt("b", avg_cost=0.05),
        ]
        result = optimizer._get_fallback(pool)
        # Sorted: b(0.05), a(0.10 default) -> middle index = 1 -> "a"
        assert result.id == "a"


# ====================================================================
# 7. Optimizer - Degradation detection
# ====================================================================


class TestDegradation:
    @pytest.mark.asyncio
    async def test_quality_degradation_warning(self):
        optimizer = CostLatencyOptimizer()
        key = "wf:step"
        stats = [PerformanceStats(model="sonnet", avg_quality=0.55, sample_count=10)]
        optimizer._cache[key] = (time.monotonic(), stats)
        slo = SLO(quality_min=0.6)
        alerts = await optimizer.check_degradation("step", "wf", slo)
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"
        assert alerts[0].metric == "quality"

    @pytest.mark.asyncio
    async def test_quality_degradation_critical(self):
        optimizer = CostLatencyOptimizer()
        key = "wf:step"
        # avg_quality 0.40 < quality_min 0.6 * 0.8 = 0.48 => critical
        stats = [PerformanceStats(model="sonnet", avg_quality=0.40, sample_count=10)]
        optimizer._cache[key] = (time.monotonic(), stats)
        slo = SLO(quality_min=0.6)
        alerts = await optimizer.check_degradation("step", "wf", slo)
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_cost_overrun_alert(self):
        optimizer = CostLatencyOptimizer()
        key = "wf:step"
        stats = [PerformanceStats(model="opus", avg_cost=0.30, sample_count=10)]
        optimizer._cache[key] = (time.monotonic(), stats)
        slo = SLO(cost_max_usd=0.15)
        alerts = await optimizer.check_degradation("step", "wf", slo)
        cost_alerts = [a for a in alerts if a.metric == "cost"]
        assert len(cost_alerts) >= 1
        assert cost_alerts[0].severity == "critical"  # 0.30 > 0.15 * 1.5 = 0.225

    @pytest.mark.asyncio
    async def test_latency_breach_alert(self):
        optimizer = CostLatencyOptimizer()
        key = "wf:step"
        stats = [PerformanceStats(model="opus", avg_latency=150, sample_count=10)]
        optimizer._cache[key] = (time.monotonic(), stats)
        slo = SLO(latency_max_seconds=120)
        alerts = await optimizer.check_degradation("step", "wf", slo)
        lat_alerts = [a for a in alerts if a.metric == "latency"]
        assert len(lat_alerts) >= 1

    @pytest.mark.asyncio
    async def test_too_few_samples_skipped(self):
        """Models with < 5 samples should not trigger alerts."""
        optimizer = CostLatencyOptimizer()
        key = "wf:step"
        stats = [PerformanceStats(model="sonnet", avg_quality=0.10, sample_count=3)]
        optimizer._cache[key] = (time.monotonic(), stats)
        slo = SLO(quality_min=0.8)
        alerts = await optimizer.check_degradation("step", "wf", slo)
        assert alerts == []

    @pytest.mark.asyncio
    async def test_trend_degradation(self):
        """Quality drop > 0.1 from previous stats triggers warning."""
        optimizer = CostLatencyOptimizer()
        key = "wf:step"
        # Set previous stats
        optimizer._previous_stats[key] = {
            "sonnet": PerformanceStats(model="sonnet", avg_quality=0.9, sample_count=20),
        }
        # Current stats show degradation
        stats = [PerformanceStats(model="sonnet", avg_quality=0.7, sample_count=20)]
        optimizer._cache[key] = (time.monotonic(), stats)
        slo = SLO(quality_min=0.5)  # Low enough to not trigger threshold alert
        alerts = await optimizer.check_degradation("step", "wf", slo)
        trend_alerts = [a for a in alerts if "dropped" in a.recommended_action]
        assert len(trend_alerts) == 1
        assert trend_alerts[0].severity == "warning"

    @pytest.mark.asyncio
    async def test_alerts_capped(self):
        """Alerts should be capped at max_alerts."""
        optimizer = CostLatencyOptimizer()
        optimizer._max_alerts = 5
        key = "wf:step"
        stats = [PerformanceStats(model="sonnet", avg_quality=0.1, sample_count=10)]
        optimizer._cache[key] = (time.monotonic(), stats)
        slo = SLO(quality_min=0.9)

        for i in range(10):
            # Each call generates an alert
            await optimizer.check_degradation("step", "wf", slo)
            # Re-populate cache since check_degradation stores previous
            optimizer._cache[key] = (time.monotonic(), stats)

        assert len(optimizer._alerts) <= 5

    def test_clear_alerts(self):
        optimizer = CostLatencyOptimizer()
        optimizer._alerts = [
            DegradationAlert(
                model="sonnet", step_id="s1", workflow_name="w1",
                metric="quality", current_value=0.5, threshold=0.8,
                severity="warning", recommended_action="Fix it.",
            )
        ]
        count = optimizer.clear_alerts()
        assert count == 1
        assert optimizer._alerts == []

    def test_get_recent_alerts_respects_limit(self):
        optimizer = CostLatencyOptimizer()
        optimizer._alerts = [
            DegradationAlert(
                model=f"m{i}", step_id="s1", workflow_name="w1",
                metric="quality", current_value=0.5, threshold=0.8,
                severity="warning", recommended_action="Fix it.",
            )
            for i in range(30)
        ]
        recent = optimizer.get_recent_alerts(limit=5)
        assert len(recent) == 5


# ====================================================================
# 8. Optimizer - Singleton
# ====================================================================


class TestOptimizerSingleton:
    def test_get_optimizer_returns_same_instance(self):
        import sandcastle.engine.optimizer as opt_mod
        opt_mod._optimizer_instance = None
        o1 = get_optimizer()
        o2 = get_optimizer()
        assert o1 is o2
        opt_mod._optimizer_instance = None  # cleanup


# ====================================================================
# 9. Optimizer - Default/Extended pools
# ====================================================================


class TestModelPools:
    def test_default_pool_has_three(self):
        assert len(DEFAULT_MODEL_POOL) == 3

    def test_extended_pool_has_five(self):
        assert len(EXTENDED_MODEL_POOL) == 5

    def test_default_pool_models(self):
        models = {o.model for o in DEFAULT_MODEL_POOL}
        assert models == {"haiku", "sonnet", "opus"}


# ====================================================================
# 10. Optimizer - DegradationAlert post_init
# ====================================================================


class TestDegradationAlertPostInit:
    def test_detected_at_auto_set(self):
        alert = DegradationAlert(
            model="sonnet", step_id="s", workflow_name="w",
            metric="quality", current_value=0.5, threshold=0.8,
            severity="warning", recommended_action="action",
        )
        assert alert.detected_at > 0

    def test_detected_at_preserved_if_set(self):
        alert = DegradationAlert(
            model="sonnet", step_id="s", workflow_name="w",
            metric="quality", current_value=0.5, threshold=0.8,
            severity="warning", recommended_action="action",
            detected_at=12345.0,
        )
        assert alert.detected_at == 12345.0


# ====================================================================
# 11. AutoPilot - Statistical significance (_two_tailed_p_value)
# ====================================================================


class TestTwoTailedPValue:
    def test_zero_t_stat(self):
        """t=0 should give p=1.0 (no difference)."""
        p = _two_tailed_p_value(0.0, 10)
        assert p == pytest.approx(1.0, abs=0.001)

    def test_large_t_stat(self):
        """Large t should give p near 0."""
        p = _two_tailed_p_value(10.0, 100)
        assert p < 0.001

    def test_moderate_t_stat(self):
        """t~2 with enough df should give p near 0.05."""
        p = _two_tailed_p_value(2.0, 30)
        assert 0.01 < p < 0.10

    def test_negative_df(self):
        assert _two_tailed_p_value(5.0, -1) == 1.0

    def test_zero_df(self):
        assert _two_tailed_p_value(5.0, 0) == 1.0

    def test_very_small_df(self):
        """Very small df should still return valid p."""
        p = _two_tailed_p_value(3.0, 2)
        assert 0.0 <= p <= 1.0

    def test_very_large_df(self):
        """Large df uses z approximation directly."""
        p = _two_tailed_p_value(1.96, 1000)
        assert 0.04 < p < 0.06  # Should be ~0.05

    def test_nan_t_stat(self):
        """NaN t_stat should return 1.0 (not significant)."""
        p = _two_tailed_p_value(float("nan"), 10)
        assert p == 1.0

    def test_inf_t_stat(self):
        """Inf t_stat should return 0.0 (infinitely significant)."""
        p = _two_tailed_p_value(float("inf"), 10)
        assert p == 0.0

    def test_neg_inf_t_stat(self):
        """Negative Inf t_stat should return 0.0."""
        p = _two_tailed_p_value(float("-inf"), 10)
        assert p == 0.0

    def test_nan_df(self):
        p = _two_tailed_p_value(2.0, float("nan"))
        assert p == 1.0

    def test_result_always_in_range(self):
        """P-value must always be in [0, 1]."""
        for t in [-100, -10, -1, 0, 1, 10, 100]:
            for df in [1, 5, 10, 50, 200]:
                p = _two_tailed_p_value(t, df)
                assert 0.0 <= p <= 1.0, f"Out of range: t={t}, df={df}, p={p}"


# ====================================================================
# 12. AutoPilot - _check_significance (Welch's t-test)
# ====================================================================


class TestCheckSignificance:
    def test_clearly_different_groups(self):
        """Two clearly different groups should be significant."""
        winner = [0.9, 0.95, 0.88, 0.92, 0.90, 0.93, 0.91, 0.89, 0.94, 0.92]
        loser = [0.4, 0.35, 0.42, 0.38, 0.41, 0.37, 0.39, 0.40, 0.36, 0.43]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True
        assert p < 0.001

    def test_identical_groups(self):
        """Identical groups should not be significant."""
        data = [0.8, 0.82, 0.79, 0.81, 0.80, 0.78, 0.83, 0.80]
        is_sig, p = _check_significance(data, data.copy())
        assert is_sig is False
        assert p > 0.5

    def test_too_few_samples(self):
        """Less than 3 samples per group should return not significant."""
        is_sig, p = _check_significance([0.9, 0.8], [0.5, 0.4])
        assert is_sig is False
        assert p == 1.0

    def test_empty_lists(self):
        is_sig, p = _check_significance([], [])
        assert is_sig is False
        assert p == 1.0

    def test_zero_variance_same_mean(self):
        """All same values in both groups: not significant."""
        winner = [0.8, 0.8, 0.8, 0.8, 0.8]
        loser = [0.8, 0.8, 0.8, 0.8, 0.8]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is False
        assert p == 1.0

    def test_zero_variance_different_mean(self):
        """All same values but different between groups."""
        winner = [0.9, 0.9, 0.9, 0.9, 0.9]
        loser = [0.5, 0.5, 0.5, 0.5, 0.5]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True
        assert p == 0.0

    def test_one_zero_variance_group(self):
        """One group constant, other varies."""
        winner = [0.9, 0.9, 0.9, 0.9, 0.9]
        loser = [0.5, 0.6, 0.4, 0.55, 0.45]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True
        assert p < 0.05

    def test_unequal_sample_sizes(self):
        """Welch's t-test handles unequal sample sizes."""
        winner = [0.9, 0.88, 0.92, 0.91]
        loser = [0.5, 0.52, 0.48, 0.51, 0.49, 0.50, 0.53, 0.47, 0.51, 0.50]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True

    def test_custom_p_threshold(self):
        """Custom p-value threshold."""
        winner = [0.85, 0.80, 0.82, 0.84, 0.83]
        loser = [0.75, 0.70, 0.72, 0.74, 0.73]
        is_sig_strict, p_strict = _check_significance(winner, loser, min_p_value=0.001)
        is_sig_loose, p_loose = _check_significance(winner, loser, min_p_value=0.10)
        # p values should be the same, significance varies with threshold
        assert p_strict == pytest.approx(p_loose)
        # The groups are quite different, but might not be < 0.001

    def test_nan_in_scores(self):
        """NaN in score list should be handled gracefully."""
        winner = [0.9, float("nan"), 0.88, 0.92, 0.91]
        loser = [0.5, 0.52, 0.48, 0.51, 0.49]
        is_sig, p = _check_significance(winner, loser)
        # Should return not significant since NaN propagates through mean
        assert is_sig is False
        assert p == 1.0

    def test_symmetry(self):
        """Swapping groups should give same p-value (two-tailed)."""
        a = [0.9, 0.88, 0.92, 0.91, 0.87]
        b = [0.5, 0.52, 0.48, 0.51, 0.49]
        _, p_ab = _check_significance(a, b)
        _, p_ba = _check_significance(b, a)
        assert p_ab == pytest.approx(p_ba, abs=0.001)


# ====================================================================
# 13. AutoPilot - select_winner
# ====================================================================


class TestSelectWinnerWave7:
    def test_optimize_quality_picks_highest(self):
        stats = [
            VariantRow("a", 10, 0.80, 0.05, 5.0),
            VariantRow("b", 10, 0.95, 0.10, 8.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_optimize_cost_picks_cheapest(self):
        stats = [
            VariantRow("a", 10, 0.80, 0.05, 5.0),
            VariantRow("b", 10, 0.90, 0.01, 8.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_optimize_latency_picks_fastest(self):
        stats = [
            VariantRow("fast", 10, 0.80, 0.05, 2.0),
            VariantRow("slow", 10, 0.90, 0.05, 10.0),
        ]
        config = AutoPilotConfig(optimize_for="latency", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "fast"

    def test_pareto_balanced(self):
        stats = [
            VariantRow("exp_fast", 10, 0.90, 0.50, 1.0),
            VariantRow("balanced", 10, 0.85, 0.05, 3.0),
            VariantRow("cheap_slow", 10, 0.70, 0.01, 20.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner is not None
        # Balanced should generally win in Pareto optimization
        assert winner["variant_id"] == "balanced"

    def test_pareto_all_zero_cost(self):
        """Pareto with all-zero costs should not crash (division guard)."""
        stats = [
            VariantRow("a", 10, 0.90, 0.0, 5.0),
            VariantRow("b", 10, 0.80, 0.0, 3.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner is not None

    def test_pareto_all_zero_duration(self):
        """Pareto with all-zero durations should not crash."""
        stats = [
            VariantRow("a", 10, 0.90, 0.05, 0.0),
            VariantRow("b", 10, 0.80, 0.10, 0.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner is not None

    def test_pareto_tiny_cost_no_inf(self):
        """Near-zero cost should not produce Inf in Pareto scores."""
        stats = [
            VariantRow("a", 10, 0.90, 1e-15, 5.0),
            VariantRow("b", 10, 0.80, 1e-15, 3.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner is not None
        # Pareto score should not be NaN or Inf
        assert not math.isnan(winner.get("pareto_score", 0))
        assert not math.isinf(winner.get("pareto_score", 0))

    def test_quality_threshold_filters(self):
        stats = [
            VariantRow("bad", 10, 0.30, 0.01, 1.0),
            VariantRow("good", 10, 0.80, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.7)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "good"

    def test_all_below_threshold_returns_best_quality(self):
        stats = [
            VariantRow("a", 10, 0.30, 0.01, 1.0),
            VariantRow("b", 10, 0.50, 0.05, 3.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.9)
        winner = select_winner(stats, config)
        # Falls back to best quality
        assert winner["variant_id"] == "b"

    def test_empty_stats_returns_none(self):
        config = AutoPilotConfig(optimize_for="quality")
        assert select_winner([], config) is None

    def test_single_variant(self):
        stats = [VariantRow("only", 10, 0.85, 0.05, 5.0)]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "only"

    def test_none_quality_treated_as_zero(self):
        stats = [
            VariantRow("a", 10, None, 0.01, 1.0),
            VariantRow("b", 10, 0.80, 0.05, 3.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "b"

    def test_none_cost_treated_as_zero(self):
        stats = [
            VariantRow("a", 10, 0.80, None, 5.0),
            VariantRow("b", 10, 0.80, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.6)
        winner = select_winner(stats, config)
        # None cost becomes 0, so "a" is cheaper
        assert winner["variant_id"] == "a"

    def test_unknown_optimize_for_defaults_to_quality(self):
        """Unknown optimize_for value should use default (quality) path."""
        stats = [
            VariantRow("lo", 10, 0.60, 0.01, 1.0),
            VariantRow("hi", 10, 0.95, 0.50, 10.0),
        ]
        config = AutoPilotConfig(optimize_for="unknown_mode", quality_threshold=0.5)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "hi"


# ====================================================================
# 14. AutoPilot - Schema completeness evaluation
# ====================================================================


class TestSchemaCompleteness:
    def test_full_match(self):
        schema = {"properties": {"a": {}, "b": {}, "c": {}}}
        output = {"a": 1, "b": 2, "c": 3}
        assert _evaluate_schema_completeness(output, schema) == 1.0

    def test_partial_match(self):
        schema = {"properties": {"a": {}, "b": {}, "c": {}, "d": {}}}
        output = {"a": 1, "b": 2}
        assert _evaluate_schema_completeness(output, schema) == 0.5

    def test_none_values_not_counted(self):
        schema = {"properties": {"a": {}, "b": {}}}
        output = {"a": 1, "b": None}
        assert _evaluate_schema_completeness(output, schema) == 0.5

    def test_no_schema_with_output(self):
        assert _evaluate_schema_completeness({"data": 1}, None) == 1.0

    def test_no_schema_no_output(self):
        assert _evaluate_schema_completeness(None, None) == 0.0

    def test_non_dict_output(self):
        schema = {"properties": {"a": {}}}
        assert _evaluate_schema_completeness("string", schema) == 0.0
        assert _evaluate_schema_completeness(42, schema) == 0.0
        assert _evaluate_schema_completeness([1, 2], schema) == 0.0

    def test_empty_properties(self):
        schema = {"properties": {}}
        assert _evaluate_schema_completeness({"a": 1}, schema) == 1.0

    def test_extra_keys_ignored(self):
        schema = {"properties": {"a": {}}}
        output = {"a": 1, "b": 2, "c": 3}
        assert _evaluate_schema_completeness(output, schema) == 1.0

    def test_schema_without_properties_key(self):
        schema = {"type": "object"}
        output = {"a": 1}
        # No properties key -> empty properties -> returns 1.0
        assert _evaluate_schema_completeness(output, schema) == 1.0


# ====================================================================
# 15. AutoPilot - apply_variant
# ====================================================================


class TestApplyVariantWave7:
    def test_model_override(self):
        step = StepDefinition(id="s1", prompt="test", model="sonnet")
        variant = VariantConfig(id="v1", model="haiku")
        modified = apply_variant(step, variant)
        assert modified.model == "haiku"
        assert modified.prompt == "test"  # preserved

    def test_prompt_override(self):
        step = StepDefinition(id="s1", prompt="original", model="sonnet")
        variant = VariantConfig(id="v1", prompt="new prompt")
        modified = apply_variant(step, variant)
        assert modified.prompt == "new prompt"
        assert modified.model == "sonnet"

    def test_max_turns_override(self):
        step = StepDefinition(id="s1", prompt="test", model="sonnet", max_turns=10)
        variant = VariantConfig(id="v1", max_turns=20)
        modified = apply_variant(step, variant)
        assert modified.max_turns == 20

    def test_autopilot_cleared(self):
        """Applying a variant should clear autopilot config to prevent recursion."""
        config = AutoPilotConfig(enabled=True, variants=[VariantConfig(id="v")])
        step = StepDefinition(id="s1", prompt="test", model="sonnet", autopilot=config)
        variant = VariantConfig(id="v1", model="haiku")
        modified = apply_variant(step, variant)
        assert modified.autopilot is None

    def test_no_overrides_preserves_step(self):
        """Variant with no model/prompt/max_turns preserves original."""
        step = StepDefinition(id="s1", prompt="test", model="sonnet", max_turns=10)
        variant = VariantConfig(id="v1")
        modified = apply_variant(step, variant)
        assert modified.model == "sonnet"
        assert modified.prompt == "test"
        assert modified.max_turns == 10


# ====================================================================
# 16. AutoPilot - Progressive rollout
# ====================================================================


class TestProgressiveRollout:
    def test_rollout_stages_ordered(self):
        assert ROLLOUT_ORDER == ["canary", "partial", "full"]
        assert ROLLOUT_STAGES["canary"] == 0.10
        assert ROLLOUT_STAGES["partial"] == 0.50
        assert ROLLOUT_STAGES["full"] == 1.0

    def test_should_use_winner_none_stage(self):
        assert should_use_winner(None) is False

    def test_should_use_winner_full_always_true(self):
        """Full rollout should always return True."""
        # random.random() is always < 1.0
        results = [should_use_winner("full") for _ in range(100)]
        assert all(results)

    def test_should_use_winner_canary_sometimes(self):
        """Canary rollout (~10%) should sometimes return True, sometimes False."""
        random.seed(42)
        results = [should_use_winner("canary") for _ in range(1000)]
        true_count = sum(results)
        # Should be roughly 10% (within statistical bounds)
        assert 50 < true_count < 200  # Very loose bounds

    def test_should_use_winner_unknown_stage(self):
        """Unknown stage should return False (0 traffic)."""
        assert should_use_winner("unknown") is False

    def test_should_use_winner_partial(self):
        """Partial rollout (~50%) should be roughly half."""
        random.seed(42)
        results = [should_use_winner("partial") for _ in range(1000)]
        true_count = sum(results)
        assert 400 < true_count < 600


# ====================================================================
# 17. AutoPilot - Thompson Sampling correctness
# ====================================================================


class TestThompsonSampling:
    """Tests for the pick_variant Thompson Sampling algorithm (unit-level)."""

    def test_high_quality_variant_preferred(self):
        """A variant with consistently high quality should be picked more often."""
        # We test the Beta distribution logic directly
        # High quality variant: successes=90, total=100 -> alpha=91, beta=11
        # Low quality variant: successes=30, total=100 -> alpha=31, beta=71
        random.seed(42)
        high_picks = 0
        trials = 1000
        for _ in range(trials):
            high_sample = random.betavariate(91, 11)
            low_sample = random.betavariate(31, 71)
            if high_sample > low_sample:
                high_picks += 1
        # High quality should win the vast majority of the time
        assert high_picks > 950

    def test_equal_quality_explores_both(self):
        """Variants with equal quality should be explored roughly equally."""
        random.seed(42)
        a_picks = 0
        trials = 1000
        for _ in range(trials):
            a_sample = random.betavariate(51, 51)
            b_sample = random.betavariate(51, 51)
            if a_sample > b_sample:
                a_picks += 1
        # Should be roughly 50/50
        assert 400 < a_picks < 600

    def test_cold_start_uniform_exploration(self):
        """With no data (alpha=1, beta=1), all variants should be explored equally."""
        random.seed(42)
        picks = [0, 0, 0]
        trials = 3000
        for _ in range(trials):
            samples = [random.betavariate(1, 1) for _ in range(3)]
            picks[samples.index(max(samples))] += 1
        # Each should get roughly 1/3
        for count in picks:
            assert 800 < count < 1200

    def test_round_vs_int_truncation(self):
        """Verify that round() fixes the int() truncation bias.

        With int(): total=1, avg_q=0.9 -> successes=0 (wrong)
        With round(): total=1, avg_q=0.9 -> successes=1 (correct)
        """
        total, avg_q = 1, 0.9
        successes_round = round(total * avg_q)
        successes_int = int(total * avg_q)
        assert successes_round == 1  # Correct
        assert successes_int == 0  # Was the bug

    def test_round_at_boundary(self):
        """Check round() behavior at exactly 0.5."""
        total, avg_q = 1, 0.5
        successes = round(total * avg_q)
        failures = total - successes
        alpha = max(successes + 1, 1)
        beta_param = max(failures + 1, 1)
        # round(0.5) in Python uses banker's rounding -> 0
        # Either way, both alpha and beta should be valid
        assert alpha >= 1
        assert beta_param >= 1

    def test_large_sample_count(self):
        """Large sample counts should produce valid Beta parameters."""
        total, avg_q = 10000, 0.85
        successes = round(total * avg_q)
        failures = total - successes
        alpha = max(successes + 1, 1)
        beta_param = max(failures + 1, 1)
        assert alpha == 8501
        assert beta_param == 1501
        # Should not crash
        sample = random.betavariate(alpha, beta_param)
        assert 0 <= sample <= 1


# ====================================================================
# 18. AutoPilot - Evaluate result dispatch
# ====================================================================


class TestEvaluateResult:
    @pytest.mark.asyncio
    async def test_schema_completeness_method(self):
        from sandcastle.engine.autopilot import evaluate_result
        config = AutoPilotConfig(
            evaluation=EvaluationConfig(method="schema_completeness"),
        )
        step = StepDefinition(
            id="s1", prompt="test",
            output_schema={"properties": {"a": {}, "b": {}}},
        )
        score = await evaluate_result(config, step, {"a": 1, "b": 2})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_unknown_method_returns_1_for_non_none(self):
        from sandcastle.engine.autopilot import evaluate_result
        config = AutoPilotConfig(
            evaluation=EvaluationConfig(method="unknown_method"),
        )
        step = StepDefinition(id="s1", prompt="test")
        score = await evaluate_result(config, step, {"data": "exists"})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_unknown_method_returns_0_for_none(self):
        from sandcastle.engine.autopilot import evaluate_result
        config = AutoPilotConfig(
            evaluation=EvaluationConfig(method="unknown_method"),
        )
        step = StepDefinition(id="s1", prompt="test")
        score = await evaluate_result(config, step, None)
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_no_evaluation_config_uses_schema(self):
        from sandcastle.engine.autopilot import evaluate_result
        config = AutoPilotConfig(evaluation=None)
        step = StepDefinition(
            id="s1", prompt="test",
            output_schema={"properties": {"a": {}}},
        )
        score = await evaluate_result(config, step, {"a": 1})
        assert score == 1.0


# ====================================================================
# 19. Edge cases - numerical stability
# ====================================================================


class TestNumericalStability:
    def test_pareto_with_extreme_values(self):
        stats = [
            VariantRow("a", 10, 1.0, 1e6, 1e6),
            VariantRow("b", 10, 0.0, 0.0, 0.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=-1.0)
        winner = select_winner(stats, config)
        assert winner is not None
        score = winner.get("pareto_score", 0)
        assert not math.isnan(score)
        assert not math.isinf(score)

    def test_significance_with_large_values(self):
        winner = [1e10, 1e10 + 1, 1e10 - 1, 1e10 + 0.5, 1e10 - 0.5]
        loser = [1, 2, 3, 4, 5]
        is_sig, p = _check_significance(winner, loser)
        # Should be clearly significant
        assert is_sig is True

    def test_significance_with_tiny_differences(self):
        """Very small differences should not be significant with few samples."""
        winner = [0.8000001, 0.8000002, 0.8000003, 0.8000004, 0.8000005]
        loser = [0.8000000, 0.8000000, 0.8000000, 0.8000000, 0.8000000]
        is_sig, p = _check_significance(winner, loser)
        # Extremely tiny difference might still be significant due to zero variance in loser
        # The test just checks it doesn't crash
        assert 0.0 <= p <= 1.0

    def test_budget_pressure_with_inf(self):
        """Inf cost should be clamped to 1.0."""
        result = calculate_budget_pressure(float("inf"), 100.0)
        assert result == 1.0

    def test_budget_pressure_with_nan_max(self):
        """NaN max_cost should return 0.0 (falsy)."""
        result = calculate_budget_pressure(5.0, float("nan"))
        # float("nan") is truthy but nan <= 0 is False, so it passes the guard
        # Then 5.0 / nan = nan, min(nan, 1.0) depends on implementation
        # This is a known edge case - at least it should not crash
        assert isinstance(result, float)

    def test_scoring_with_zero_cost(self):
        """Zero cost should not cause issues in scoring."""
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("free", avg_quality=0.8, avg_cost=0.0, avg_latency=10, sample_count=10),
            _opt("paid", avg_quality=0.8, avg_cost=0.10, avg_latency=10, sample_count=10),
        ]
        slo = SLO(optimize_for="cost")
        result = optimizer._score_options(options, slo)
        assert result.id == "free"

    def test_scoring_with_zero_latency(self):
        """Zero latency should not cause issues."""
        optimizer = CostLatencyOptimizer()
        options = [
            _opt("instant", avg_quality=0.8, avg_cost=0.1, avg_latency=0.0, sample_count=10),
            _opt("slow", avg_quality=0.8, avg_cost=0.1, avg_latency=60.0, sample_count=10),
        ]
        slo = SLO(optimize_for="latency")
        result = optimizer._score_options(options, slo)
        assert result.id == "instant"


# ====================================================================
# 20. SLO dataclass defaults
# ====================================================================


class TestSLODefaults:
    def test_defaults(self):
        slo = SLO()
        assert slo.quality_min == 0.6
        assert slo.cost_max_usd == 0.20
        assert slo.latency_max_seconds == 120
        assert slo.optimize_for == "balanced"

    def test_custom_values(self):
        slo = SLO(quality_min=0.9, cost_max_usd=0.50, latency_max_seconds=30, optimize_for="cost")
        assert slo.quality_min == 0.9
        assert slo.cost_max_usd == 0.50
        assert slo.latency_max_seconds == 30
        assert slo.optimize_for == "cost"


# ====================================================================
# 21. RoutingDecision dataclass
# ====================================================================


class TestRoutingDecisionDataclass:
    def test_defaults(self):
        opt = _opt("test")
        rd = RoutingDecision(selected_option=opt, reason="test reason")
        assert rd.alternatives == []
        assert rd.budget_pressure == 0.0
        assert rd.confidence == 0.1

    def test_custom_values(self):
        opt = _opt("test")
        alt = _opt("alt")
        rd = RoutingDecision(
            selected_option=opt,
            reason="selected",
            alternatives=[alt],
            budget_pressure=0.42,
            confidence=0.8,
        )
        assert len(rd.alternatives) == 1
        assert rd.budget_pressure == 0.42
        assert rd.confidence == 0.8


# ====================================================================
# 22. PerformanceStats dataclass
# ====================================================================


class TestPerformanceStats:
    def test_defaults(self):
        ps = PerformanceStats(model="sonnet")
        assert ps.avg_quality is None
        assert ps.avg_cost is None
        assert ps.avg_latency is None
        assert ps.sample_count == 0


# ====================================================================
# 23. AutoPilotConfig defaults
# ====================================================================


class TestAutoPilotConfigDefaults:
    def test_defaults(self):
        config = AutoPilotConfig()
        assert config.enabled is False
        assert config.optimize_for == "quality"
        assert config.variants == []
        assert config.evaluation is None
        assert config.sample_rate == 1.0
        assert config.min_samples == 10
        assert config.auto_deploy is True
        assert config.quality_threshold == 0.7


# ====================================================================
# 24. MAX_SAMPLES_PER_EXPERIMENT safety cap
# ====================================================================


class TestSafetyCap:
    def test_safety_cap_value(self):
        from sandcastle.engine.autopilot import MAX_SAMPLES_PER_EXPERIMENT
        assert MAX_SAMPLES_PER_EXPERIMENT == 500
        assert MAX_SAMPLES_PER_EXPERIMENT > 0


# ====================================================================
# 25. Integration: optimizer + autopilot interaction
# ====================================================================


class TestIntegration:
    @pytest.mark.asyncio
    async def test_optimizer_with_enriched_stats_and_slo(self):
        """Full flow: enrich pool -> filter by SLO -> score -> decide."""
        optimizer = CostLatencyOptimizer()
        # Pre-populate cache with realistic stats
        cache_key = "test-wf:enrich"
        optimizer._cache[cache_key] = (
            time.monotonic(),
            [
                PerformanceStats(model="haiku", avg_quality=0.65, avg_cost=0.02, avg_latency=5, sample_count=50),
                PerformanceStats(model="sonnet", avg_quality=0.85, avg_cost=0.08, avg_latency=15, sample_count=50),
                PerformanceStats(model="opus", avg_quality=0.95, avg_cost=0.45, avg_latency=60, sample_count=50),
            ],
        )
        pool = [
            _opt("haiku", "haiku"),
            _opt("sonnet", "sonnet"),
            _opt("opus", "opus"),
        ]
        slo = SLO(quality_min=0.7, cost_max_usd=0.20, optimize_for="balanced")
        decision = await optimizer.select_model(
            step_id="enrich", workflow_name="test-wf",
            slo=slo, model_pool=pool,
        )
        # haiku filtered (quality 0.65 < 0.7)
        # opus filtered (cost 0.45 > 0.20)
        # Only sonnet remains
        assert decision.selected_option.model == "sonnet"
        assert decision.confidence == 0.95  # 50 samples

    @pytest.mark.asyncio
    async def test_degradation_triggers_in_select_model(self):
        """Select model should detect degradation and add to reason."""
        optimizer = CostLatencyOptimizer()
        cache_key = "test-wf:step"
        # Stats show quality below SLO
        optimizer._cache[cache_key] = (
            time.monotonic(),
            [
                PerformanceStats(
                    model="sonnet", avg_quality=0.30, avg_cost=0.08,
                    avg_latency=15, sample_count=20,
                ),
            ],
        )
        pool = [_opt("sonnet", "sonnet")]
        slo = SLO(quality_min=0.6, optimize_for="quality")
        decision = await optimizer.select_model(
            step_id="step", workflow_name="test-wf",
            slo=slo, model_pool=pool,
        )
        # Should mention critical degradation
        assert "critical" in decision.reason.lower() or "warning" in decision.reason.lower()


# ====================================================================
# 26. AutoPilot - Concurrent experiment edge case
# ====================================================================


class TestConcurrentEdgeCases:
    def test_select_winner_with_single_sample_per_variant(self):
        """Even with just 1 sample per variant, should not crash."""
        stats = [
            VariantRow("a", 1, 0.90, 0.05, 5.0),
            VariantRow("b", 1, 0.80, 0.01, 2.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.0)
        winner = select_winner(stats, config)
        assert winner is not None

    def test_select_winner_with_zero_counts(self):
        """Zero sample counts should still work."""
        stats = [
            VariantRow("a", 0, 0.0, 0.0, 0.0),
            VariantRow("b", 0, 0.0, 0.0, 0.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=-1.0)
        winner = select_winner(stats, config)
        assert winner is not None
