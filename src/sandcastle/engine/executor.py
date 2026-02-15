"""Workflow executor — runs steps sequentially (Phase 1)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sandcastle.engine.dag import ExecutionPlan, StepDefinition, WorkflowDefinition
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
        # parts[2] should be "output"
        if parts[2] == "output":
            if len(parts) == 3:
                return step_data
            # Navigate deeper: steps.X.output.field
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
            return match.group(0)  # Leave unresolved
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

    # Process storage refs one at a time (async)
    pattern = re.compile(r"\{storage\.([^}]+)\}")
    result = prompt
    for match in pattern.finditer(prompt):
        replacement = await _resolve(match)
        result = result.replace(match.group(0), replacement, 1)

    return result


async def execute_step(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandstormClient,
    storage: StorageBackend,
    parallel_index: int | None = None,
) -> StepResult:
    """Execute a single step by calling Sandstorm."""
    started_at = datetime.now(timezone.utc)

    try:
        # Resolve template variables in prompt
        prompt = resolve_templates(step.prompt, context)
        prompt = await resolve_storage_refs(prompt, storage)

        # Build Sandstorm request
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

        logger.info(f"Executing step '{step.id}' (model={step.model})")
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
        )

    except SandstormError as e:
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.error(f"Step '{step.id}' failed: {e}")
        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            cost_usd=0.0,
            duration_seconds=duration,
            status="failed",
            error=str(e),
        )

    except Exception as e:
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.error(f"Step '{step.id}' unexpected error: {e}")
        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            cost_usd=0.0,
            duration_seconds=duration,
            status="failed",
            error=str(e),
        )


async def execute_workflow(
    workflow: WorkflowDefinition,
    plan: ExecutionPlan,
    input_data: dict,
    run_id: str | None = None,
    storage: StorageBackend | None = None,
) -> WorkflowResult:
    """Execute a full workflow sequentially (Phase 1 — no parallelism)."""
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
            for step_id in stage:
                step = workflow.get_step(step_id)

                if step.parallel_over:
                    # Fan-out: run one step per item (sequentially in Phase 1)
                    items = resolve_variable(step.parallel_over, context)
                    if not isinstance(items, list):
                        items = [items]

                    fan_results = []
                    for i, item in enumerate(items):
                        item_context = context.with_item(item, i)
                        result = await execute_step(
                            step, item_context, sandbox, storage, parallel_index=i
                        )
                        if result.status == "failed":
                            on_failure = step.retry.on_failure if step.retry else "abort"
                            if on_failure == "abort":
                                raise StepExecutionError(
                                    f"Step '{step_id}' item {i} failed: {result.error}"
                                )
                            elif on_failure == "skip":
                                result.status = "skipped"
                        fan_results.append(result.output)
                        context.costs.append(result.cost_usd)

                    context.step_outputs[step_id] = fan_results
                else:
                    result = await execute_step(step, context, sandbox, storage)
                    if result.status == "failed":
                        on_failure = step.retry.on_failure if step.retry else "abort"
                        if on_failure == "abort":
                            raise StepExecutionError(
                                f"Step '{step_id}' failed: {result.error}"
                            )
                        elif on_failure == "skip":
                            context.step_outputs[step_id] = None
                        else:
                            context.step_outputs[step_id] = None
                    else:
                        context.step_outputs[step_id] = result.output
                    context.costs.append(result.cost_usd)

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


class StepExecutionError(Exception):
    """A step failed and the workflow should abort."""
