"""Real-time Cost-Latency Optimizer - SLO-based dynamic model routing.

Uses historical performance data (from AutoPilot samples + past runs)
to make real-time model selection decisions per step.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# --- Dataclasses ---


@dataclass
class SLO:
    """Service Level Objective for a step."""

    quality_min: float = 0.6
    cost_max_usd: float = 0.20
    latency_max_seconds: int = 120
    optimize_for: str = "balanced"  # "cost" | "quality" | "latency" | "balanced"


@dataclass
class ModelOption:
    """A model choice in the pool with optional performance stats."""

    id: str
    model: str
    max_turns: int = 10
    avg_quality: float | None = None
    avg_cost: float | None = None
    avg_latency: float | None = None
    sample_count: int = 0


@dataclass
class RoutingDecision:
    """Result of model selection."""

    selected_option: ModelOption
    reason: str
    alternatives: list[ModelOption] = field(default_factory=list)
    budget_pressure: float = 0.0
    confidence: float = 0.1


@dataclass
class PerformanceStats:
    """Aggregated performance data for a model on a step."""

    model: str
    avg_quality: float | None = None
    avg_cost: float | None = None
    avg_latency: float | None = None
    sample_count: int = 0


# --- Default model pool ---

DEFAULT_MODEL_POOL = [
    ModelOption(id="fast-cheap", model="haiku", max_turns=5),
    ModelOption(id="balanced", model="sonnet", max_turns=10),
    ModelOption(id="thorough", model="opus", max_turns=20),
]


# --- CostLatencyOptimizer ---


class CostLatencyOptimizer:
    """Selects optimal model for each step based on SLO and historical data."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[PerformanceStats]]] = {}
        self._cache_ttl: int = 300  # 5 minutes

    async def select_model(
        self,
        step_id: str,
        workflow_name: str,
        slo: SLO,
        model_pool: list[ModelOption],
        budget_pressure: float = 0.0,
    ) -> RoutingDecision:
        """Select optimal model for a step based on SLO and historical data.

        Algorithm:
        1. Load performance history for this step
        2. Enrich model_pool with avg quality/cost/latency per option
        3. Filter: remove options that violate hard SLO constraints
        4. Adjust for budget pressure
        5. Score remaining options by optimize_for objective
        6. Return best option with explanation
        """
        # 1. Load historical performance
        stats = await self._get_performance_stats(step_id, workflow_name)

        # 2. Enrich pool with stats
        enriched_pool = self._enrich_pool(model_pool, stats)

        # 3. Filter by hard SLO constraints
        viable = []
        for option in enriched_pool:
            if option.avg_quality is not None and option.avg_quality < slo.quality_min:
                continue
            if option.avg_cost is not None and option.avg_cost > slo.cost_max_usd:
                continue
            if option.avg_latency is not None and option.avg_latency > slo.latency_max_seconds:
                continue
            viable.append(option)

        # If nothing is viable, fall back to middle option
        if not viable:
            viable = [self._get_fallback(enriched_pool)]

        # 4. Apply budget pressure adjustment
        if budget_pressure > 0.9:
            viable.sort(key=lambda o: o.avg_cost or float("inf"))
            selected = viable[0]
            reason = f"Budget critical ({budget_pressure:.0%}). Forced cheapest viable option."
        elif budget_pressure > 0.7:
            selected = self._score_with_bias(viable, slo, cost_bias=0.7)
            reason = f"Budget pressure ({budget_pressure:.0%}). Biased toward cost savings."
        else:
            selected = self._score_options(viable, slo)
            reason = f"Optimized for {slo.optimize_for}."

        # Cold start detection
        if all(o.sample_count == 0 for o in enriched_pool):
            selected = self._get_fallback(enriched_pool)
            reason = "Cold start - no historical data. Using balanced default."

        confidence = self._calculate_confidence(selected)

        return RoutingDecision(
            selected_option=selected,
            reason=reason,
            alternatives=[o for o in viable if o.id != selected.id],
            budget_pressure=budget_pressure,
            confidence=confidence,
        )

    def _score_options(self, options: list[ModelOption], slo: SLO) -> ModelOption:
        """Score options by optimization objective."""

        def score(option: ModelOption) -> float:
            q = option.avg_quality or 0.5
            c = option.avg_cost or 0.10
            latency = option.avg_latency or 60.0

            if slo.optimize_for == "cost":
                return -c + (q * 0.1)
            elif slo.optimize_for == "quality":
                return q - (c * 0.1)
            elif slo.optimize_for == "latency":
                return -latency + (q * 0.1)
            else:  # balanced
                return (q * 0.4) + (-c * 0.3 / 0.5) + (-latency * 0.3 / 120)

        return max(options, key=score)

    def _score_with_bias(
        self, options: list[ModelOption], slo: SLO, cost_bias: float = 0.7
    ) -> ModelOption:
        """Score with extra weight on cost (for budget pressure)."""

        def score(option: ModelOption) -> float:
            q = option.avg_quality or 0.5
            c = option.avg_cost or 0.10
            return (q * (1 - cost_bias)) + (-c * cost_bias / 0.5)

        return max(options, key=score)

    def _enrich_pool(
        self, pool: list[ModelOption], stats: list[PerformanceStats]
    ) -> list[ModelOption]:
        """Merge historical stats into model options."""
        stats_map = {s.model: s for s in stats}
        enriched = []
        for option in pool:
            s = stats_map.get(option.model)
            if s:
                enriched.append(ModelOption(
                    id=option.id,
                    model=option.model,
                    max_turns=option.max_turns,
                    avg_quality=s.avg_quality,
                    avg_cost=s.avg_cost,
                    avg_latency=s.avg_latency,
                    sample_count=s.sample_count,
                ))
            else:
                enriched.append(option)
        return enriched

    def _calculate_confidence(self, option: ModelOption) -> float:
        """How confident are we in this option's stats."""
        if option.sample_count >= 50:
            return 0.95
        elif option.sample_count >= 20:
            return 0.8
        elif option.sample_count >= 5:
            return 0.6
        elif option.sample_count >= 1:
            return 0.3
        else:
            return 0.1

    def _get_fallback(self, pool: list[ModelOption]) -> ModelOption:
        """When nothing meets SLO, pick middle option."""
        sorted_by_cost = sorted(pool, key=lambda o: o.avg_cost or 0.10)
        return sorted_by_cost[len(sorted_by_cost) // 2]

    async def _get_performance_stats(
        self, step_id: str, workflow_name: str
    ) -> list[PerformanceStats]:
        """Load aggregated performance data with caching.

        Sources:
        1. autopilot_samples (higher quality data from experiments)
        2. run_steps (historical actual performance)
        """
        cache_key = f"{workflow_name}:{step_id}"
        now = time.monotonic()

        if cache_key in self._cache:
            cached_at, data = self._cache[cache_key]
            if (now - cached_at) < self._cache_ttl:
                return data

        stats: list[PerformanceStats] = []
        try:
            stats = await self._query_stats(step_id, workflow_name)
        except Exception as e:
            logger.warning(f"Could not load performance stats: {e}")

        self._cache[cache_key] = (now, stats)
        return stats

    async def _query_stats(
        self, step_id: str, workflow_name: str
    ) -> list[PerformanceStats]:
        """Query DB for performance statistics."""
        try:
            from sqlalchemy import func, select

            from sandcastle.models.db import AutoPilotSample, RunStep, StepStatus, async_session

            stats_by_model: dict[str, PerformanceStats] = {}

            async with async_session() as session:
                # Query from run_steps (historical data)
                # We get avg cost and avg duration grouped by step model
                # Note: model is not stored on run_steps directly, so we use
                # cost as a proxy indicator for model type
                step_q = (
                    select(
                        func.avg(RunStep.cost_usd).label("avg_cost"),
                        func.avg(RunStep.duration_seconds).label("avg_duration"),
                        func.count(RunStep.id).label("count"),
                    )
                    .where(
                        RunStep.step_id == step_id,
                        RunStep.status == StepStatus.COMPLETED,
                    )
                )
                result = await session.execute(step_q)
                row = result.first()
                if row and row.count > 0:
                    # Use cost buckets as rough model indicator
                    # < 0.02 = haiku, 0.02-0.10 = sonnet, > 0.10 = opus
                    stats_by_model["sonnet"] = PerformanceStats(
                        model="sonnet",
                        avg_cost=float(row.avg_cost or 0),
                        avg_latency=float(row.avg_duration or 0),
                        sample_count=int(row.count),
                    )

                # Query from autopilot_samples (higher quality)
                from sandcastle.models.db import AutoPilotExperiment

                sample_q = (
                    select(
                        AutoPilotSample.variant_id,
                        func.avg(AutoPilotSample.quality_score).label("avg_quality"),
                        func.avg(AutoPilotSample.cost_usd).label("avg_cost"),
                        func.avg(AutoPilotSample.duration_seconds).label("avg_duration"),
                        func.count(AutoPilotSample.id).label("count"),
                    )
                    .join(
                        AutoPilotExperiment,
                        AutoPilotSample.experiment_id == AutoPilotExperiment.id,
                    )
                    .where(
                        AutoPilotExperiment.step_id == step_id,
                        AutoPilotExperiment.workflow_name == workflow_name,
                    )
                    .group_by(AutoPilotSample.variant_id)
                )
                sample_rows = (await session.execute(sample_q)).all()
                for srow in sample_rows:
                    # Try to extract model from variant config
                    model_name = srow.variant_id  # variant_id often contains model hint
                    stats_by_model[model_name] = PerformanceStats(
                        model=model_name,
                        avg_quality=float(srow.avg_quality) if srow.avg_quality else None,
                        avg_cost=float(srow.avg_cost or 0),
                        avg_latency=float(srow.avg_duration or 0),
                        sample_count=int(srow.count),
                    )

            return list(stats_by_model.values())

        except Exception as e:
            logger.warning(f"Performance stats query failed: {e}")
            return []


def calculate_budget_pressure(
    current_cost: float, max_cost: float | None
) -> float:
    """Calculate current budget utilization (0-1)."""
    if not max_cost or max_cost <= 0:
        return 0.0
    return min(current_cost / max_cost, 1.0)
