"""Workflow executor - parallel execution with retries, cost tracking, budget, cancel."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sandcastle.engine.dag import (
    ExecutionPlan,
    StepDefinition,
    WorkflowDefinition,
)
from sandcastle.engine.sandbox import SandstormClient, SandstormError
from sandcastle.engine.storage import StorageBackend

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    """Result from executing a single step."""

    step_id: str
    parallel_index: int | None = None
    output: Any = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    status: str = "completed"  # "completed" | "failed" | "skipped"
    error: str | None = None
    attempt: int = 1


@dataclass
class RunContext:
    """Mutable context passed through the execution of a workflow run."""

    run_id: str
    input: dict
    step_outputs: dict[str, Any] = field(default_factory=dict)
    costs: list[float] = field(default_factory=list)
    status: str = "running"
    error: str | None = None
    max_cost_usd: float | None = None
    workflow_name: str = ""

    def with_item(self, item: Any, index: int) -> RunContext:
        """Create a child context for a parallel_over item."""
        return RunContext(
            run_id=self.run_id,
            input={**self.input, "_item": item, "_index": index},
            step_outputs=dict(self.step_outputs),
            costs=self.costs,
            status=self.status,
            max_cost_usd=self.max_cost_usd,
            workflow_name=self.workflow_name,
        )

    @property
    def total_cost(self) -> float:
        return sum(self.costs)

    def snapshot(self) -> dict:
        """Create a serializable snapshot of the context for checkpointing."""
        return {
            "run_id": self.run_id,
            "input": self.input,
            "step_outputs": self.step_outputs,
            "costs": self.costs,
            "total_cost": self.total_cost,
        }


@dataclass
class WorkflowResult:
    """Final result of a workflow execution."""

    run_id: str
    outputs: dict[str, Any]
    total_cost_usd: float
    status: str
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


def resolve_variable(var_path: str, context: RunContext) -> Any:
    """Resolve a dotted variable path against the run context.

    Supports:
    - input.X -> from context.input
    - steps.STEP_ID.output -> from context.step_outputs
    - steps.STEP_ID.output.FIELD -> specific field
    - run_id -> current run UUID
    - date -> current ISO date
    """
    parts = var_path.split(".")

    if parts[0] == "input":
        obj = context.input
        for part in parts[1:]:
            if isinstance(obj, dict):
                obj = obj.get(part)
            elif isinstance(obj, list):
                obj = obj[int(part)]
            else:
                return None
        return obj

    if parts[0] == "steps" and len(parts) >= 3:
        step_id = parts[1]
        step_data = context.step_outputs.get(step_id)
        if step_data is None:
            return None
        if parts[2] == "output":
            if len(parts) == 3:
                return step_data
            obj = step_data
            for part in parts[3:]:
                if isinstance(obj, dict):
                    obj = obj.get(part)
                elif isinstance(obj, list):
                    obj = obj[int(part)]
                else:
                    return None
            return obj

    if var_path == "run_id":
        return context.run_id

    if var_path == "date":
        return datetime.now(timezone.utc).date().isoformat()

    return None


def resolve_templates(template: str, context: RunContext) -> str:
    """Replace {var.path} template variables in a string."""

    def _replace(match: re.Match) -> str:
        var_path = match.group(1)
        value = resolve_variable(var_path, context)
        if value is None:
            return match.group(0)
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    return re.sub(r"\{([^}]+)\}", _replace, template)


async def resolve_storage_refs(prompt: str, storage: StorageBackend) -> str:
    """Replace {storage.PATH} references with stored content."""

    async def _resolve(match: re.Match) -> str:
        path = match.group(1)
        content = await storage.read(path)
        return content if content is not None else match.group(0)

    pattern = re.compile(r"\{storage\.([^}]+)\}")
    result = prompt
    for match in pattern.finditer(prompt):
        replacement = await _resolve(match)
        result = result.replace(match.group(0), replacement, 1)

    return result


def _backoff_delay(attempt: int, backoff: str = "exponential") -> float:
    """Calculate backoff delay in seconds."""
    if backoff == "exponential":
        return min(2**attempt, 60)  # Cap at 60s
    return 2.0  # Fixed 2s delay


async def _save_run_step(
    run_id: str,
    step_id: str,
    status: str,
    parallel_index: int | None = None,
    output: Any = None,
    cost_usd: float = 0.0,
    duration_seconds: float = 0.0,
    attempt: int = 1,
    error: str | None = None,
) -> None:
    """Create or update a RunStep record in the database."""
    try:
        from sandcastle.models.db import RunStep, StepStatus, async_session

        status_map = {
            "pending": StepStatus.PENDING,
            "running": StepStatus.RUNNING,
            "completed": StepStatus.COMPLETED,
            "failed": StepStatus.FAILED,
            "skipped": StepStatus.SKIPPED,
            "awaiting_approval": StepStatus.AWAITING_APPROVAL,
        }

        async with async_session() as session:
            step = RunStep(
                run_id=uuid.UUID(run_id),
                step_id=step_id,
                parallel_index=parallel_index,
                status=status_map.get(status, StepStatus.PENDING),
                output_data=(
                    output if isinstance(output, dict)
                    else {"result": output} if output else None
                ),
                cost_usd=cost_usd,
                duration_seconds=duration_seconds,
                attempt=attempt,
                error=error,
                started_at=(
                    datetime.now(timezone.utc) if status == "running"
                    else None
                ),
                completed_at=(
                    datetime.now(timezone.utc)
                    if status in ("completed", "failed", "skipped")
                    else None
                ),
            )
            session.add(step)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save RunStep for {step_id}: {e}")


async def _save_checkpoint(
    run_id: str,
    step_id: str,
    stage_index: int,
    context: RunContext,
) -> None:
    """Save a checkpoint after completing a stage for replay/fork support."""
    try:
        from sandcastle.models.db import RunCheckpoint, async_session

        async with async_session() as session:
            checkpoint = RunCheckpoint(
                run_id=uuid.UUID(run_id),
                step_id=step_id,
                stage_index=stage_index,
                context_snapshot=context.snapshot(),
            )
            session.add(checkpoint)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save checkpoint for step {step_id}: {e}")


# In-memory cancel flags for local mode (no Redis)
_cancel_flags: set[str] = set()


def cancel_run_local(run_id: str) -> None:
    """Set cancel flag in-memory (local mode without Redis)."""
    _cancel_flags.add(run_id)


async def _check_cancel(run_id: str) -> bool:
    """Check if a run has been cancelled via Redis flag or in-memory set."""
    from sandcastle.config import settings

    if not settings.redis_url:
        # Local mode: check in-memory set
        return run_id in _cancel_flags

    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        result = await r.get(f"cancel:{run_id}")
        await r.aclose()
        return result is not None
    except Exception:
        return False


def _check_budget(context: RunContext) -> str | None:
    """Check if the run has exceeded its budget.

    Returns None if OK, "warning" at 80%, "exceeded" at 100%.
    """
    if context.max_cost_usd is None or context.max_cost_usd <= 0:
        return None
    ratio = context.total_cost / context.max_cost_usd
    if ratio >= 1.0:
        return "exceeded"
    if ratio >= 0.8:
        return "warning"
    return None


async def execute_step_with_retry(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandstormClient,
    storage: StorageBackend,
    parallel_index: int | None = None,
    step_overrides: dict | None = None,
) -> StepResult:
    """Execute a step with retry logic and exponential backoff."""
    # Apply step overrides for fork
    if step_overrides:
        if "prompt" in step_overrides:
            step = StepDefinition(
                id=step.id,
                prompt=step_overrides["prompt"],
                depends_on=step.depends_on,
                model=step_overrides.get("model", step.model),
                max_turns=step_overrides.get("max_turns", step.max_turns),
                timeout=step_overrides.get("timeout", step.timeout),
                parallel_over=step.parallel_over,
                output_schema=step.output_schema,
                retry=step.retry,
                fallback=step.fallback,
            )
        elif "model" in step_overrides:
            step = StepDefinition(
                id=step.id,
                prompt=step.prompt,
                depends_on=step.depends_on,
                model=step_overrides["model"],
                max_turns=step_overrides.get("max_turns", step.max_turns),
                timeout=step_overrides.get("timeout", step.timeout),
                parallel_over=step.parallel_over,
                output_schema=step.output_schema,
                retry=step.retry,
                fallback=step.fallback,
            )

    # AutoPilot: pick variant if configured
    autopilot_experiment = None
    autopilot_variant = None
    original_step = step

    if step.autopilot and step.autopilot.enabled and step.autopilot.variants:
        try:
            import random

            from sandcastle.engine.autopilot import (
                apply_variant,
                get_or_create_experiment,
                pick_variant,
            )

            if random.random() <= step.autopilot.sample_rate:
                experiment = await get_or_create_experiment(
                    workflow_name=context.workflow_name,
                    step_id=step.id,
                    config=step.autopilot,
                )
                autopilot_experiment = experiment
                variant = await pick_variant(experiment.id, step.autopilot.variants)
                if variant:
                    autopilot_variant = variant
                    step = apply_variant(step, variant)
                    logger.info(
                        f"AutoPilot: step '{step.id}' using variant '{variant.id}'"
                    )
        except Exception as e:
            logger.warning(f"AutoPilot variant selection failed, using baseline: {e}")

    max_attempts = step.retry.max_attempts if step.retry else 1
    backoff = step.retry.backoff if step.retry else "exponential"

    # Record step as running
    await _save_run_step(
        run_id=context.run_id,
        step_id=step.id,
        status="running",
        parallel_index=parallel_index,
    )

    for attempt in range(1, max_attempts + 1):
        result = await _execute_step_once(
            step, context, sandbox, storage, parallel_index, attempt
        )

        if result.status == "completed":
            # AutoPilot: evaluate and save sample
            if autopilot_experiment and autopilot_variant:
                try:
                    from sandcastle.engine.autopilot import (
                        evaluate_result,
                        maybe_complete_experiment,
                        save_sample,
                    )

                    score = await evaluate_result(
                        original_step.autopilot, original_step, result.output
                    )
                    await save_sample(
                        experiment_id=autopilot_experiment.id,
                        run_id=context.run_id,
                        variant=autopilot_variant,
                        output=result.output,
                        quality_score=score,
                        cost_usd=result.cost_usd,
                        duration_seconds=result.duration_seconds,
                    )
                    await maybe_complete_experiment(
                        autopilot_experiment.id, original_step.autopilot
                    )
                except Exception as e:
                    logger.warning(f"AutoPilot sample recording failed: {e}")

            # Record step completion
            await _save_run_step(
                run_id=context.run_id,
                step_id=step.id,
                status="completed",
                parallel_index=parallel_index,
                output=result.output,
                cost_usd=result.cost_usd,
                duration_seconds=result.duration_seconds,
                attempt=attempt,
            )
            return result

        # Last attempt - check for fallback
        if attempt >= max_attempts:
            on_failure = step.retry.on_failure if step.retry else "abort"

            # Try fallback prompt if configured
            if on_failure == "fallback" and step.fallback and step.fallback.prompt:
                logger.info(f"Step '{step.id}' failed, trying fallback prompt")
                fallback_result = await _execute_fallback(
                    step, context, sandbox, storage, parallel_index, attempt
                )
                if fallback_result.status == "completed":
                    await _save_run_step(
                        run_id=context.run_id,
                        step_id=step.id,
                        status="completed",
                        parallel_index=parallel_index,
                        output=fallback_result.output,
                        cost_usd=result.cost_usd + fallback_result.cost_usd,
                        duration_seconds=result.duration_seconds + fallback_result.duration_seconds,
                        attempt=attempt,
                    )
                    return fallback_result

            logger.warning(
                f"Step '{step.id}' failed after {max_attempts} attempts: {result.error}"
            )
            # Record step failure
            await _save_run_step(
                run_id=context.run_id,
                step_id=step.id,
                status="failed",
                parallel_index=parallel_index,
                cost_usd=result.cost_usd,
                duration_seconds=result.duration_seconds,
                attempt=attempt,
                error=result.error,
            )
            return result

        delay = _backoff_delay(attempt, backoff)
        logger.info(
            f"Step '{step.id}' attempt {attempt} failed, retrying in {delay}s..."
        )
        await asyncio.sleep(delay)

    return result  # Should not reach here


async def _execute_fallback(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandstormClient,
    storage: StorageBackend,
    parallel_index: int | None = None,
    attempt: int = 1,
) -> StepResult:
    """Execute the fallback prompt for a step."""
    started_at = datetime.now(timezone.utc)
    try:
        prompt = resolve_templates(step.fallback.prompt, context)
        prompt = await resolve_storage_refs(prompt, storage)

        request: dict[str, Any] = {
            "prompt": prompt,
            "model": step.fallback.model,
            "max_turns": step.max_turns,
            "timeout": step.timeout,
        }

        logger.info(f"Executing fallback for step '{step.id}' (model={step.fallback.model})")
        result = await sandbox.query(request)
        output = result.structured_output if result.structured_output else result.text
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            output=output,
            cost_usd=result.total_cost_usd,
            duration_seconds=duration,
            status="completed",
            attempt=attempt,
        )
    except Exception as e:
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.error(f"Fallback for step '{step.id}' also failed: {e}")
        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            cost_usd=0.0,
            duration_seconds=duration,
            status="failed",
            error=f"Fallback failed: {e}",
            attempt=attempt,
        )


async def _execute_step_once(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandstormClient,
    storage: StorageBackend,
    parallel_index: int | None = None,
    attempt: int = 1,
) -> StepResult:
    """Execute a single attempt of a step."""
    started_at = datetime.now(timezone.utc)

    try:
        # SLO-based model selection (optimizer)
        routing_decision = None
        effective_model = step.model
        effective_max_turns = step.max_turns
        if hasattr(step, "slo") and step.slo and hasattr(step, "model_pool") and step.model_pool:
            try:
                from sandcastle.engine.optimizer import (
                    SLO,
                    CostLatencyOptimizer,
                    ModelOption,
                    calculate_budget_pressure,
                )

                slo = SLO(
                    quality_min=step.slo.quality_min,
                    cost_max_usd=step.slo.cost_max_usd,
                    latency_max_seconds=step.slo.latency_max_seconds,
                    optimize_for=step.slo.optimize_for,
                )
                pool = [
                    ModelOption(id=m.id, model=m.model, max_turns=m.max_turns)
                    for m in step.model_pool
                ]
                bp = calculate_budget_pressure(context.total_cost, context.max_cost_usd)

                optimizer = CostLatencyOptimizer()
                decision = await optimizer.select_model(
                    step_id=step.id,
                    workflow_name=context.workflow_name,
                    slo=slo,
                    model_pool=pool,
                    budget_pressure=bp,
                )
                routing_decision = decision
                effective_model = decision.selected_option.model
                effective_max_turns = decision.selected_option.max_turns
                logger.info(
                    f"Optimizer: step '{step.id}' -> {effective_model} "
                    f"({decision.reason}, confidence={decision.confidence:.1%})"
                )
            except Exception as e:
                logger.warning(f"Optimizer failed for step '{step.id}', using default: {e}")

        prompt = resolve_templates(step.prompt, context)
        prompt = await resolve_storage_refs(prompt, storage)

        request: dict[str, Any] = {
            "prompt": prompt,
            "model": effective_model,
            "max_turns": effective_max_turns,
            "timeout": step.timeout,
        }
        if step.output_schema:
            request["output_format"] = {
                "type": "json_schema",
                "schema": step.output_schema,
            }

        idx_str = f" [{parallel_index}]" if parallel_index is not None else ""
        logger.info(
            f"Executing step '{step.id}'{idx_str} attempt {attempt} "
            f"(model={effective_model})"
        )
        result = await sandbox.query(request)

        # Save routing decision to DB
        if routing_decision:
            await _save_routing_decision(
                context.run_id, step.id, routing_decision, step.slo
            )

        output = result.structured_output if result.structured_output else result.text
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        # Policy evaluation
        if hasattr(step, "policies") and step.policies is not None:
            try:
                from sandcastle.engine.policy import PolicyEngine, resolve_step_policies

                applicable = resolve_step_policies(step.policies, [])
                if applicable:
                    engine = PolicyEngine(applicable)
                    eval_result = await engine.evaluate(
                        step_id=step.id,
                        output=output,
                        context={
                            "step_id": step.id,
                            "run_id": context.run_id,
                            "total_cost_usd": context.total_cost,
                            "input": context.input,
                        },
                        step_cost_usd=result.total_cost_usd,
                    )
                    if eval_result.violations:
                        await _save_policy_violations(
                            context.run_id, step.id, eval_result.violations
                        )

                    if eval_result.should_block:
                        raise StepBlocked(
                            step_id=step.id,
                            reason=eval_result.block_reason or "Policy blocked",
                        )

                    if eval_result.should_inject_approval:
                        # Reuse approval gate mechanism
                        from sandcastle.models.db import (
                            ApprovalRequest,
                            ApprovalStatus,
                            Run,
                            RunStatus,
                        )
                        from sandcastle.models.db import (
                            async_session as db_session,
                        )
                        config = eval_result.approval_config or {}
                        async with db_session() as session:
                            approval = ApprovalRequest(
                                run_id=uuid.UUID(context.run_id),
                                step_id=step.id,
                                status=ApprovalStatus.PENDING,
                                request_data=(
                                    output if isinstance(output, dict)
                                    else {"result": output}
                                ),
                                message=config.get(
                                    "message", "Policy requires approval"
                                ),
                                timeout_at=None,
                                on_timeout=config.get("on_timeout", "abort"),
                                allow_edit=False,
                            )
                            if config.get("timeout_hours"):
                                from datetime import timedelta
                                approval.timeout_at = datetime.now(
                                    timezone.utc
                                ) + timedelta(hours=config["timeout_hours"])
                            session.add(approval)
                            run = await session.get(Run, uuid.UUID(context.run_id))
                            if run:
                                run.status = RunStatus.AWAITING_APPROVAL
                            await session.commit()
                            await session.refresh(approval)
                            approval_id = str(approval.id)
                        raise WorkflowPaused(
                            approval_id=approval_id, run_id=context.run_id
                        )

                    # Use modified output (after redactions)
                    output = eval_result.modified_output
            except (StepBlocked, WorkflowPaused):
                raise
            except Exception as e:
                logger.warning(f"Policy evaluation failed for step '{step.id}': {e}")

        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            output=output,
            cost_usd=result.total_cost_usd,
            duration_seconds=duration,
            status="completed",
            attempt=attempt,
        )

    except (StepBlocked, WorkflowPaused):
        raise

    except (SandstormError, Exception) as e:
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.error(f"Step '{step.id}' attempt {attempt} error: {e}")
        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            cost_usd=0.0,
            duration_seconds=duration,
            status="failed",
            error=str(e),
            attempt=attempt,
        )


async def _execute_approval_step(
    step: StepDefinition,
    context: RunContext,
    stage_index: int,
) -> None:
    """Create an approval request and pause the workflow.

    Raises WorkflowPaused to halt execution until the approval is resolved.
    """
    from sandcastle.models.db import (
        ApprovalRequest,
        ApprovalStatus,
        Run,
        RunStatus,
        async_session,
    )

    # Resolve show_data if configured
    request_data = None
    if step.approval_config and step.approval_config.show_data:
        request_data_val = resolve_variable(step.approval_config.show_data, context)
        if request_data_val is not None:
            if isinstance(request_data_val, dict):
                request_data = request_data_val
            else:
                request_data = {"value": request_data_val}

    # Calculate timeout
    timeout_at = None
    if step.approval_config and step.approval_config.timeout_hours:
        from datetime import timedelta
        timeout_at = datetime.now(timezone.utc) + timedelta(
            hours=step.approval_config.timeout_hours
        )

    on_timeout = step.approval_config.on_timeout if step.approval_config else "abort"
    allow_edit = step.approval_config.allow_edit if step.approval_config else False
    message = step.approval_config.message if step.approval_config else "Approval required"

    # Save checkpoint before pausing
    await _save_checkpoint(context.run_id, step.id, stage_index, context)

    # Record step as awaiting approval
    await _save_run_step(
        run_id=context.run_id,
        step_id=step.id,
        status="awaiting_approval",
    )

    # Create approval request
    async with async_session() as session:
        approval = ApprovalRequest(
            run_id=uuid.UUID(context.run_id),
            step_id=step.id,
            status=ApprovalStatus.PENDING,
            request_data=request_data,
            message=message,
            timeout_at=timeout_at,
            on_timeout=on_timeout,
            allow_edit=allow_edit,
        )
        session.add(approval)

        # Update run status to AWAITING_APPROVAL
        run = await session.get(Run, uuid.UUID(context.run_id))
        if run:
            run.status = RunStatus.AWAITING_APPROVAL
        await session.commit()
        await session.refresh(approval)
        approval_id = str(approval.id)

    # Fire webhook
    try:
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        run_obj = None
        async with async_session() as session:
            run_obj = await session.get(Run, uuid.UUID(context.run_id))

        if run_obj and run_obj.callback_url:
            await dispatch_webhook(
                url=run_obj.callback_url,
                event="approval.requested",
                run_id=context.run_id,
                workflow=run_obj.workflow_name or "",
                status="awaiting_approval",
                outputs={"approval_id": approval_id, "step_id": step.id, "message": message},
            )
    except Exception as e:
        logger.warning(f"Could not dispatch approval webhook: {e}")

    raise WorkflowPaused(approval_id=approval_id, run_id=context.run_id)


async def _execute_sub_workflow_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend,
    depth: int = 0,
) -> StepResult:
    """Execute a sub-workflow step, with optional fan-out."""
    from sandcastle.config import settings
    from sandcastle.engine.dag import build_plan, parse_yaml_string, validate

    started_at = datetime.now(timezone.utc)

    if not step.sub_workflow or not step.sub_workflow.workflow:
        return StepResult(
            step_id=step.id, status="failed", error="Missing sub_workflow config"
        )

    # Depth check
    max_depth = settings.max_workflow_depth
    if depth >= max_depth:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Max workflow depth ({max_depth}) exceeded",
        )

    # Load and parse sub-workflow
    try:
        from pathlib import Path

        workflows_dir = Path(settings.workflows_dir)
        wf_name = step.sub_workflow.workflow
        yaml_path = None
        for candidate in [
            workflows_dir / f"{wf_name}.yaml",
            workflows_dir / wf_name,
        ]:
            if candidate.exists() and candidate.is_file():
                yaml_path = candidate
                break

        if yaml_path is None:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Sub-workflow '{wf_name}' not found",
            )

        yaml_content = yaml_path.read_text()
        sub_workflow = parse_yaml_string(yaml_content)

        errors = validate(sub_workflow)
        if errors:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Sub-workflow validation: {'; '.join(errors)}",
            )

        sub_plan = build_plan(sub_workflow)

    except Exception as e:
        return StepResult(
            step_id=step.id, status="failed", error=f"Sub-workflow load error: {e}"
        )

    # Resolve input mapping
    sub_input = {}
    for target_key, source_path in step.sub_workflow.input_mapping.items():
        sub_input[target_key] = resolve_variable(source_path, context)

    # Fan-out if parallel_over is configured
    if step.sub_workflow.parallel_over:
        items = resolve_variable(step.sub_workflow.parallel_over, context)
        if not isinstance(items, list):
            items = [items]

        semaphore = asyncio.Semaphore(step.sub_workflow.max_concurrent)

        async def run_sub(item: Any, index: int) -> WorkflowResult:
            async with semaphore:
                item_input = {**sub_input, "_item": item, "_index": index}
                sub_run_id = str(uuid.uuid4())
                return await execute_workflow(
                    workflow=sub_workflow,
                    plan=sub_plan,
                    input_data=item_input,
                    run_id=sub_run_id,
                    storage=storage,
                    depth=depth + 1,
                )

        tasks = [
            asyncio.create_task(run_sub(item, i))
            for i, item in enumerate(items)
        ]
        sub_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate outputs
        outputs = []
        total_cost = 0.0
        sub_run_ids = []
        for r in sub_results:
            if isinstance(r, Exception):
                outputs.append(None)
            else:
                outputs.append(r.outputs)
                total_cost += r.total_cost_usd
                sub_run_ids.append(r.run_id)

        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        # Apply output mapping if configured
        output = outputs
        if step.sub_workflow.output_mapping:
            mapped = {}
            for target, source in step.sub_workflow.output_mapping.items():
                parts = source.split(".")
                extracted = []
                for o in outputs:
                    val = o
                    for p in parts:
                        if isinstance(val, dict):
                            val = val.get(p)
                    extracted.append(val)
                mapped[target] = extracted
            output = mapped

        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=total_cost,
            duration_seconds=duration,
            status="completed",
        )

    else:
        # Single sub-workflow execution
        sub_run_id = str(uuid.uuid4())
        sub_result = await execute_workflow(
            workflow=sub_workflow,
            plan=sub_plan,
            input_data=sub_input,
            run_id=sub_run_id,
            storage=storage,
            depth=depth + 1,
        )

        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        # Apply output mapping
        output = sub_result.outputs
        if step.sub_workflow.output_mapping:
            mapped = {}
            for target, source in step.sub_workflow.output_mapping.items():
                parts = source.split(".")
                val = sub_result.outputs
                for p in parts:
                    if isinstance(val, dict):
                        val = val.get(p)
                mapped[target] = val
            output = mapped

        status = "completed" if sub_result.status == "completed" else "failed"
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=sub_result.total_cost_usd,
            duration_seconds=duration,
            status=status,
            error=sub_result.error,
        )


async def execute_workflow(
    workflow: WorkflowDefinition,
    plan: ExecutionPlan,
    input_data: dict,
    run_id: str | None = None,
    storage: StorageBackend | None = None,
    max_cost_usd: float | None = None,
    initial_context: dict | None = None,
    skip_steps: set[str] | None = None,
    step_overrides: dict[str, dict] | None = None,
    depth: int = 0,
) -> WorkflowResult:
    """Execute a full workflow with parallel stages and retry logic.

    Args:
        workflow: Parsed workflow definition.
        plan: Execution plan with topologically sorted stages.
        input_data: Input data for the workflow.
        run_id: Optional run UUID (generated if not provided).
        storage: Storage backend for file references.
        max_cost_usd: Budget limit - hard stop at 100%.
        initial_context: Pre-loaded context for replay/fork (skip_steps outputs).
        skip_steps: Set of step IDs to skip (already completed in replay).
        step_overrides: Per-step overrides for fork (e.g. {"score": {"model": "opus"}}).
        depth: Current nesting depth for hierarchical workflows.
    """
    from sandcastle.config import settings
    from sandcastle.engine.storage import LocalStorage

    # Depth check for hierarchical workflows
    if depth > settings.max_workflow_depth:
        return WorkflowResult(
            run_id=run_id or str(uuid.uuid4()),
            outputs={},
            total_cost_usd=0.0,
            status="failed",
            error=f"Max workflow depth ({settings.max_workflow_depth}) exceeded",
        )

    if run_id is None:
        run_id = str(uuid.uuid4())

    if storage is None:
        storage = LocalStorage()

    started_at = datetime.now(timezone.utc)
    context = RunContext(
        run_id=run_id, input=input_data, max_cost_usd=max_cost_usd,
        workflow_name=workflow.name,
    )

    # Restore context from checkpoint if doing replay/fork
    if initial_context:
        context.step_outputs = initial_context.get("step_outputs", {})
        context.costs = initial_context.get("costs", [])

    # Resolve global policies from workflow definition
    global_policies = []
    if hasattr(workflow, "policies") and workflow.policies:
        try:
            from sandcastle.engine.policy import (
                PolicyAction as PEPolicyAction,
            )
            from sandcastle.engine.policy import (
                PolicyDefinition as PEPolicyDefinition,
            )
            from sandcastle.engine.policy import (
                PolicyPattern as PEPolicyPattern,
            )
            from sandcastle.engine.policy import (
                PolicyTrigger as PEPolicyTrigger,
            )
            for gp in workflow.policies:
                # Convert DAG dataclasses to policy engine dataclasses
                pe_trigger = PEPolicyTrigger(
                    type=gp.trigger.type,
                    patterns=[
                        PEPolicyPattern(type=p.type, pattern=p.pattern)
                        for p in (gp.trigger.patterns or [])
                    ] if gp.trigger.patterns else None,
                    expression=gp.trigger.expression,
                )
                pe_action = PEPolicyAction(
                    type=gp.action.type,
                    replacement=gp.action.replacement,
                    apply_to=gp.action.apply_to,
                    approval_config=gp.action.approval_config,
                    message=gp.action.message,
                    notify=gp.action.notify,
                )
                global_policies.append(PEPolicyDefinition(
                    id=gp.id,
                    trigger=pe_trigger,
                    action=pe_action,
                    description=gp.description,
                    severity=gp.severity,
                ))
        except Exception as e:
            logger.warning(f"Could not load global policies: {e}")

    sandbox = SandstormClient(
        base_url=workflow.sandstorm_url or settings.sandstorm_url,
        anthropic_api_key=settings.anthropic_api_key,
        e2b_api_key=settings.e2b_api_key,
    )

    try:
        for stage_idx, stage in enumerate(plan.stages):
            # Check cancellation before each stage
            if await _check_cancel(run_id):
                logger.info(f"Run {run_id} cancelled before stage {stage_idx}")
                return WorkflowResult(
                    run_id=run_id,
                    outputs=context.step_outputs,
                    total_cost_usd=context.total_cost,
                    status="cancelled",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )

            # Check budget before each stage
            budget_status = _check_budget(context)
            if budget_status == "exceeded":
                cost = context.total_cost
                limit = context.max_cost_usd
                logger.warning(
                    f"Run {run_id} budget exceeded "
                    f"(${cost:.4f} / ${limit:.4f})"
                )
                return WorkflowResult(
                    run_id=run_id,
                    outputs=context.step_outputs,
                    total_cost_usd=cost,
                    status="budget_exceeded",
                    error=f"Budget exceeded: ${cost:.4f} >= ${limit:.4f}",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )
            elif budget_status == "warning":
                logger.warning(
                    f"Run {run_id} at 80%+ budget "
                    f"(${context.total_cost:.4f} / ${context.max_cost_usd:.4f})"
                )

            # Collect all tasks for this stage (parallel execution)
            tasks: list[asyncio.Task] = []
            task_meta: list[dict] = []  # Track which step/item each task is for

            for step_id in stage:
                # Skip steps that are already completed (replay/fork)
                if skip_steps and step_id in skip_steps:
                    logger.info(f"Skipping step '{step_id}' (replay/fork)")
                    continue

                step = workflow.get_step(step_id)
                overrides = (step_overrides or {}).get(step_id)

                # Resolve policies: step-level overrides or global policies
                if global_policies and step.policies is None:
                    # No step-level override -> apply all global policies
                    step = StepDefinition(
                        id=step.id,
                        prompt=step.prompt,
                        depends_on=step.depends_on,
                        model=step.model,
                        max_turns=step.max_turns,
                        timeout=step.timeout,
                        parallel_over=step.parallel_over,
                        output_schema=step.output_schema,
                        retry=step.retry,
                        fallback=step.fallback,
                        type=step.type,
                        approval_config=step.approval_config,
                        autopilot=step.autopilot,
                        sub_workflow=step.sub_workflow,
                        policies=global_policies,
                    )
                elif global_policies and step.policies:
                    # Step has explicit policy list -> resolve refs against globals
                    try:
                        from sandcastle.engine.policy import resolve_step_policies
                        resolved = resolve_step_policies(step.policies, global_policies)
                        step = StepDefinition(
                            id=step.id,
                            prompt=step.prompt,
                            depends_on=step.depends_on,
                            model=step.model,
                            max_turns=step.max_turns,
                            timeout=step.timeout,
                            parallel_over=step.parallel_over,
                            output_schema=step.output_schema,
                            retry=step.retry,
                            fallback=step.fallback,
                            type=step.type,
                            approval_config=step.approval_config,
                            autopilot=step.autopilot,
                            sub_workflow=step.sub_workflow,
                            policies=resolved,
                        )
                    except Exception as e:
                        logger.warning(f"Could not resolve step policies: {e}")

                # Handle approval gate steps
                if step.type == "approval":
                    await _execute_approval_step(step, context, stage_idx)
                    continue  # Won't reach here - WorkflowPaused is raised

                # Handle sub-workflow steps
                if step.type == "sub_workflow":
                    sub_result = await _execute_sub_workflow_step(
                        step, context, storage, depth=depth,
                    )
                    context.costs.append(sub_result.cost_usd)
                    if sub_result.status == "completed":
                        context.step_outputs[step_id] = sub_result.output
                        await _save_run_step(
                            run_id=context.run_id,
                            step_id=step.id,
                            status="completed",
                            output=sub_result.output,
                            cost_usd=sub_result.cost_usd,
                            duration_seconds=sub_result.duration_seconds,
                        )
                    else:
                        raise StepExecutionError(
                            f"Sub-workflow step '{step_id}' failed: {sub_result.error}"
                        )
                    continue

                if step.parallel_over:
                    # Fan-out: one task per item
                    items = resolve_variable(step.parallel_over, context)
                    if not isinstance(items, list):
                        items = [items]

                    for i, item in enumerate(items):
                        item_context = context.with_item(item, i)
                        task = asyncio.create_task(
                            execute_step_with_retry(
                                step, item_context, sandbox, storage,
                                parallel_index=i, step_overrides=overrides,
                            )
                        )
                        tasks.append(task)
                        task_meta.append({
                            "step_id": step_id,
                            "fan_out": True,
                            "index": i,
                        })
                else:
                    task = asyncio.create_task(
                        execute_step_with_retry(
                            step, context, sandbox, storage,
                            step_overrides=overrides,
                        )
                    )
                    tasks.append(task)
                    task_meta.append({"step_id": step_id, "fan_out": False})

            if not tasks:
                continue

            # Await all tasks in the stage concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            fan_out_results: dict[str, list] = {}

            for i, (result, meta) in enumerate(zip(results, task_meta)):
                step_id = meta["step_id"]
                step = workflow.get_step(step_id)

                if isinstance(result, Exception):
                    result = StepResult(
                        step_id=step_id,
                        status="failed",
                        error=str(result),
                    )

                context.costs.append(result.cost_usd)

                # Check if workflow has dead_letter enabled
                use_dead_letter = (
                    workflow.on_failure
                    and workflow.on_failure.dead_letter
                )

                if meta["fan_out"]:
                    if step_id not in fan_out_results:
                        fan_out_results[step_id] = []

                    if result.status == "failed":
                        on_failure = step.retry.on_failure if step.retry else "abort"
                        if use_dead_letter:
                            await _send_to_dead_letter(
                                run_id=run_id,
                                step_id=step_id,
                                error=result.error,
                                input_data={"_item_index": meta["index"]},
                                attempts=result.attempt,
                                parallel_index=meta["index"],
                            )
                            fan_out_results[step_id].append(None)
                        elif on_failure == "abort":
                            raise StepExecutionError(
                                f"Step '{step_id}' item {meta['index']} failed: {result.error}"
                            )
                        else:
                            fan_out_results[step_id].append(None)
                    else:
                        fan_out_results[step_id].append(result.output)
                else:
                    if result.status == "failed":
                        on_failure = step.retry.on_failure if step.retry else "abort"
                        if use_dead_letter:
                            await _send_to_dead_letter(
                                run_id=run_id,
                                step_id=step_id,
                                error=result.error,
                                input_data=context.input,
                                attempts=result.attempt,
                            )
                            context.step_outputs[step_id] = None
                        elif on_failure == "abort":
                            raise StepExecutionError(
                                f"Step '{step_id}' failed: {result.error}"
                            )
                        else:
                            context.step_outputs[step_id] = None
                    else:
                        context.step_outputs[step_id] = result.output

            # Store fan-out results
            for step_id, items in fan_out_results.items():
                context.step_outputs[step_id] = items

            # Save checkpoint after each completed stage
            last_step_in_stage = stage[-1] if stage else "unknown"
            await _save_checkpoint(run_id, last_step_in_stage, stage_idx, context)

            # Post-stage cancel check (catches cancel during execution)
            if await _check_cancel(run_id):
                logger.info(
                    f"Run {run_id} cancelled after stage {stage_idx}"
                )
                return WorkflowResult(
                    run_id=run_id,
                    outputs=context.step_outputs,
                    total_cost_usd=context.total_cost,
                    status="cancelled",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )

            # Post-stage budget check (catches overrun immediately)
            post_budget = _check_budget(context)
            if post_budget == "exceeded":
                cost = context.total_cost
                limit = context.max_cost_usd
                logger.warning(
                    f"Run {run_id} budget exceeded after stage "
                    f"{stage_idx} (${cost:.4f} / ${limit:.4f})"
                )
                return WorkflowResult(
                    run_id=run_id,
                    outputs=context.step_outputs,
                    total_cost_usd=cost,
                    status="budget_exceeded",
                    error=f"Budget exceeded: ${cost:.4f} >= ${limit:.4f}",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )

        completed_at = datetime.now(timezone.utc)

        # Store results if configured
        if workflow.on_complete and workflow.on_complete.storage_path:
            storage_path = resolve_templates(workflow.on_complete.storage_path, context)
            await storage.write(storage_path, json.dumps(context.step_outputs))

        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="completed",
            started_at=started_at,
            completed_at=completed_at,
        )

    except WorkflowPaused:
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="awaiting_approval",
            error=None,
            started_at=started_at,
            completed_at=None,
        )

    except StepBlocked as e:
        completed_at = datetime.now(timezone.utc)
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="failed",
            error=f"Policy blocked: {e}",
            started_at=started_at,
            completed_at=completed_at,
        )

    except StepExecutionError as e:
        completed_at = datetime.now(timezone.utc)
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="failed",
            error=str(e),
            started_at=started_at,
            completed_at=completed_at,
        )

    except Exception as e:
        completed_at = datetime.now(timezone.utc)
        logger.error(f"Workflow '{workflow.name}' failed: {e}")
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="failed",
            error=str(e),
            started_at=started_at,
            completed_at=completed_at,
        )

    finally:
        await sandbox.close()


async def _save_routing_decision(
    run_id: str,
    step_id: str,
    decision: Any,
    slo_config: Any = None,
) -> None:
    """Save an optimizer routing decision to the database."""
    try:
        from sandcastle.models.db import RoutingDecision as DBRoutingDecision
        from sandcastle.models.db import async_session

        slo_data = None
        if slo_config:
            slo_data = {
                "quality_min": slo_config.quality_min,
                "cost_max_usd": slo_config.cost_max_usd,
                "latency_max_seconds": slo_config.latency_max_seconds,
                "optimize_for": slo_config.optimize_for,
            }

        alternatives_data = [
            {
                "id": a.id,
                "model": a.model,
                "avg_quality": a.avg_quality,
                "avg_cost": a.avg_cost,
            }
            for a in decision.alternatives
        ]

        async with async_session() as session:
            rd = DBRoutingDecision(
                run_id=uuid.UUID(run_id),
                step_id=step_id,
                selected_model=decision.selected_option.model,
                selected_variant_id=decision.selected_option.id,
                reason=decision.reason,
                budget_pressure=decision.budget_pressure,
                confidence=decision.confidence,
                alternatives=alternatives_data,
                slo=slo_data,
            )
            session.add(rd)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save routing decision for {step_id}: {e}")


async def _save_policy_violations(
    run_id: str,
    step_id: str,
    violations: list,
) -> None:
    """Save policy violations to the database."""
    try:
        from sandcastle.models.db import PolicyViolation as DBPolicyViolation
        from sandcastle.models.db import async_session

        async with async_session() as session:
            for v in violations:
                pv = DBPolicyViolation(
                    run_id=uuid.UUID(run_id),
                    step_id=step_id,
                    policy_id=v.policy_id,
                    severity=v.severity,
                    trigger_details=v.trigger_details,
                    action_taken=v.action_taken,
                    output_modified=v.output_modified,
                )
                session.add(pv)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save policy violations for {step_id}: {e}")


async def _send_to_dead_letter(
    run_id: str,
    step_id: str,
    error: str | None,
    input_data: dict | None,
    attempts: int,
    parallel_index: int | None = None,
) -> None:
    """Insert a failed step into the dead letter queue."""
    try:
        from sandcastle.models.db import DeadLetterItem, async_session

        async with async_session() as session:
            dlq_item = DeadLetterItem(
                run_id=uuid.UUID(run_id),
                step_id=step_id,
                parallel_index=parallel_index,
                error=error,
                input_data=input_data,
                attempts=attempts,
            )
            session.add(dlq_item)
            await session.commit()
        logger.info(f"Step '{step_id}' sent to dead letter queue")
    except Exception as e:
        logger.error(f"Failed to insert into dead letter queue: {e}")


class StepExecutionError(Exception):
    """A step failed and the workflow should abort."""


class StepBlocked(Exception):
    """A step was blocked by a policy."""

    def __init__(self, step_id: str, reason: str):
        self.step_id = step_id
        self.reason = reason
        super().__init__(f"Step '{step_id}' blocked: {reason}")


class WorkflowPaused(Exception):
    """A workflow is paused waiting for human approval."""

    def __init__(self, approval_id: str, run_id: str):
        self.approval_id = approval_id
        self.run_id = run_id
        super().__init__(f"Workflow paused: approval {approval_id} for run {run_id}")
