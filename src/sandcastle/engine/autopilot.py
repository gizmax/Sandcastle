"""AutoPilot - self-optimizing workflow step experiments."""

from __future__ import annotations

import dataclasses
import logging
import math
import random
import uuid
from datetime import datetime, timezone
from typing import Any

from sandcastle.engine.dag import AutoPilotConfig, StepDefinition, VariantConfig

logger = logging.getLogger(__name__)

# Progressive rollout stage definitions
ROLLOUT_STAGES = {
    "canary": 0.10,    # 10% traffic
    "partial": 0.50,   # 50% traffic
    "full": 1.0,       # 100% traffic
}

ROLLOUT_ORDER = ["canary", "partial", "full"]


async def get_or_create_experiment(
    workflow_name: str,
    step_id: str,
    config: AutoPilotConfig,
) -> Any:
    """Find an active experiment for this workflow+step, or create a new one."""
    from sqlalchemy import select

    from sandcastle.models.db import (
        AutoPilotExperiment,
        ExperimentStatus,
        async_session,
    )

    async with async_session() as session:
        stmt = select(AutoPilotExperiment).where(
            AutoPilotExperiment.workflow_name == workflow_name,
            AutoPilotExperiment.step_id == step_id,
            AutoPilotExperiment.status == ExperimentStatus.RUNNING,
        )
        result = await session.execute(stmt)
        experiment = result.scalar_one_or_none()

        if experiment:
            return experiment

        # Create new experiment
        experiment = AutoPilotExperiment(
            workflow_name=workflow_name,
            step_id=step_id,
            status=ExperimentStatus.RUNNING,
            optimize_for=config.optimize_for,
            config={
                "variants": [
                    {"id": v.id, "model": v.model, "prompt": v.prompt, "max_turns": v.max_turns}
                    for v in config.variants
                ],
                "min_samples": config.min_samples,
                "auto_deploy": config.auto_deploy,
                "quality_threshold": config.quality_threshold,
                "sample_rate": config.sample_rate,
            },
        )
        session.add(experiment)
        await session.commit()
        await session.refresh(experiment)
        return experiment


async def pick_variant(
    experiment_id: uuid.UUID,
    variants: list[VariantConfig],
) -> VariantConfig | None:
    """Pick variant using Thompson Sampling (Beta-Bernoulli bandit)."""
    from sqlalchemy import func, select

    from sandcastle.models.db import AutoPilotSample, async_session

    if not variants:
        return None

    async with async_session() as session:
        # Get per-variant quality stats
        stmt = (
            select(
                AutoPilotSample.variant_id,
                func.count(AutoPilotSample.id).label("total"),
                func.avg(AutoPilotSample.quality_score).label("avg_quality"),
            )
            .where(AutoPilotSample.experiment_id == experiment_id)
            .group_by(AutoPilotSample.variant_id)
        )
        result = await session.execute(stmt)
        stats = {
            row.variant_id: (row.total, float(row.avg_quality or 0.5))
            for row in result.all()
        }

    # Thompson Sampling: sample from Beta distribution for each variant
    best_sample = -1.0
    selected = variants[0]

    for variant in variants:
        total, avg_q = stats.get(variant.id, (0, 0.5))
        # Convert to Beta distribution parameters
        # alpha = successes + 1, beta = failures + 1
        successes = int(total * avg_q)
        failures = total - successes
        alpha = max(successes + 1, 1)
        beta_param = max(failures + 1, 1)
        # Sample from Beta distribution
        sample = random.betavariate(alpha, beta_param)
        if sample > best_sample:
            best_sample = sample
            selected = variant

    return selected


def apply_variant(
    step: StepDefinition,
    variant: VariantConfig,
) -> StepDefinition:
    """Create a modified StepDefinition from a variant config."""
    overrides: dict[str, Any] = {"autopilot": None}  # Don't recurse
    if variant.prompt:
        overrides["prompt"] = variant.prompt
    if variant.model:
        overrides["model"] = variant.model
    if variant.max_turns:
        overrides["max_turns"] = variant.max_turns
    return dataclasses.replace(step, **overrides)


async def evaluate_result(
    config: AutoPilotConfig,
    step: StepDefinition,
    output: Any,
) -> float:
    """Evaluate the quality of a step result.

    Returns a score between 0.0 and 1.0.
    """
    method = config.evaluation.method if config.evaluation else "schema_completeness"

    if method == "schema_completeness":
        return _evaluate_schema_completeness(output, step.output_schema)

    if method == "llm_judge":
        return await _evaluate_llm_judge(output, config)

    # Default: simple non-null check
    return 1.0 if output is not None else 0.0


def _evaluate_schema_completeness(output: Any, schema: dict | None) -> float:
    """Score based on how many expected fields are present."""
    if schema is None:
        return 1.0 if output is not None else 0.0

    if not isinstance(output, dict):
        return 0.0

    properties = schema.get("properties", {})
    if not properties:
        return 1.0

    present = sum(1 for key in properties if key in output and output[key] is not None)
    return present / len(properties)


async def _evaluate_llm_judge(output: Any, config: AutoPilotConfig) -> float:
    """Use an LLM to judge output quality (via Sandshore haiku)."""
    try:
        from sandcastle.config import settings
        from sandcastle.engine.sandshore import get_sandshore_runtime

        criteria = config.evaluation.criteria if config.evaluation else "overall quality"
        output_str = str(output)[:2000]  # Truncate for efficiency

        prompt = (
            f"Rate the following output on a scale of 0.0 to 1.0 based on: {criteria}\n\n"
            f"Output:\n{output_str}\n\n"
            "Respond with ONLY a number between 0.0 and 1.0."
        )

        client = get_sandshore_runtime(
            anthropic_api_key=settings.anthropic_api_key,
            e2b_api_key=settings.e2b_api_key,
            sandbox_backend=settings.sandbox_backend,
            docker_image=settings.docker_image,
            docker_url=settings.docker_url or None,
            cloudflare_worker_url=settings.cloudflare_worker_url,
        )
        result = await client.query({
            "prompt": prompt,
            "model": "haiku",
            "max_turns": 1,
            "timeout": 30,
        })
        score = float(result.text.strip())
        return max(0.0, min(1.0, score))

    except Exception as e:
        logger.warning(f"LLM judge evaluation failed: {e}")
        return 0.5  # Default middle score on failure


async def save_sample(
    experiment_id: uuid.UUID,
    run_id: str,
    variant: VariantConfig,
    output: Any,
    quality_score: float,
    cost_usd: float,
    duration_seconds: float,
) -> None:
    """Record a sample result to the database."""
    from sandcastle.models.db import AutoPilotSample, async_session

    try:
        async with async_session() as session:
            sample = AutoPilotSample(
                experiment_id=experiment_id,
                run_id=uuid.UUID(run_id),
                variant_id=variant.id,
                variant_config={
                    "model": variant.model,
                    "prompt": variant.prompt[:200] if variant.prompt else None,
                    "max_turns": variant.max_turns,
                },
                output_data=output if isinstance(output, dict) else {"result": str(output)[:1000]},
                quality_score=quality_score,
                cost_usd=cost_usd,
                duration_seconds=duration_seconds,
            )
            session.add(sample)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save autopilot sample: {e}")


async def maybe_complete_experiment(
    experiment_id: uuid.UUID,
    config: AutoPilotConfig,
) -> dict | None:
    """Check if experiment has enough samples and select a winner.

    Returns winner info dict if experiment completed, None otherwise.
    """
    from sqlalchemy import func, select

    from sandcastle.models.db import (
        AutoPilotExperiment,
        AutoPilotSample,
        ExperimentStatus,
        async_session,
    )

    async with async_session() as session:
        # Count total samples
        count_stmt = select(func.count(AutoPilotSample.id)).where(
            AutoPilotSample.experiment_id == experiment_id
        )
        total = await session.scalar(count_stmt)

        if total < config.min_samples:
            return None

        # Get stats per variant
        stats_stmt = (
            select(
                AutoPilotSample.variant_id,
                func.count(AutoPilotSample.id).label("count"),
                func.avg(AutoPilotSample.quality_score).label("avg_quality"),
                func.avg(AutoPilotSample.cost_usd).label("avg_cost"),
                func.avg(AutoPilotSample.duration_seconds).label("avg_duration"),
            )
            .where(AutoPilotSample.experiment_id == experiment_id)
            .group_by(AutoPilotSample.variant_id)
        )
        result = await session.execute(stats_stmt)
        variant_stats = result.all()

    winner = select_winner(variant_stats, config)

    if winner and config.auto_deploy:
        # Check statistical significance before deploying
        async with async_session() as session:
            scores_stmt = (
                select(AutoPilotSample.variant_id, AutoPilotSample.quality_score)
                .where(
                    AutoPilotSample.experiment_id == experiment_id,
                    AutoPilotSample.quality_score.is_not(None),
                )
            )
            scores_result = await session.execute(scores_stmt)
            scores_by_variant: dict[str, list[float]] = {}
            for row in scores_result.all():
                scores_by_variant.setdefault(row.variant_id, []).append(
                    float(row.quality_score)
                )

        winner_scores = scores_by_variant.get(winner["variant_id"], [])
        # Collect runner-up scores (all non-winner variants)
        other_scores = []
        for vid, scores in scores_by_variant.items():
            if vid != winner["variant_id"]:
                other_scores.extend(scores)

        is_significant, p_value = _check_significance(winner_scores, other_scores)
        winner["p_value"] = p_value
        winner["is_significant"] = is_significant

        if not is_significant:
            logger.info(
                "AutoPilot experiment %s: winner %s not yet statistically "
                "significant (p=%.3f). Continuing.",
                experiment_id, winner["variant_id"], p_value,
            )
            return None  # Keep running - not enough evidence yet

        # Start progressive rollout instead of immediate full deploy
        async with async_session() as session:
            experiment = await session.get(AutoPilotExperiment, experiment_id)
            if experiment and experiment.status == ExperimentStatus.RUNNING:
                experiment.status = ExperimentStatus.DEPLOYING
                experiment.deployed_variant_id = winner["variant_id"]
                experiment.rollout_stage = "canary"
                await session.commit()

        logger.info(
            "AutoPilot experiment %s: winner=%s (p=%.3f), "
            "starting progressive rollout (canary)",
            experiment_id, winner["variant_id"], p_value,
        )

    return winner


def select_winner(variant_stats: list, config: AutoPilotConfig) -> dict | None:
    """Select the winning variant based on the optimization target."""
    if not variant_stats:
        return None

    candidates = []
    for row in variant_stats:
        avg_quality = float(row.avg_quality or 0)
        avg_cost = float(row.avg_cost or 0)
        avg_duration = float(row.avg_duration or 0)

        # Filter out variants below quality threshold
        if avg_quality < config.quality_threshold:
            continue

        candidates.append({
            "variant_id": row.variant_id,
            "count": row.count,
            "avg_quality": avg_quality,
            "avg_cost": avg_cost,
            "avg_duration": avg_duration,
        })

    if not candidates:
        # No candidates above threshold - return best quality anyway
        best = max(variant_stats, key=lambda r: float(r.avg_quality or 0))
        return {
            "variant_id": best.variant_id,
            "count": best.count,
            "avg_quality": float(best.avg_quality or 0),
            "avg_cost": float(best.avg_cost or 0),
            "avg_duration": float(best.avg_duration or 0),
        }

    if config.optimize_for == "cost":
        return min(candidates, key=lambda c: c["avg_cost"])
    elif config.optimize_for == "latency":
        return min(candidates, key=lambda c: c["avg_duration"])
    elif config.optimize_for == "pareto":
        # Simple Pareto: normalize and find best combined score
        # Lower cost and duration is better, higher quality is better
        max_cost = max(c["avg_cost"] for c in candidates) or 1
        max_dur = max(c["avg_duration"] for c in candidates) or 1
        for c in candidates:
            cost_score = 1 - (c["avg_cost"] / max_cost)
            dur_score = 1 - (c["avg_duration"] / max_dur)
            c["pareto_score"] = (c["avg_quality"] + cost_score + dur_score) / 3
        return max(candidates, key=lambda c: c["pareto_score"])
    else:
        # Default: optimize for quality
        return max(candidates, key=lambda c: c["avg_quality"])


# --- Statistical significance ---


def _beta_inc(a: float, b: float, x: float) -> float:
    """Approximate regularized incomplete beta function."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    # Use a normal approximation for the t-distribution.
    # Recover t^2 from x = df / (df + t^2) where df = 2*a
    t_stat_sq = (1 - x) / x * (2 * a)
    t_stat = math.sqrt(max(t_stat_sq, 0))
    # Normal approximation of t-distribution CDF
    z = t_stat * (1 - 1 / (4 * max(a * 2, 1)))
    # Standard normal CDF approximation
    p = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return 2 * (1 - p)


def _check_significance(
    winner_scores: list[float],
    runner_up_scores: list[float],
    min_p_value: float = 0.05,
) -> tuple[bool, float]:
    """Welch's t-test to check if winner is statistically significant.

    Returns (is_significant, p_value).
    """
    n1, n2 = len(winner_scores), len(runner_up_scores)
    if n1 < 3 or n2 < 3:
        return False, 1.0

    mean1 = sum(winner_scores) / n1
    mean2 = sum(runner_up_scores) / n2
    var1 = sum((x - mean1) ** 2 for x in winner_scores) / (n1 - 1)
    var2 = sum((x - mean2) ** 2 for x in runner_up_scores) / (n2 - 1)

    se = math.sqrt(var1 / n1 + var2 / n2)
    if se == 0:
        return mean1 > mean2, 0.0 if mean1 != mean2 else 1.0

    t_stat = (mean1 - mean2) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var1 / n1 + var2 / n2) ** 2
    den = (var1 / n1) ** 2 / (n1 - 1) + (var2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else 1

    # Approximate p-value using t-distribution CDF
    x = df / (df + t_stat ** 2)
    if t_stat > 0:
        p_value = 0.5 * _beta_inc(0.5 * df, 0.5, x)
    else:
        p_value = 1.0 - 0.5 * _beta_inc(0.5 * df, 0.5, x)

    return p_value < min_p_value, p_value


# --- Progressive rollout ---


def should_use_winner(rollout_stage: str | None) -> bool:
    """Determine if current request should use the winning variant based on rollout stage."""
    if rollout_stage is None:
        return False
    traffic = ROLLOUT_STAGES.get(rollout_stage, 0)
    return random.random() < traffic


async def advance_rollout(experiment_id: uuid.UUID) -> dict | None:
    """Advance the experiment to the next rollout stage.

    Call this periodically or after sufficient monitoring.
    Returns the new stage info or None if already fully deployed.
    """
    from sandcastle.models.db import AutoPilotExperiment, ExperimentStatus, async_session

    async with async_session() as session:
        experiment = await session.get(AutoPilotExperiment, experiment_id)
        if not experiment or experiment.status != ExperimentStatus.DEPLOYING:
            return None

        current = experiment.rollout_stage or "canary"
        current_idx = ROLLOUT_ORDER.index(current) if current in ROLLOUT_ORDER else 0

        if current_idx >= len(ROLLOUT_ORDER) - 1:
            # Already at full - complete the experiment
            experiment.status = ExperimentStatus.COMPLETED
            experiment.completed_at = datetime.now(timezone.utc)
            experiment.rollout_stage = "full"
            await session.commit()
            logger.info(
                "AutoPilot experiment %s: rollout complete (full)", experiment_id
            )
            return {"stage": "full", "traffic": 1.0, "completed": True}

        next_stage = ROLLOUT_ORDER[current_idx + 1]
        experiment.rollout_stage = next_stage
        await session.commit()

        logger.info(
            "AutoPilot experiment %s: advanced rollout %s -> %s (%.0f%% traffic)",
            experiment_id, current, next_stage, ROLLOUT_STAGES[next_stage] * 100,
        )
        return {
            "stage": next_stage,
            "traffic": ROLLOUT_STAGES[next_stage],
            "completed": False,
        }
