"""Tests for the Real-time Cost-Latency Optimizer."""

from __future__ import annotations

import pytest

from sandcastle.engine.dag import (
    parse_yaml_string,
    validate,
)
from sandcastle.engine.optimizer import (
    SLO,
    CostLatencyOptimizer,
    ModelOption,
    PerformanceStats,
    calculate_budget_pressure,
)

# --- YAML parsing ---


SLO_WORKFLOW_YAML = """
name: optimizer-test
description: Workflow with SLO
sandstorm_url: http://localhost:8000
steps:
  - id: enrich
    prompt: "Enrich data"
    slo:
      quality_min: 0.7
      cost_max_usd: 0.15
      latency_max_seconds: 60
      optimize_for: cost
    model_pool:
      - id: fast-cheap
        model: haiku
        max_turns: 5
      - id: balanced
        model: sonnet
        max_turns: 10
      - id: thorough
        model: opus
        max_turns: 20

  - id: analyze
    prompt: "Analyze results"
    slo:
      quality_min: 0.8
      optimize_for: quality
"""

AUTO_POOL_YAML = """
name: auto-pool-test
description: Workflow with auto model pool
sandstorm_url: http://localhost:8000
steps:
  - id: step1
    prompt: "Do something"
    slo:
      quality_min: 0.6
      optimize_for: balanced
"""


def test_parse_slo_config():
    """SLO configuration is parsed correctly from YAML."""
    wf = parse_yaml_string(SLO_WORKFLOW_YAML)
    step = wf.get_step("enrich")
    assert step.slo is not None
    assert step.slo.quality_min == 0.7
    assert step.slo.cost_max_usd == 0.15
    assert step.slo.latency_max_seconds == 60
    assert step.slo.optimize_for == "cost"


def test_parse_model_pool():
    """Model pool is parsed correctly from YAML."""
    wf = parse_yaml_string(SLO_WORKFLOW_YAML)
    step = wf.get_step("enrich")
    assert step.model_pool is not None
    assert len(step.model_pool) == 3
    assert step.model_pool[0].id == "fast-cheap"
    assert step.model_pool[0].model == "haiku"
    assert step.model_pool[0].max_turns == 5
    assert step.model_pool[1].model == "sonnet"
    assert step.model_pool[2].model == "opus"


def test_parse_slo_with_defaults():
    """SLO with partial config gets default values."""
    wf = parse_yaml_string(SLO_WORKFLOW_YAML)
    step = wf.get_step("analyze")
    assert step.slo is not None
    assert step.slo.quality_min == 0.8
    assert step.slo.cost_max_usd == 0.20  # default
    assert step.slo.latency_max_seconds == 120  # default
    assert step.slo.optimize_for == "quality"


def test_auto_model_pool():
    """SLO without explicit model_pool gets auto pool."""
    wf = parse_yaml_string(AUTO_POOL_YAML)
    step = wf.get_step("step1")
    assert step.slo is not None
    assert step.model_pool is not None
    assert len(step.model_pool) == 3
    assert step.model_pool[0].model == "haiku"
    assert step.model_pool[1].model == "sonnet"
    assert step.model_pool[2].model == "opus"


def test_step_without_slo():
    """Steps without SLO have no model_pool."""
    yaml = """
name: no-slo-test
description: No SLO
sandstorm_url: http://localhost:8000
steps:
  - id: basic
    prompt: "Basic step"
"""
    wf = parse_yaml_string(yaml)
    step = wf.get_step("basic")
    assert step.slo is None
    assert step.model_pool is None


def test_validate_invalid_optimize_for():
    """Invalid optimize_for value is caught by validation."""
    yaml = """
name: invalid-slo
description: Bad SLO
sandstorm_url: http://localhost:8000
steps:
  - id: bad
    prompt: "Bad step"
    slo:
      optimize_for: invalid_value
"""
    wf = parse_yaml_string(yaml)
    errors = validate(wf)
    assert any("optimize_for" in e for e in errors)


# --- Scoring ---


def _make_option(
    option_id: str = "test",
    model: str = "sonnet",
    max_turns: int = 10,
    avg_quality: float | None = None,
    avg_cost: float | None = None,
    avg_latency: float | None = None,
    sample_count: int = 0,
) -> ModelOption:
    """Helper to create a ModelOption for testing."""
    return ModelOption(
        id=option_id,
        model=model,
        max_turns=max_turns,
        avg_quality=avg_quality,
        avg_cost=avg_cost,
        avg_latency=avg_latency,
        sample_count=sample_count,
    )


@pytest.mark.asyncio
async def test_score_optimize_cost():
    """Cost optimization selects cheapest option."""
    optimizer = CostLatencyOptimizer()
    options = [
        _make_option("haiku", "haiku", avg_quality=0.7, avg_cost=0.02, sample_count=10),
        _make_option("sonnet", "sonnet", avg_quality=0.85, avg_cost=0.10, sample_count=10),
        _make_option("opus", "opus", avg_quality=0.95, avg_cost=0.50, sample_count=10),
    ]
    slo = SLO(quality_min=0.6, cost_max_usd=1.0, optimize_for="cost")
    result = optimizer._score_options(options, slo)
    assert result.model == "haiku"


@pytest.mark.asyncio
async def test_score_optimize_quality():
    """Quality optimization selects best quality option."""
    optimizer = CostLatencyOptimizer()
    options = [
        _make_option("haiku", "haiku", avg_quality=0.7, avg_cost=0.02, sample_count=10),
        _make_option("sonnet", "sonnet", avg_quality=0.85, avg_cost=0.10, sample_count=10),
        _make_option("opus", "opus", avg_quality=0.95, avg_cost=0.50, sample_count=10),
    ]
    slo = SLO(quality_min=0.6, cost_max_usd=1.0, optimize_for="quality")
    result = optimizer._score_options(options, slo)
    assert result.model == "opus"


@pytest.mark.asyncio
async def test_score_optimize_latency():
    """Latency optimization selects fastest option."""
    optimizer = CostLatencyOptimizer()
    options = [
        _make_option("haiku", "haiku", avg_latency=10, avg_quality=0.7, sample_count=10),
        _make_option("sonnet", "sonnet", avg_latency=30, avg_quality=0.85, sample_count=10),
        _make_option("opus", "opus", avg_latency=90, avg_quality=0.95, sample_count=10),
    ]
    slo = SLO(quality_min=0.6, optimize_for="latency")
    result = optimizer._score_options(options, slo)
    assert result.model == "haiku"


# --- SLO filtering ---


@pytest.mark.asyncio
async def test_slo_filters_below_quality():
    """Options below quality_min are filtered out."""
    optimizer = CostLatencyOptimizer()
    slo = SLO(quality_min=0.8, cost_max_usd=1.0, optimize_for="cost")
    pool = [
        _make_option("haiku", "haiku", avg_quality=0.65, avg_cost=0.02, sample_count=10),
        _make_option("sonnet", "sonnet", avg_quality=0.85, avg_cost=0.10, sample_count=10),
    ]
    decision = await optimizer.select_model(
        step_id="test", workflow_name="test", slo=slo, model_pool=pool
    )
    # Haiku should be filtered (0.65 < 0.8), sonnet should be selected
    assert decision.selected_option.model == "sonnet"


@pytest.mark.asyncio
async def test_slo_filters_above_cost():
    """Options above cost_max_usd are filtered out."""
    optimizer = CostLatencyOptimizer()
    slo = SLO(quality_min=0.0, cost_max_usd=0.15, optimize_for="quality")
    pool = [
        _make_option("haiku", "haiku", avg_quality=0.7, avg_cost=0.02, sample_count=10),
        _make_option("opus", "opus", avg_quality=0.95, avg_cost=0.50, sample_count=10),
    ]
    decision = await optimizer.select_model(
        step_id="test", workflow_name="test", slo=slo, model_pool=pool
    )
    # Opus should be filtered (0.50 > 0.15), haiku should be selected
    assert decision.selected_option.model == "haiku"


# --- Budget pressure ---


@pytest.mark.asyncio
async def test_budget_pressure_critical():
    """Critical budget pressure forces cheapest option."""
    optimizer = CostLatencyOptimizer()
    slo = SLO(quality_min=0.0, cost_max_usd=1.0, optimize_for="quality")
    pool = [
        _make_option("haiku", "haiku", avg_quality=0.7, avg_cost=0.02, sample_count=10),
        _make_option("opus", "opus", avg_quality=0.95, avg_cost=0.50, sample_count=10),
    ]
    decision = await optimizer.select_model(
        step_id="test",
        workflow_name="test",
        slo=slo,
        model_pool=pool,
        budget_pressure=0.95,
    )
    assert decision.selected_option.model == "haiku"
    assert "critical" in decision.reason.lower()


@pytest.mark.asyncio
async def test_budget_pressure_warning():
    """Warning budget pressure biases toward cheaper."""
    optimizer = CostLatencyOptimizer()
    slo = SLO(quality_min=0.0, cost_max_usd=1.0, optimize_for="quality")
    pool = [
        _make_option("haiku", "haiku", avg_quality=0.7, avg_cost=0.02, sample_count=10),
        _make_option("opus", "opus", avg_quality=0.95, avg_cost=0.50, sample_count=10),
    ]
    decision = await optimizer.select_model(
        step_id="test",
        workflow_name="test",
        slo=slo,
        model_pool=pool,
        budget_pressure=0.75,
    )
    # Budget pressure should bias toward cheaper
    assert "pressure" in decision.reason.lower()


def test_calculate_budget_pressure():
    """Budget pressure calculation works correctly."""
    assert calculate_budget_pressure(0.0, 10.0) == 0.0
    assert calculate_budget_pressure(5.0, 10.0) == 0.5
    assert calculate_budget_pressure(9.0, 10.0) == 0.9
    assert calculate_budget_pressure(10.0, 10.0) == 1.0
    assert calculate_budget_pressure(15.0, 10.0) == 1.0  # Capped at 1.0
    assert calculate_budget_pressure(5.0, None) == 0.0
    assert calculate_budget_pressure(5.0, 0) == 0.0


# --- Cold start ---


@pytest.mark.asyncio
async def test_cold_start_uses_fallback():
    """Cold start (no data) uses balanced default."""
    optimizer = CostLatencyOptimizer()
    slo = SLO(quality_min=0.6, optimize_for="cost")
    pool = [
        _make_option("haiku", "haiku"),
        _make_option("sonnet", "sonnet"),
        _make_option("opus", "opus"),
    ]
    decision = await optimizer.select_model(
        step_id="new-step", workflow_name="new-wf", slo=slo, model_pool=pool
    )
    assert "cold start" in decision.reason.lower()
    assert decision.confidence == 0.1


# --- Confidence ---


def test_confidence_levels():
    """Confidence scales with sample count."""
    optimizer = CostLatencyOptimizer()
    assert optimizer._calculate_confidence(_make_option(sample_count=0)) == 0.1
    assert optimizer._calculate_confidence(_make_option(sample_count=1)) == 0.3
    assert optimizer._calculate_confidence(_make_option(sample_count=5)) == 0.6
    assert optimizer._calculate_confidence(_make_option(sample_count=20)) == 0.8
    assert optimizer._calculate_confidence(_make_option(sample_count=50)) == 0.95


# --- Fallback ---


def test_fallback_picks_middle():
    """Fallback picks the middle-cost option."""
    optimizer = CostLatencyOptimizer()
    pool = [
        _make_option("cheap", avg_cost=0.01),
        _make_option("mid", avg_cost=0.10),
        _make_option("expensive", avg_cost=0.50),
    ]
    result = optimizer._get_fallback(pool)
    assert result.id == "mid"


# --- Enrichment ---


def test_enrich_pool():
    """Performance stats are merged into model options."""
    optimizer = CostLatencyOptimizer()
    pool = [
        _make_option("haiku", "haiku"),
        _make_option("sonnet", "sonnet"),
    ]
    stats = [
        PerformanceStats(model="haiku", avg_quality=0.7, avg_cost=0.02, sample_count=25),
        PerformanceStats(model="sonnet", avg_quality=0.85, avg_cost=0.10, sample_count=50),
    ]
    enriched = optimizer._enrich_pool(pool, stats)
    assert enriched[0].avg_quality == 0.7
    assert enriched[0].avg_cost == 0.02
    assert enriched[0].sample_count == 25
    assert enriched[1].avg_quality == 0.85
    assert enriched[1].sample_count == 50


def test_enrich_pool_missing_stats():
    """Options without stats keep their defaults."""
    optimizer = CostLatencyOptimizer()
    pool = [_make_option("haiku", "haiku"), _make_option("opus", "opus")]
    stats = [PerformanceStats(model="haiku", avg_cost=0.02, sample_count=10)]
    enriched = optimizer._enrich_pool(pool, stats)
    assert enriched[0].avg_cost == 0.02
    assert enriched[1].avg_cost is None  # No stats for opus
    assert enriched[1].sample_count == 0


# --- Cache ---


@pytest.mark.asyncio
async def test_cache_works():
    """Stats are cached and reused."""
    optimizer = CostLatencyOptimizer()
    # Pre-populate cache
    import time
    optimizer._cache["test-wf:step1"] = (
        time.monotonic(),
        [PerformanceStats(model="sonnet", avg_cost=0.10, sample_count=5)],
    )
    stats = await optimizer._get_performance_stats("step1", "test-wf")
    assert len(stats) == 1
    assert stats[0].model == "sonnet"


# --- DB model ---


def test_routing_decision_model_exists():
    """RoutingDecision model exists and has expected fields."""
    from sandcastle.models.db import RoutingDecision

    assert hasattr(RoutingDecision, "run_id")
    assert hasattr(RoutingDecision, "step_id")
    assert hasattr(RoutingDecision, "selected_model")
    assert hasattr(RoutingDecision, "budget_pressure")
    assert hasattr(RoutingDecision, "confidence")
    assert hasattr(RoutingDecision, "alternatives")
    assert hasattr(RoutingDecision, "slo")
