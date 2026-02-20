"""AutoPilot - self-optimizing workflow step experiments."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sandcastle.engine.dag import AutoPilotConfig, StepDefinition, VariantConfig

logger = logging.getLogger(__name__)


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
    """Pick the least-sampled variant (round-robin selection)."""
    from sqlalchemy import func, select

    from sandcastle.models.db import AutoPilotSample, async_session

    if not variants:
        return None

    async with async_session() as session:
        # Count samples per variant
        stmt = (
            select(
                AutoPilotSample.variant_id,
                func.count(AutoPilotSample.id).label("count"),
            )
            .where(AutoPilotSample.experiment_id == experiment_id)
            .group_by(AutoPilotSample.variant_id)
        )
        result = await session.execute(stmt)
        counts = {row.variant_id: row.count for row in result.all()}

    # Pick the variant with the fewest samples
    min_count = float("inf")
    selected = variants[0]

    for variant in variants:
        count = counts.get(variant.id, 0)
        if count < min_count:
            min_count = count
            selected = variant

    return selected


def apply_variant(
    step: StepDefinition,
    variant: VariantConfig,
) -> StepDefinition:
    """Create a modified StepDefinition from a variant config."""
    return StepDefinition(
        id=step.id,
        prompt=variant.prompt if variant.prompt else step.prompt,
        depends_on=step.depends_on,
        model=variant.model if variant.model else step.model,
        max_turns=variant.max_turns if variant.max_turns else step.max_turns,
        timeout=step.timeout,
        parallel_over=step.parallel_over,
        output_schema=step.output_schema,
        retry=step.retry,
        fallback=step.fallback,
        type=step.type,
        autopilot=None,  # Don't recurse
    )


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
            proxy_url=None,
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
        async with async_session() as session:
            experiment = await session.get(AutoPilotExperiment, experiment_id)
            if experiment and experiment.status == ExperimentStatus.RUNNING:
                experiment.status = ExperimentStatus.COMPLETED
                experiment.deployed_variant_id = winner["variant_id"]
                experiment.completed_at = datetime.now(timezone.utc)
                await session.commit()

        logger.info(
            f"AutoPilot experiment {experiment_id} completed: "
            f"winner={winner['variant_id']} ({config.optimize_for})"
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
