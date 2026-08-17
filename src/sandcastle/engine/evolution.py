"""Workflow Evolution Engine - iterative workflow improvement via mutations.

Inspired by Karpathy's autoresearch loop and Nunchi's composite scoring formula.
Each iteration mutates a workflow, runs the eval suite, and keeps or discards
the change based on a composite score.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Model upgrade/downgrade ladder
MODEL_LADDER = ["haiku", "sonnet", "opus"]

# Fallback default used when no historical run data is available.
_DEFAULT_TOKENS_PER_RUN = 1000

# Midprice used to back-derive tokens from total_cost_usd when RunStep has no
# explicit token columns. Sonnet baseline blended (2:1 input:output) per 1M tokens.
# 2 * 3 + 15 = 21 / 3 = 7.0 USD per 1M blended tokens.
_BASELINE_BLENDED_USD_PER_M = 7.0

# Half-saturation points for the bounded efficiency terms used by evolution
# scoring. At these values an otherwise perfect efficiency contribution is 25
# points; lower cost/latency approaches 50 without ever becoming unbounded.
_COST_EFFICIENCY_SCALE_USD = 0.05
_LATENCY_EFFICIENCY_SCALE_SECONDS = 60.0

# In-process cache: workflow_name -> (tokens, timestamp_monotonic).
_TOKEN_CACHE: dict[str, tuple[int, float]] = {}
_TOKEN_CACHE_TTL_SECONDS = 60.0
_TOKEN_CACHE_LOCK = threading.Lock()


def _cache_get(workflow_name: str) -> int | None:
    with _TOKEN_CACHE_LOCK:
        entry = _TOKEN_CACHE.get(workflow_name)
        if entry is None:
            return None
        tokens, ts = entry
        if time.monotonic() - ts > _TOKEN_CACHE_TTL_SECONDS:
            _TOKEN_CACHE.pop(workflow_name, None)
            return None
        return tokens


def _cache_set(workflow_name: str, tokens: int) -> None:
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE[workflow_name] = (tokens, time.monotonic())


def _cache_clear() -> None:
    """Clear cache - mainly for tests."""
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE.clear()


async def _estimate_tokens_per_run(
    workflow_name: str,
    default: int = _DEFAULT_TOKENS_PER_RUN,
    tenant_id: str | None = None,
) -> int:
    """Estimate tokens per run for a workflow from recent run history.

    Queries the last 20 completed runs for this workflow_name. If RunStep rows
    exist with token columns those are summed; otherwise tokens are derived
    from total_cost_usd using a blended baseline price. Results are cached in
    process for `_TOKEN_CACHE_TTL_SECONDS` seconds. Falls back to `default`
    on any error or when no history exists.
    """
    if not workflow_name:
        return default

    cache_key = workflow_name if tenant_id is None else f"{tenant_id}\0{workflow_name}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        from sqlalchemy import select

        from sandcastle.models.db import Run, RunStatus, async_session
    except Exception as exc:  # pragma: no cover - import failure
        logger.debug("token estimate: import failed: %s", exc)
        return default

    try:
        async with async_session() as session:
            stmt = (
                select(Run.total_cost_usd)
                .where(Run.workflow_name == workflow_name)
                .where(Run.tenant_id == tenant_id)
                .where(Run.status == RunStatus.COMPLETED)
                .order_by(Run.created_at.desc())
                .limit(20)
            )
            result = await session.execute(stmt)
            costs = [row[0] for row in result.all() if row[0] is not None]
    except Exception as exc:
        logger.debug("token estimate: DB query failed for %s: %s", workflow_name, exc)
        return default

    if not costs:
        return default

    # Derive tokens from cost using blended baseline price.
    avg_cost = sum(costs) / len(costs)
    if avg_cost <= 0:
        return default

    tokens = int(round(avg_cost * 1_000_000 / _BASELINE_BLENDED_USD_PER_M))
    if tokens <= 0:
        tokens = default
    _cache_set(cache_key, tokens)
    return tokens


def _sync_estimate_tokens_per_run(
    workflow_name: str,
    default: int = _DEFAULT_TOKENS_PER_RUN,
) -> int:
    """Synchronous wrapper around _estimate_tokens_per_run.

    Uses the cache fast-path when possible. Otherwise runs the coroutine
    via asyncio.run, or falls back to default if a loop is already running
    in the current thread (cannot block).
    """
    if not workflow_name:
        return default

    cached = _cache_get(workflow_name)
    if cached is not None:
        return cached

    try:
        asyncio.get_running_loop()
        # We are already inside an event loop; cannot block. Use default.
        # Callers in async context should await _estimate_tokens_per_run directly.
        return default
    except RuntimeError:
        pass

    try:
        return asyncio.run(_estimate_tokens_per_run(workflow_name, default))
    except Exception as exc:
        logger.debug("sync token estimate failed for %s: %s", workflow_name, exc)
        return default


def _build_cost_estimates(tokens_per_run: int = _DEFAULT_TOKENS_PER_RUN) -> dict[str, float]:
    """Build per-model cost estimates for `tokens_per_run` tokens.

    Uses PROVIDER_REGISTRY (single source of truth) and a blended
    2:1 input:output token ratio.
    """
    from sandcastle.engine.providers import PROVIDER_REGISTRY

    estimates: dict[str, float] = {}
    for name in MODEL_LADDER:
        info = PROVIDER_REGISTRY.get(name)
        if info:
            blended_per_m = (info.input_price_per_m * 2 + info.output_price_per_m) / 3
            estimates[name] = round(blended_per_m * tokens_per_run / 1_000_000, 6)
        else:
            estimates[name] = 0.0
    return estimates


async def estimate_model_costs(
    workflow_name: str,
    default: int = _DEFAULT_TOKENS_PER_RUN,
    tenant_id: str | None = None,
) -> dict[str, float]:
    """Return per-model cost estimates calibrated to workflow_name's history.

    Falls back to the default tokens-per-run baseline when no history exists.
    """
    tokens = await _estimate_tokens_per_run(
        workflow_name,
        default=default,
        tenant_id=tenant_id,
    )
    return _build_cost_estimates(tokens)


# Backwards-compatible module-level default estimates (1K tokens baseline).
MODEL_COST_ESTIMATES: dict[str, float] = _build_cost_estimates()


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


def compute_evolution_score(
    quality: float,
    cost_usd: float,
    duration_seconds: float,
    eval_runs: int,
    optimize_for: str = "balanced",
    budget_limit: float | None = None,
) -> float:
    """Compute composite score for workflow evolution.

    Inspired by Nunchi's trading score formula.
    Higher is better.

    Args:
        quality: eval pass rate (0.0-1.0)
        cost_usd: total cost of eval runs
        duration_seconds: total duration of eval runs
        eval_runs: number of eval cases executed (for confidence)
        optimize_for: scoring objective (quality/cost/latency/balanced)
        budget_limit: optional total evolution budget cap

    Returns:
        Composite score (higher is better).
    """
    # Confidence factor: need enough runs for a reliable score
    confidence = min(eval_runs / 5, 1.0) ** 0.5

    # Cost penalty: proportional penalty above budget
    cost_penalty = 0.0
    if budget_limit and cost_usd > budget_limit:
        cost_penalty = (cost_usd - budget_limit) / budget_limit * 20

    # Latency penalty: penalize > 60s
    latency_penalty = max(0, (duration_seconds - 60) / 60) * 2
    cost_efficiency = 50 * confidence / (
        1 + max(cost_usd, 0.0) / _COST_EFFICIENCY_SCALE_USD
    )
    latency_efficiency = (
        50
        * confidence
        / (1 + max(duration_seconds, 0.0) / _LATENCY_EFFICIENCY_SCALE_SECONDS)
    )

    if optimize_for == "quality":
        base = quality * 100 * confidence - cost_penalty - latency_penalty
    elif optimize_for == "cost":
        base = cost_efficiency + quality * 50 * confidence - latency_penalty
    elif optimize_for == "latency":
        base = latency_efficiency + quality * 50 * confidence - cost_penalty
    else:  # balanced
        base = quality * 60 * confidence - cost_usd * 10 - latency_penalty * 0.5

    return base


# ---------------------------------------------------------------------------
# Mutation operators
# ---------------------------------------------------------------------------


async def mutate_prompt(
    workflow_yaml: str,
    step_id: str,
    eval_results: list[dict],
) -> tuple[str, str]:
    """Use LLM to suggest an improved prompt based on eval failures.

    Args:
        workflow_yaml: current workflow YAML
        step_id: which step to improve
        eval_results: list of eval case result dicts with 'passed', 'error', 'output' keys

    Returns:
        (new_yaml, description) tuple.
    """
    from sandcastle.engine.dag import parse_yaml_string

    try:
        workflow = parse_yaml_string(workflow_yaml)
    except Exception as exc:
        return workflow_yaml, f"mutate_prompt failed to parse YAML: {exc}"

    # Find the target step
    target_step = None
    for step in workflow.steps:
        if step.id == step_id:
            target_step = step
            break

    if target_step is None:
        return workflow_yaml, f"step '{step_id}' not found"

    # Analyze failures
    failures = [r for r in eval_results if not r.get("passed", True)]
    if not failures:
        return workflow_yaml, "no failures to improve"

    failure_summary = "\n".join(
        f"- case='{r.get('name', '?')}' error='{r.get('error', '')}' output='{str(r.get('output', ''))[:200]}'"
        for r in failures[:5]
    )

    prompt_text = (
        f"You are improving a workflow step prompt. "
        f"Current prompt:\n{target_step.prompt}\n\n"
        f"Eval failures:\n{failure_summary}\n\n"
        f"Write an improved prompt that fixes these failures. "
        f"Respond with ONLY the improved prompt text, nothing else."
    )

    new_prompt = target_step.prompt  # default: no change
    try:
        from sandcastle.engine.generator import _call_advisor_llm

        response_text = await _call_advisor_llm(
            system="You are a prompt engineering expert. Improve the given prompt based on eval failures.",
            user=prompt_text,
            purpose="evolution",
        )
        improved = response_text.strip()
        if improved and improved != target_step.prompt:
            new_prompt = improved
        else:
            return workflow_yaml, "LLM returned unchanged prompt"
    except Exception as exc:
        logger.warning("mutate_prompt LLM call failed: %s", exc)
        # Fallback: append clarity instruction
        new_prompt = target_step.prompt + "\n\nBe precise and complete in your response."

    # Apply mutation using dataclasses.replace
    new_step = dataclasses.replace(target_step, prompt=new_prompt)
    new_steps = [new_step if s.id == step_id else s for s in workflow.steps]
    new_workflow = dataclasses.replace(workflow, steps=new_steps)

    new_yaml = _workflow_to_yaml(new_workflow, workflow_yaml)
    desc = f"improved prompt for step '{step_id}' based on {len(failures)} failure(s)"
    return new_yaml, desc


async def mutate_model(
    workflow_yaml: str,
    step_id: str,
    current_cost: float,
) -> tuple[str, str]:
    """Try a different model - upgrade for quality or downgrade for cost.

    If current model is sonnet and cost is high -> try haiku (cheaper).
    If current model is haiku and quality is low -> try sonnet (better).

    Args:
        workflow_yaml: current workflow YAML
        step_id: which step to change
        current_cost: total eval cost to determine direction

    Returns:
        (new_yaml, description) tuple.
    """
    from sandcastle.engine.dag import parse_yaml_string

    try:
        workflow = parse_yaml_string(workflow_yaml)
    except Exception as exc:
        return workflow_yaml, f"mutate_model failed to parse YAML: {exc}"

    target_step = None
    for step in workflow.steps:
        if step.id == step_id:
            target_step = step
            break

    if target_step is None:
        return workflow_yaml, f"step '{step_id}' not found"

    current_model = target_step.model or workflow.default_model or "sonnet"
    # Normalize model alias
    current_model_norm = current_model.split("/")[-1].lower()
    if "haiku" in current_model_norm:
        current_idx = 0
    elif "sonnet" in current_model_norm:
        current_idx = 1
    elif "opus" in current_model_norm:
        current_idx = 2
    else:
        current_idx = 1  # default to sonnet position

    # Downgrade if cost is high, upgrade otherwise
    cost_threshold = 0.10  # $0.10 per eval suite is expensive
    if current_cost > cost_threshold and current_idx > 0:
        new_idx = current_idx - 1
        direction = "downgrade"
    elif current_idx < len(MODEL_LADDER) - 1:
        new_idx = current_idx + 1
        direction = "upgrade"
    else:
        return workflow_yaml, f"model '{current_model}' already at optimal position"

    new_model = MODEL_LADDER[new_idx]

    new_step = dataclasses.replace(target_step, model=new_model)
    new_steps = [new_step if s.id == step_id else s for s in workflow.steps]
    new_workflow = dataclasses.replace(workflow, steps=new_steps)

    new_yaml = _workflow_to_yaml(new_workflow, workflow_yaml)
    desc = f"{direction} step '{step_id}' from '{current_model}' to '{new_model}'"
    return new_yaml, desc


async def mutate_simplify(
    workflow_yaml: str,
    eval_results: list[dict],
) -> tuple[str, str]:
    """Try removing a step or reducing max_turns.

    Inspired by Nunchi's 'Great Simplification'.
    Finds the step with the lowest impact and tries to simplify.

    Args:
        workflow_yaml: current workflow YAML
        eval_results: list of eval case result dicts

    Returns:
        (new_yaml, description) tuple.
    """
    from sandcastle.engine.dag import parse_yaml_string

    try:
        workflow = parse_yaml_string(workflow_yaml)
    except Exception as exc:
        return workflow_yaml, f"mutate_simplify failed to parse YAML: {exc}"

    if not workflow.steps:
        return workflow_yaml, "no steps to simplify"

    # Safety: never simplify to fewer than 1 step
    if len(workflow.steps) <= 1:
        # Just reduce max_turns on the single step
        step = workflow.steps[0]
        if step.max_turns > 1:
            new_step = dataclasses.replace(step, max_turns=max(1, step.max_turns - 2))
            new_workflow = dataclasses.replace(workflow, steps=[new_step])
            new_yaml = _workflow_to_yaml(new_workflow, workflow_yaml)
            return new_yaml, f"reduced max_turns for step '{step.id}' from {step.max_turns} to {new_step.max_turns}"
        return workflow_yaml, "workflow is already minimal"

    # Find candidate step: prefer steps with no dependents (leaf steps are lower impact)
    steps_with_dependents = set()
    for step in workflow.steps:
        for dep in (step.depends_on or []):
            steps_with_dependents.add(dep)

    leaf_steps = [s for s in workflow.steps if s.id not in steps_with_dependents]

    if leaf_steps:
        # Among leaves, prefer the one with most tools (removal has less quality impact)
        # or highest max_turns (reduce it)
        candidate = max(leaf_steps, key=lambda s: len(s.tools or []) + (s.max_turns or 10))
    else:
        # All steps have dependents - just pick step with highest max_turns
        candidate = max(workflow.steps, key=lambda s: s.max_turns or 10)

    # Decision: reduce max_turns if > 5, else try removing tools, else skip
    if (candidate.max_turns or 10) > 5:
        new_step = dataclasses.replace(candidate, max_turns=max(3, (candidate.max_turns or 10) - 3))
        new_steps = [new_step if s.id == candidate.id else s for s in workflow.steps]
        new_workflow = dataclasses.replace(workflow, steps=new_steps)
        new_yaml = _workflow_to_yaml(new_workflow, workflow_yaml)
        desc = f"reduced max_turns for step '{candidate.id}' from {candidate.max_turns} to {new_step.max_turns}"
        return new_yaml, desc

    if candidate.tools and len(candidate.tools) > 1:
        # Remove the last tool
        new_tools = candidate.tools[:-1]
        removed_tool = candidate.tools[-1]
        new_step = dataclasses.replace(candidate, tools=new_tools)
        new_steps = [new_step if s.id == candidate.id else s for s in workflow.steps]
        new_workflow = dataclasses.replace(workflow, steps=new_steps)
        new_yaml = _workflow_to_yaml(new_workflow, workflow_yaml)
        desc = f"removed tool '{removed_tool}' from step '{candidate.id}'"
        return new_yaml, desc

    # Last resort: if this leaf step is truly not needed, remove it
    # Only if it has no dependents AND other steps don't depend on it
    if candidate.id not in steps_with_dependents:
        new_steps = [s for s in workflow.steps if s.id != candidate.id]
        new_workflow = dataclasses.replace(workflow, steps=new_steps)
        new_yaml = _workflow_to_yaml(new_workflow, workflow_yaml)
        desc = f"removed leaf step '{candidate.id}'"
        return new_yaml, desc

    return workflow_yaml, "no simplification opportunity found"


# ---------------------------------------------------------------------------
# YAML serialization helper
# ---------------------------------------------------------------------------


def _workflow_to_yaml(workflow: Any, original_yaml: str) -> str:
    """Serialize mutated workflow back to YAML.

    Attempts a minimal patch: replaces the YAML representation of each step.
    Falls back to reconstructing from the workflow dataclass fields.
    """
    try:
        # Parse original YAML to get a mutable dict
        data = yaml.safe_load(original_yaml)
        if not isinstance(data, dict):
            raise ValueError("original YAML is not a mapping")

        # Rebuild steps list from workflow dataclass
        new_steps = []
        for step in workflow.steps:
            step_dict: dict[str, Any] = {"id": step.id}
            if step.type and step.type != "standard":
                step_dict["type"] = step.type
            if step.prompt:
                step_dict["prompt"] = step.prompt
            if step.model and step.model != "sonnet":
                step_dict["model"] = step.model
            if step.max_turns and step.max_turns != 10:
                step_dict["max_turns"] = step.max_turns
            if step.depends_on:
                step_dict["depends_on"] = step.depends_on
            if step.tools:
                step_dict["tools"] = step.tools
            if step.timeout and step.timeout != 300:
                step_dict["timeout"] = step.timeout
            new_steps.append(step_dict)

        data["steps"] = new_steps
        return yaml.dump(data, default_flow_style=False, allow_unicode=True)
    except Exception as exc:
        logger.warning("_workflow_to_yaml fallback triggered: %s", exc)
        return original_yaml


def _current_adapter_id(workflow_yaml: str) -> str | None:
    """Adapter id the workflow is currently routed to, if any (lineage parent).

    Looks at ``default_model`` and step models for an ``adapter/<id>`` reference.
    Returns None when the workflow still runs on a base model.
    """
    try:
        data = yaml.safe_load(workflow_yaml) or {}
    except Exception:  # noqa: BLE001 - unparseable yaml -> no lineage
        return None
    if not isinstance(data, dict):
        return None
    candidates = [data.get("default_model")]
    steps = data.get("steps")
    if isinstance(steps, list):
        candidates += [st.get("model") for st in steps if isinstance(st, dict)]
    for model in candidates:
        if isinstance(model, str) and model.startswith("adapter/"):
            return model.removeprefix("adapter/")
    return None


async def mutate_finetune(
    workflow_yaml: str,
    eval_results: list[dict],
    base_model: str = "sonnet",
    min_samples: int | None = None,
) -> tuple[str, str]:
    """Overnight Self-Tune mutation: train a local LoRA adapter and route to it.

    Builds training pairs from the workflow's own eval results, trains a task-specific
    adapter via the configured Trainer (a deterministic mock unless trainer_backend=
    "gpu"), registers it, and rewrites every step's model to ``adapter/<id>``. Returns
    the workflow unchanged with a reason when it cannot/should not run (too few
    samples, training error) - run_evolution then records it as a discard, exactly
    like the other mutations. Same ``(yaml, description)`` shape as mutate_prompt/etc.
    """
    from sandcastle.config import settings
    from sandcastle.engine.adapter_registry import AdapterRegistry
    from sandcastle.engine.training.trainer import get_trainer

    if min_samples is None:
        min_samples = settings.evolution_finetune_min_samples

    pairs = []
    for result in eval_results or []:
        if not isinstance(result, dict):
            continue
        input_value = result.get("input")
        if input_value is None:
            input_value = result.get("prompt")
        output_value = result.get("expected")
        has_expected_output = output_value is not None
        if output_value is None:
            output_value = result.get("output")
        if not has_expected_output and result.get("passed") is False:
            continue
        if input_value is not None and output_value is not None:
            pairs.append({"input": input_value, "output": output_value})
    if len(pairs) < min_samples:
        return workflow_yaml, f"finetune skipped: {len(pairs)} usable samples < min {min_samples}"

    try:
        result = await get_trainer().train(base_model, pairs, settings)
    except Exception as exc:  # noqa: BLE001 - any trainer failure -> graceful discard
        return workflow_yaml, f"finetune training failed: {exc}"

    # Lineage: if the workflow is already routed to an adapter, that adapter is the
    # parent of the one we just trained (parent -> child chain across nights).
    dataset_hash = hashlib.sha256(
        json.dumps(pairs, sort_keys=True, default=str).encode()
    ).hexdigest()

    AdapterRegistry().register(
        adapter_id=result.adapter_id,
        base_model=result.base_model,
        metrics=result.metrics,
        samples=result.samples,
        lora_config=result.lora_config,
        created_at=time.time(),
        dataset_hash=dataset_hash,
        parent_adapter_id=_current_adapter_id(workflow_yaml),
    )

    adapter_model = f"adapter/{result.adapter_id}"
    try:
        data = yaml.safe_load(workflow_yaml) or {}
        if isinstance(data.get("default_model"), str):
            data["default_model"] = adapter_model
        for st in data.get("steps", []):
            if isinstance(st, dict) and st.get("model"):
                st["model"] = adapter_model
        new_yaml = yaml.dump(data, default_flow_style=False, allow_unicode=True)
    except Exception as exc:  # noqa: BLE001
        return workflow_yaml, f"finetune yaml rewrite failed: {exc}"

    return new_yaml, (
        f"trained LoRA adapter {result.adapter_id} on {result.samples} samples "
        f"(eval_score={result.metrics.get('eval_score')}); routed steps to it"
    )


def _pick_mutation_type(
    iteration: int,
    eval_results: list[dict],
    current_cost: float,
    optimize_for: str,
) -> str:
    """Pick mutation strategy based on iteration number and context."""
    from sandcastle.config import settings

    # Overnight Self-Tune: periodically attempt a LoRA fine-tune (opt-in). Gated so it
    # runs at most once per 5 iterations and never on the first iteration.
    if settings.evolution_auto_finetune and iteration > 0 and iteration % 5 == 4:
        return "finetune"

    # Round-robin with context awareness
    if optimize_for == "cost":
        # Prefer model downgrade and simplify for cost optimization
        cycle = ["model", "simplify", "prompt", "model", "simplify"]
    elif optimize_for == "quality":
        # Prefer prompt improvement for quality
        cycle = ["prompt", "model", "prompt", "simplify", "prompt"]
    elif optimize_for == "latency":
        # Prefer simplify and model downgrade
        cycle = ["simplify", "model", "simplify", "prompt", "simplify"]
    else:  # balanced
        cycle = ["prompt", "model", "simplify", "prompt", "model"]

    return cycle[iteration % len(cycle)]


def _build_mutation_eval_results(
    suite_cases: list[Any],
    case_results: list[Any],
) -> list[dict[str, Any]]:
    """Combine eval outcomes with their original inputs for mutation operators."""
    mutation_results: list[dict[str, Any]] = []
    for index, result in enumerate(case_results):
        case_input = suite_cases[index].input if index < len(suite_cases) else None
        mutation_results.append(
            {
                "name": result.name,
                "input": case_input,
                "passed": result.passed,
                "error": result.error,
                "output": result.output,
                "cost_usd": result.cost_usd,
                "duration_seconds": result.duration_seconds,
            }
        )
    return mutation_results


# ---------------------------------------------------------------------------
# Evolution orchestrator
# ---------------------------------------------------------------------------


def _prepare_eval_suite(
    eval_suite_yaml: str,
    workflow_name: str,
) -> tuple[str, Any]:
    """Normalize and parse an eval suite for a known workflow.

    Evolution already knows the workflow name, so callers may omit it. Older
    dashboard versions also wrapped the suite fields in a top-level ``suite``
    mapping; accept that shape while storing the canonical flat format.
    """
    from sandcastle.engine.eval import parse_eval_suite_string

    data = yaml.safe_load(eval_suite_yaml)
    if not isinstance(data, dict):
        raise ValueError("Eval suite must be a YAML mapping")

    nested_suite = data.get("suite")
    if isinstance(nested_suite, dict):
        top_level = {key: value for key, value in data.items() if key != "suite"}
        data = {**nested_suite, **top_level}

    suite_workflow = data.get("workflow")
    if suite_workflow and suite_workflow != workflow_name:
        raise ValueError(
            f"Eval suite targets workflow '{suite_workflow}', expected '{workflow_name}'"
        )
    data["workflow"] = workflow_name

    normalized_yaml = yaml.safe_dump(data, sort_keys=False)
    return normalized_yaml, parse_eval_suite_string(normalized_yaml)


async def run_evolution(
    workflow_name: str,
    eval_suite_yaml: str,
    max_iterations: int = 20,
    optimize_for: str = "balanced",
    budget_limit: float | None = None,
    tenant_id: str | None = None,
    evolution_id: uuid.UUID | None = None,
    record_exists: bool = False,
) -> dict:
    """Run the evolution loop.

    1. Load baseline workflow
    2. Run eval suite -> baseline score
    3. For each iteration:
       a. Pick mutation strategy (prompt/model/simplify)
       b. Apply mutation to workflow
       c. Run eval suite on mutated workflow
       d. Compute score
       e. If score > best_score: KEEP (update best)
       f. Else: DISCARD (revert to best)
       g. Log iteration to DB
    4. Return best variant

    Args:
        workflow_name: name of the workflow to evolve
        eval_suite_yaml: YAML string defining the eval suite
        max_iterations: maximum number of mutation iterations
        optimize_for: scoring objective (quality/cost/latency/balanced)
        budget_limit: optional total experiment cost cap (USD)
        tenant_id: optional tenant for multi-tenant setups
        evolution_id: optional preallocated ID for API background execution
        record_exists: whether the API already persisted the running record

    Returns:
        Dict with evolution results including best_yaml and stats.
    """
    from sandcastle.engine.dag import parse_yaml_string, validate
    from sandcastle.engine.eval import run_eval_suite
    from sandcastle.engine.generator import _get_advisor_config
    from sandcastle.models.db import EvolutionIteration, WorkflowEvolution, async_session

    evolution_id = evolution_id or uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Snapshot advisor config at start of evolution for logging - the actual
    # provider resolution still happens inside _call_advisor_llm per call.
    # This primarily serves as a diagnostic record in the log.
    try:
        locked_config = _get_advisor_config()
        logger.info(
            "Evolution %s: advisor config at start - provider=%s model=%s region=%s",
            evolution_id,
            locked_config.get("api_key_env", ""),
            locked_config.get("model", "unknown"),
            locked_config.get("region", "us"),
        )
    except Exception as exc:
        logger.warning("Evolution %s: could not read advisor config: %s", evolution_id, exc)
        locked_config = None

    # Calibrate per-model cost estimates against this workflow's run history.
    try:
        estimated_tokens = await _estimate_tokens_per_run(
            workflow_name,
            tenant_id=tenant_id,
        )
        cost_estimates = _build_cost_estimates(estimated_tokens)
        logger.info(
            "Evolution %s: tokens_per_run=%d cost_estimates=%s",
            evolution_id, estimated_tokens, cost_estimates,
        )
    except Exception as exc:
        logger.debug("Evolution %s: token estimate failed: %s", evolution_id, exc)

    # --- Load baseline workflow ---
    try:
        baseline_yaml = _get_workflow_yaml(workflow_name)
        baseline_workflow = parse_yaml_string(baseline_yaml)
        validation_errors = validate(baseline_workflow)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
    except Exception as exc:
        return {
            "error": f"Failed to load workflow '{workflow_name}': {exc}",
            "status": "failed",
        }

    # --- Parse eval suite ---
    try:
        eval_suite_yaml, suite = _prepare_eval_suite(eval_suite_yaml, workflow_name)
    except Exception as exc:
        return {
            "error": f"Failed to parse eval suite: {exc}",
            "status": "failed",
        }
    if not suite.cases:
        # Without eval cases every variant scores 0.0 and the whole evolution
        # is a no-op that still burns iterations - refuse loudly instead.
        return {
            "error": (
                "Eval suite has no cases - evolution needs at least one eval "
                "case (input + assertions) to score variants against. Add "
                "cases to the eval suite before starting."
            ),
            "status": "failed",
        }

    # --- Create DB record ---
    if not record_exists:
        evolution_record = WorkflowEvolution(
            id=evolution_id,
            workflow_name=workflow_name,
            status="running",
            optimize_for=optimize_for,
            max_iterations=max_iterations,
            budget_limit_usd=budget_limit,
            eval_suite_yaml=eval_suite_yaml,
            tenant_id=tenant_id,
            created_at=now,
        )

        async with async_session() as session:
            session.add(evolution_record)
            await session.commit()

    # --- Run baseline eval ---
    logger.info("Evolution %s: running baseline eval for '%s'", evolution_id, workflow_name)
    try:
        baseline_result = await run_eval_suite(suite, tenant_id=tenant_id)
    except Exception as exc:
        logger.error("Evolution %s: baseline eval failed: %s", evolution_id, exc)
        await _update_evolution_status(
            evolution_id,
            "failed",
            error=f"Baseline eval failed: {exc}",
        )
        return {"error": f"Baseline eval failed: {exc}", "status": "failed"}

    if await _is_evolution_cancelled(evolution_id):
        return {
            "evolution_id": str(evolution_id),
            "workflow_name": workflow_name,
            "status": "cancelled",
        }

    baseline_quality = baseline_result.pass_rate
    baseline_cost = baseline_result.total_cost_usd
    baseline_duration = baseline_result.total_duration_seconds
    baseline_eval_runs = baseline_result.total

    baseline_score = compute_evolution_score(
        quality=baseline_quality,
        cost_usd=baseline_cost,
        duration_seconds=baseline_duration,
        eval_runs=baseline_eval_runs,
        optimize_for=optimize_for,
        budget_limit=budget_limit,
    )

    logger.info(
        "Evolution %s: baseline score=%.2f quality=%.2f cost=$%.4f",
        evolution_id, baseline_score, baseline_quality, baseline_cost,
    )

    # Update DB with baseline
    async with async_session() as session:
        rec = await session.get(WorkflowEvolution, evolution_id)
        if rec:
            rec.baseline_score = baseline_score
            rec.baseline_quality = baseline_quality
            rec.baseline_cost = baseline_cost
            rec.best_score = baseline_score
            rec.best_quality = baseline_quality
            rec.best_cost = baseline_cost
            rec.best_variant_yaml = baseline_yaml
            await session.commit()

    # --- Evolution loop ---
    best_score = baseline_score
    best_yaml = baseline_yaml
    best_quality = baseline_quality
    best_cost = baseline_cost
    # Cumulative spend across the baseline run plus every iteration's eval
    # suite. budget_limit is enforced against this running total (stop check
    # below), not against a single run's cost.
    total_spend = baseline_cost or 0.0
    total_keeps = 0
    total_discards = 0
    iterations_run = 0

    # Extract eval results as dicts for mutation operators
    eval_case_dicts = _build_mutation_eval_results(
        suite.cases,
        baseline_result.cases,
    )

    for iteration in range(max_iterations):
        if budget_limit and total_spend >= budget_limit:
            logger.warning(
                "Evolution %s: budget already exhausted ($%.4f >= $%.4f limit)",
                evolution_id,
                total_spend,
                budget_limit,
            )
            break

        if await _is_evolution_cancelled(evolution_id):
            return {
                "evolution_id": str(evolution_id),
                "workflow_name": workflow_name,
                "status": "cancelled",
            }

        logger.info("Evolution %s: iteration %d/%d", evolution_id, iteration + 1, max_iterations)

        # Pick mutation type
        mutation_type = _pick_mutation_type(
            iteration=iteration,
            eval_results=eval_case_dicts,
            current_cost=best_cost,
            optimize_for=optimize_for,
        )

        # Get first step as default candidate
        try:
            current_workflow = parse_yaml_string(best_yaml)
            target_step_id = current_workflow.steps[0].id if current_workflow.steps else "step1"
        except Exception:
            target_step_id = "step1"

        # Apply mutation
        mutation_diff = None
        try:
            if mutation_type == "prompt":
                new_yaml, description = await mutate_prompt(
                    workflow_yaml=best_yaml,
                    step_id=target_step_id,
                    eval_results=eval_case_dicts,
                )
                mutation_diff = [{"step_id": target_step_id, "field": "prompt", "type": "prompt"}]
            elif mutation_type == "model":
                new_yaml, description = await mutate_model(
                    workflow_yaml=best_yaml,
                    step_id=target_step_id,
                    current_cost=best_cost,
                )
                mutation_diff = [{"step_id": target_step_id, "field": "model", "type": "model"}]
            elif mutation_type == "finetune":
                _base = "sonnet"
                try:
                    _data = parse_yaml_string(best_yaml)
                    _base = _data.default_model or "sonnet"
                except Exception:
                    pass
                new_yaml, description = await mutate_finetune(
                    workflow_yaml=best_yaml,
                    eval_results=eval_case_dicts,
                    base_model=_base,
                )
                mutation_diff = [{"type": "finetune"}]
            else:  # simplify
                new_yaml, description = await mutate_simplify(
                    workflow_yaml=best_yaml,
                    eval_results=eval_case_dicts,
                )
                mutation_diff = [{"type": "simplify"}]
        except Exception as exc:
            logger.warning("Evolution %s: mutation %s failed: %s", evolution_id, mutation_type, exc)
            description = f"mutation error: {exc}"
            new_yaml = best_yaml
            mutation_diff = None

        # If mutation returned unchanged YAML, record as discard
        if new_yaml == best_yaml:
            iter_record = EvolutionIteration(
                evolution_id=evolution_id,
                iteration_number=iteration + 1,
                mutation_type=mutation_type,
                mutation_description=description,
                mutation_diff=mutation_diff,
                status="discard",
                variant_yaml=new_yaml,
            )
            async with async_session() as session:
                session.add(iter_record)
                ev = await session.get(WorkflowEvolution, evolution_id)
                if ev:
                    ev.current_iteration = iteration + 1
                    ev.total_discards = total_discards + 1
                await session.commit()
            total_discards += 1
            iterations_run = iteration + 1
            continue

        # Run eval suite on mutated workflow
        temp_workflow_name = f"__evolution_{evolution_id}_{iteration}"
        eval_result = await _run_eval_on_yaml(
            new_yaml,
            suite,
            temp_workflow_name,
            tenant_id=tenant_id,
        )

        if await _is_evolution_cancelled(evolution_id):
            return {
                "evolution_id": str(evolution_id),
                "workflow_name": workflow_name,
                "status": "cancelled",
            }

        if eval_result is None:
            # Eval crashed
            iter_record = EvolutionIteration(
                evolution_id=evolution_id,
                iteration_number=iteration + 1,
                mutation_type=mutation_type,
                mutation_description=description,
                mutation_diff=mutation_diff,
                status="crash",
                variant_yaml=new_yaml,
            )
            async with async_session() as session:
                session.add(iter_record)
                ev = await session.get(WorkflowEvolution, evolution_id)
                if ev:
                    ev.current_iteration = iteration + 1
                    ev.total_discards = total_discards + 1
                await session.commit()
            total_discards += 1
            iterations_run = iteration + 1
            continue

        # Compute score
        iter_quality = eval_result.pass_rate
        iter_cost = eval_result.total_cost_usd
        total_spend += iter_cost or 0.0
        iter_duration = eval_result.total_duration_seconds
        iter_eval_runs = eval_result.total

        iter_score = compute_evolution_score(
            quality=iter_quality,
            cost_usd=iter_cost,
            duration_seconds=iter_duration,
            eval_runs=iter_eval_runs,
            optimize_for=optimize_for,
            budget_limit=budget_limit,
        )

        # Keep or discard
        if iter_score > best_score:
            decision = "keep"
            best_score = iter_score
            best_yaml = new_yaml
            best_quality = iter_quality
            best_cost = iter_cost
            total_keeps += 1
            eval_case_dicts = _build_mutation_eval_results(
                suite.cases,
                eval_result.cases,
            )

            # Update best in DB
            async with async_session() as session:
                ev = await session.get(WorkflowEvolution, evolution_id)
                if ev:
                    ev.best_score = best_score
                    ev.best_quality = best_quality
                    ev.best_cost = best_cost
                    ev.best_variant_yaml = best_yaml
                await session.commit()

            logger.info(
                "Evolution %s iter %d: KEEP score=%.2f->%.2f (%s)",
                evolution_id, iteration + 1, baseline_score, best_score, description,
            )
        else:
            decision = "discard"
            total_discards += 1
            logger.debug(
                "Evolution %s iter %d: DISCARD score=%.2f<%.2f (%s)",
                evolution_id, iteration + 1, iter_score, best_score, description,
            )

        # Log iteration to DB
        iter_record = EvolutionIteration(
            evolution_id=evolution_id,
            iteration_number=iteration + 1,
            mutation_type=mutation_type,
            mutation_description=description,
            mutation_diff=mutation_diff,
            score=iter_score,
            quality=iter_quality,
            cost_usd=iter_cost,
            duration_seconds=iter_duration,
            eval_pass_rate=iter_quality,
            status=decision,
            variant_yaml=new_yaml,
        )

        async with async_session() as session:
            session.add(iter_record)
            ev = await session.get(WorkflowEvolution, evolution_id)
            if ev:
                ev.current_iteration = iteration + 1
                ev.total_keeps = total_keeps
                ev.total_discards = total_discards
            await session.commit()
        iterations_run = iteration + 1

        # Check budget stop condition against cumulative spend
        if budget_limit and total_spend >= budget_limit:
            logger.warning(
                "Evolution %s: budget exhausted ($%.4f >= $%.4f limit) after %d iterations",
                evolution_id,
                total_spend,
                budget_limit,
                iteration + 1,
            )
            break

    # --- Complete evolution ---
    completed_at = datetime.now(timezone.utc)
    async with async_session() as session:
        ev = await session.get(WorkflowEvolution, evolution_id)
        if ev and ev.status == "cancelled":
            return {
                "evolution_id": str(evolution_id),
                "workflow_name": workflow_name,
                "status": "cancelled",
            }
        if ev:
            ev.status = "completed"
            ev.completed_at = completed_at
            ev.total_keeps = total_keeps
            ev.total_discards = total_discards
            ev.best_score = best_score
            ev.best_quality = best_quality
            ev.best_cost = best_cost
            ev.best_variant_yaml = best_yaml
        await session.commit()

    improvement = best_score - baseline_score
    logger.info(
        "Evolution %s: completed. improvement=%.2f keeps=%d discards=%d",
        evolution_id, improvement, total_keeps, total_discards,
    )

    return {
        "evolution_id": str(evolution_id),
        "workflow_name": workflow_name,
        "status": "completed",
        "baseline_score": baseline_score,
        "baseline_quality": baseline_quality,
        "baseline_cost": baseline_cost,
        "best_score": best_score,
        "best_quality": best_quality,
        "best_cost": best_cost,
        "best_variant_yaml": best_yaml,
        "improvement": improvement,
        "iterations_run": iterations_run,
        "total_keeps": total_keeps,
        "total_discards": total_discards,
        "optimize_for": optimize_for,
    }


async def _run_eval_on_yaml(
    workflow_yaml: str,
    suite: Any,
    temp_name: str,
    tenant_id: str | None = None,
) -> Any | None:
    """Run eval suite against a mutated workflow YAML.

    Temporarily patches the workflow loading to use the provided YAML.
    Returns SuiteResult or None on crash.

    Args:
        workflow_yaml: YAML string of the mutated workflow.
        suite: parsed eval suite definition.
        temp_name: temporary workflow name for logging.
        tenant_id: tenant propagated into every workflow execution context.
    """
    from sandcastle.engine.eval import CaseResult, SuiteResult


    try:
        from sandcastle.engine.dag import parse_yaml_string

        mutated_workflow = parse_yaml_string(workflow_yaml)
    except Exception as exc:
        logger.warning("_run_eval_on_yaml: YAML parse failed: %s", exc)
        return None

    # Run eval cases using the mutated workflow directly
    try:
        case_results = []
        for case in suite.cases:
            try:
                result = await _run_case_on_workflow(
                    case,
                    mutated_workflow,
                    tenant_id=tenant_id,
                )
                case_results.append(result)
            except Exception as exc:
                case_results.append(
                    CaseResult(
                        name=case.name,
                        passed=False,
                        error=str(exc),
                    )
                )

        total = len(case_results)
        passed = sum(1 for c in case_results if c.passed)
        failed = total - passed
        total_cost = sum(c.cost_usd for c in case_results)
        total_duration = sum(c.duration_seconds for c in case_results)

        from sandcastle.engine.eval import SuiteResult
        return SuiteResult(
            suite_name=suite.description or mutated_workflow.name,
            workflow=mutated_workflow.name,
            total=total,
            passed=passed,
            failed=failed,
            pass_rate=passed / total if total > 0 else 0.0,
            total_cost_usd=total_cost,
            total_duration_seconds=total_duration,
            cases=case_results,
        )
    except Exception as exc:
        logger.error("_run_eval_on_yaml: eval execution failed: %s", exc)
        return None


async def _run_case_on_workflow(
    case: Any,
    workflow: Any,
    tenant_id: str | None = None,
) -> Any:
    """Execute a single eval case against a specific workflow definition."""
    import uuid as _uuid

    from sandcastle.engine.dag import build_plan
    from sandcastle.engine.eval import CaseResult, check_assertion
    from sandcastle.engine.executor import execute_workflow
    from sandcastle.engine.storage import create_storage

    start_time = time.monotonic()
    run_id = str(_uuid.uuid4())

    try:
        plan = build_plan(workflow)
        storage = create_storage()

        result = await execute_workflow(
            workflow=workflow,
            plan=plan,
            input_data=case.input,
            run_id=run_id,
            storage=storage,
            tenant_id=tenant_id,
        )

        duration = time.monotonic() - start_time
        cost = result.total_cost_usd

        step_outputs: dict = {}
        if hasattr(result, "outputs") and isinstance(result.outputs, dict):
            step_outputs = result.outputs

        final_output = result.outputs
        meta = {"cost_usd": cost, "duration_seconds": duration}

        assertion_results = []
        for assertion in case.assertions:
            ar = await check_assertion(assertion, final_output, meta, step_outputs)
            assertion_results.append(ar)

        all_passed = all(ar.passed for ar in assertion_results)
        error = result.error if hasattr(result, "error") else None

        from sandcastle.engine.eval import _summarize_output
        return CaseResult(
            name=case.name,
            passed=all_passed and result.status == "completed",
            assertions=assertion_results,
            run_id=run_id,
            cost_usd=cost,
            duration_seconds=duration,
            output=_summarize_output(final_output),
            error=error,
        )
    except Exception as exc:
        duration = time.monotonic() - start_time
        return CaseResult(
            name=case.name,
            passed=False,
            run_id=run_id,
            duration_seconds=duration,
            error=str(exc),
        )


def _get_workflow_yaml(workflow_name: str) -> str:
    """Load raw YAML content for a workflow by name."""
    import re
    from pathlib import Path

    from sandcastle.config import settings

    if not workflow_name or not workflow_name.strip():
        raise ValueError("Workflow name must not be empty")
    if ".." in workflow_name or "/" in workflow_name or "\\" in workflow_name:
        raise FileNotFoundError(f"Invalid workflow name: '{workflow_name}'")

    workflows_dir = Path(settings.workflows_dir).resolve()
    slug = re.sub(r"[^a-z0-9]+", "-", workflow_name.lower()).strip("-")
    for candidate in [
        workflows_dir / f"{workflow_name}.yaml",
        workflows_dir / f"{slug}.yaml",
        workflows_dir / workflow_name,
        workflows_dir / slug,
    ]:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(workflows_dir):
            continue
        if resolved.exists() and resolved.is_file():
            return resolved.read_text()
    raise FileNotFoundError(f"Workflow '{workflow_name}' not found in {workflows_dir}")


async def _is_evolution_cancelled(evolution_id: uuid.UUID) -> bool:
    """Return whether cancellation was requested for a running evolution."""
    from sandcastle.models.db import WorkflowEvolution, async_session

    async with async_session() as session:
        ev = await session.get(WorkflowEvolution, evolution_id)
        return bool(ev and ev.status == "cancelled")


async def _update_evolution_status(
    evolution_id: uuid.UUID,
    status: str,
    error: str | None = None,
) -> None:
    """Update evolution status in the database."""
    from sandcastle.models.db import WorkflowEvolution, async_session

    async with async_session() as session:
        ev = await session.get(WorkflowEvolution, evolution_id)
        if ev:
            ev.status = status
            if error is not None:
                ev.error = error
            if status in ("completed", "failed", "cancelled"):
                ev.completed_at = datetime.now(timezone.utc)
            await session.commit()
