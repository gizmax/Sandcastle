"""Workflow executor - parallel execution with retries and cost tracking."""

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
    FailureConfig,
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

    def with_item(self, item: Any, index: int) -> RunContext:
        """Create a child context for a parallel_over item."""
        return RunContext(
            run_id=self.run_id,
            input={**self.input, "_item": item, "_index": index},
            step_outputs=dict(self.step_outputs),
            costs=self.costs,
            status=self.status,
        )


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
    - input.X → from context.input
    - steps.STEP_ID.output → from context.step_outputs
    - steps.STEP_ID.output.FIELD → specific field
    - run_id → current run UUID
    - date → current ISO date
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


async def execute_step_with_retry(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandstormClient,
    storage: StorageBackend,
    parallel_index: int | None = None,
) -> StepResult:
    """Execute a step with retry logic and exponential backoff."""
    max_attempts = step.retry.max_attempts if step.retry else 1
    backoff = step.retry.backoff if step.retry else "exponential"

    for attempt in range(1, max_attempts + 1):
        result = await _execute_step_once(
            step, context, sandbox, storage, parallel_index, attempt
        )

        if result.status == "completed":
            return result

        # Last attempt - no more retries
        if attempt >= max_attempts:
            logger.warning(
                f"Step '{step.id}' failed after {max_attempts} attempts: {result.error}"
            )
            return result

        delay = _backoff_delay(attempt, backoff)
        logger.info(
            f"Step '{step.id}' attempt {attempt} failed, retrying in {delay}s..."
        )
        await asyncio.sleep(delay)

    return result  # Should not reach here


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
        prompt = resolve_templates(step.prompt, context)
        prompt = await resolve_storage_refs(prompt, storage)

        request: dict[str, Any] = {
            "prompt": prompt,
            "model": step.model,
            "max_turns": step.max_turns,
            "timeout": step.timeout,
        }
        if step.output_schema:
            request["output_format"] = {
                "type": "json_schema",
                "schema": step.output_schema,
            }

        idx_str = f" [{parallel_index}]" if parallel_index is not None else ""
        logger.info(f"Executing step '{step.id}'{idx_str} attempt {attempt} (model={step.model})")
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


async def execute_workflow(
    workflow: WorkflowDefinition,
    plan: ExecutionPlan,
    input_data: dict,
    run_id: str | None = None,
    storage: StorageBackend | None = None,
) -> WorkflowResult:
    """Execute a full workflow with parallel stages and retry logic."""
    from sandcastle.config import settings
    from sandcastle.engine.storage import LocalStorage

    if run_id is None:
        run_id = str(uuid.uuid4())

    if storage is None:
        storage = LocalStorage()

    started_at = datetime.now(timezone.utc)
    context = RunContext(run_id=run_id, input=input_data)

    sandbox = SandstormClient(
        base_url=workflow.sandstorm_url or settings.sandstorm_url,
        anthropic_api_key=settings.anthropic_api_key,
        e2b_api_key=settings.e2b_api_key,
    )

    try:
        for stage in plan.stages:
            # Collect all tasks for this stage (parallel execution)
            tasks: list[asyncio.Task] = []
            task_meta: list[dict] = []  # Track which step/item each task is for

            for step_id in stage:
                step = workflow.get_step(step_id)

                if step.parallel_over:
                    # Fan-out: one task per item
                    items = resolve_variable(step.parallel_over, context)
                    if not isinstance(items, list):
                        items = [items]

                    for i, item in enumerate(items):
                        item_context = context.with_item(item, i)
                        task = asyncio.create_task(
                            execute_step_with_retry(
                                step, item_context, sandbox, storage, parallel_index=i
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
                        execute_step_with_retry(step, context, sandbox, storage)
                    )
                    tasks.append(task)
                    task_meta.append({"step_id": step_id, "fan_out": False})

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

        completed_at = datetime.now(timezone.utc)

        # Store results if configured
        if workflow.on_complete and workflow.on_complete.storage_path:
            storage_path = resolve_templates(workflow.on_complete.storage_path, context)
            await storage.write(storage_path, json.dumps(context.step_outputs))

        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=sum(context.costs),
            status="completed",
            started_at=started_at,
            completed_at=completed_at,
        )

    except StepExecutionError as e:
        completed_at = datetime.now(timezone.utc)
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=sum(context.costs),
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
            total_cost_usd=sum(context.costs),
            status="failed",
            error=str(e),
            started_at=started_at,
            completed_at=completed_at,
        )

    finally:
        await sandbox.close()


async def _send_to_dead_letter(
    run_id: str,
    step_id: str,
    error: str | None,
    input_data: dict | None,
    attempts: int,
) -> None:
    """Insert a failed step into the dead letter queue."""
    try:
        from sandcastle.models.db import DeadLetterItem, async_session

        async with async_session() as session:
            import uuid as _uuid

            dlq_item = DeadLetterItem(
                run_id=_uuid.UUID(run_id),
                step_id=step_id,
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
