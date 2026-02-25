"""A2A (Agent-to-Agent) protocol server for Sandcastle.

Implements Google's A2A protocol to allow external AI agents to discover
and interact with Sandcastle workflows via a standardized JSON-RPC 2.0
interface.

Endpoints:
  GET  /.well-known/agent.json  - Agent Card (discovery)
  POST /a2a                     - JSON-RPC 2.0 task operations
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from sandcastle import __version__
from sandcastle.config import settings
from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.sandshore import SandshoreRuntime
from sandcastle.engine.storage import create_storage
from sandcastle.models.db import Run, RunStatus, async_session

from sqlalchemy import select

logger = logging.getLogger(__name__)

a2a_router = APIRouter(tags=["A2A Protocol"])


# -- State mapping: Sandcastle -> A2A --

_STATUS_MAP: dict[str, str] = {
    "queued": "submitted",
    "running": "working",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "canceled",
    "partial": "completed",
    "budget_exceeded": "failed",
    "awaiting_approval": "input-required",
}


def _map_status(sandcastle_status: str) -> str:
    """Map a Sandcastle run status to an A2A task state."""
    return _STATUS_MAP.get(sandcastle_status, "unknown")


# -- Agent Card --


def _build_agent_card(base_url: str) -> dict[str, Any]:
    """Build the A2A Agent Card payload."""
    return {
        "name": "Sandcastle",
        "description": "AI Agent Workflow Orchestrator",
        "url": base_url,
        "version": __version__,
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
        },
        "skills": [
            {
                "id": "run-workflow",
                "name": "Run Workflow",
                "description": "Execute a named workflow with optional input parameters",
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "list-workflows",
                "name": "List Workflows",
                "description": "List all available workflow templates",
                "inputModes": ["text/plain"],
                "outputModes": ["application/json"],
            },
        ],
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json"],
    }


@a2a_router.get("/.well-known/agent.json")
async def agent_card(request: Request) -> JSONResponse:
    """Return the A2A Agent Card for service discovery."""
    base_url = str(request.base_url).rstrip("/")
    return JSONResponse(content=_build_agent_card(base_url))


# -- JSON-RPC helpers --


def _jsonrpc_error(
    req_id: str | int | None,
    code: int,
    message: str,
    data: Any = None,
) -> JSONResponse:
    """Build a JSON-RPC 2.0 error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JSONResponse(
        content={"jsonrpc": "2.0", "id": req_id, "error": error}
    )


def _jsonrpc_result(req_id: str | int | None, result: Any) -> JSONResponse:
    """Build a JSON-RPC 2.0 success response."""
    return JSONResponse(
        content={"jsonrpc": "2.0", "id": req_id, "result": result}
    )


# -- Task operations --


async def _list_available_workflows() -> list[dict[str, Any]]:
    """List workflow YAML files from the workflows directory."""
    workflows_dir = Path(settings.workflows_dir)
    if not workflows_dir.exists():
        return []

    items: list[dict[str, Any]] = []
    for yaml_file in sorted(workflows_dir.glob("*.yaml")):
        try:
            content = yaml_file.read_text()
            workflow = parse_yaml_string(content)
            items.append({
                "name": workflow.name,
                "description": workflow.description or "",
                "steps_count": len(workflow.steps),
            })
        except Exception:
            continue
    return items


def _extract_workflow_name(message: dict[str, Any]) -> str | None:
    """Extract workflow name from an A2A message.

    Supports both text/plain (just the workflow name) and
    application/json ({"workflow_name": "...", "input": {...}}).
    """
    parts = message.get("parts", [])
    for part in parts:
        if part.get("type") == "data":
            data = part.get("data", {})
            if isinstance(data, dict):
                return data.get("workflow_name")
        if part.get("type") == "text":
            text = part.get("text", "").strip()
            if text:
                return text
    return None


def _extract_input(message: dict[str, Any]) -> dict[str, Any]:
    """Extract workflow input parameters from an A2A message."""
    parts = message.get("parts", [])
    for part in parts:
        if part.get("type") == "data":
            data = part.get("data", {})
            if isinstance(data, dict):
                return data.get("input", {})
    return {}


def _build_task_response(
    task_id: str,
    state: str,
    output_data: dict | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build an A2A Task object."""
    task: dict[str, Any] = {
        "id": task_id,
        "status": {
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    if error:
        task["status"]["message"] = error

    if output_data is not None:
        task["artifacts"] = [
            {
                "parts": [
                    {"type": "data", "data": output_data}
                ],
            }
        ]
    return task


async def _handle_tasks_send(params: dict[str, Any]) -> dict[str, Any]:
    """Handle tasks/send - create and execute a workflow."""
    task_id = params.get("id", str(uuid.uuid4()))
    message = params.get("message", {})

    # Determine skill from message
    skill_id = params.get("skillId", "run-workflow")

    if skill_id == "list-workflows":
        workflows = await _list_available_workflows()
        return _build_task_response(
            task_id, "completed", output_data={"workflows": workflows}
        )

    # run-workflow: resolve workflow name and input
    workflow_name = _extract_workflow_name(message)
    if not workflow_name:
        return _build_task_response(
            task_id, "failed", error="Missing workflow_name in message"
        )

    workflow_input = _extract_input(message)

    # Load workflow YAML
    import re

    workflows_dir = Path(settings.workflows_dir)
    slug = re.sub(r"[^a-z0-9]+", "-", workflow_name.lower()).strip("-")
    yaml_content: str | None = None
    for candidate in [
        workflows_dir / f"{workflow_name}.yaml",
        workflows_dir / f"{slug}.yaml",
    ]:
        if candidate.exists() and candidate.is_file():
            yaml_content = candidate.read_text()
            break

    if yaml_content is None:
        return _build_task_response(
            task_id, "failed", error=f"Workflow '{workflow_name}' not found"
        )

    # Parse and validate
    try:
        workflow = parse_yaml_string(yaml_content)
    except Exception as e:
        return _build_task_response(
            task_id, "failed", error=f"Invalid workflow: {e}"
        )

    errors = validate(workflow)
    if errors:
        return _build_task_response(
            task_id, "failed", error="; ".join(errors)
        )

    plan = build_plan(workflow)

    # Create DB run record
    run_id = task_id
    try:
        async with async_session() as session:
            db_run = Run(
                id=uuid.UUID(run_id) if len(run_id) == 36 else uuid.uuid4(),
                workflow_name=workflow.name,
                status=RunStatus.RUNNING,
                input_data=workflow_input,
                started_at=datetime.now(timezone.utc),
            )
            run_id = str(db_run.id)
            session.add(db_run)
            await session.commit()
    except Exception as e:
        return _build_task_response(
            task_id, "failed", error=f"DB error: {e}"
        )

    # Execute the workflow
    storage = create_storage()
    try:
        runtime = SandshoreRuntime()
        result = await execute_workflow(
            plan=plan,
            workflow=workflow,
            input_data=workflow_input,
            run_id=run_id,
            runtime=runtime,
            storage=storage,
        )
        await runtime.close()
    except Exception as e:
        # Update run as failed
        async with async_session() as session:
            stmt = select(Run).where(Run.id == uuid.UUID(run_id))
            res = await session.execute(stmt)
            db_run = res.scalar_one_or_none()
            if db_run:
                db_run.status = RunStatus.FAILED
                db_run.error = str(e)
                db_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
        return _build_task_response(
            run_id, "failed", error=str(e)
        )

    # Build output from result
    output = {
        "run_id": run_id,
        "status": result.status,
        "total_cost_usd": result.total_cost_usd,
        "outputs": result.step_outputs,
    }
    a2a_state = _map_status(result.status)
    return _build_task_response(run_id, a2a_state, output_data=output)


async def _handle_tasks_get(params: dict[str, Any]) -> dict[str, Any]:
    """Handle tasks/get - return task status from the DB."""
    task_id = params.get("id")
    if not task_id:
        return _build_task_response("unknown", "failed", error="Missing task id")

    try:
        run_uuid = uuid.UUID(task_id)
    except ValueError:
        return _build_task_response(
            task_id, "failed", error="Invalid task id format"
        )

    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        return _build_task_response(
            task_id, "failed", error=f"Task '{task_id}' not found"
        )

    run_status = run.status.value if hasattr(run.status, "value") else run.status
    a2a_state = _map_status(run_status)

    output_data = None
    if run.output_data:
        output_data = {
            "run_id": str(run.id),
            "status": run_status,
            "total_cost_usd": run.total_cost_usd,
            "outputs": run.output_data,
        }

    task = _build_task_response(task_id, a2a_state, output_data=output_data)
    if run.error:
        task["status"]["message"] = run.error
    return task


async def _handle_tasks_cancel(params: dict[str, Any]) -> dict[str, Any]:
    """Handle tasks/cancel - cancel a running workflow."""
    task_id = params.get("id")
    if not task_id:
        return _build_task_response("unknown", "failed", error="Missing task id")

    try:
        run_uuid = uuid.UUID(task_id)
    except ValueError:
        return _build_task_response(
            task_id, "failed", error="Invalid task id format"
        )

    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        return _build_task_response(
            task_id, "failed", error=f"Task '{task_id}' not found"
        )

    run_status = run.status.value if hasattr(run.status, "value") else run.status
    if run_status not in ("queued", "running"):
        return _build_task_response(
            task_id, "failed",
            error=f"Cannot cancel task with status '{run_status}'"
        )

    # Set cancel flag (in-memory for local mode)
    from sandcastle.engine.executor import _cancel_flags

    _cancel_flags.add(task_id)

    # Update DB status
    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()
        if run:
            run.status = RunStatus.CANCELLED
            run.completed_at = datetime.now(timezone.utc)
            await session.commit()

    return _build_task_response(task_id, "canceled")


# -- JSON-RPC dispatch --


_METHOD_HANDLERS = {
    "tasks/send": _handle_tasks_send,
    "tasks/get": _handle_tasks_get,
    "tasks/cancel": _handle_tasks_cancel,
}


@a2a_router.post("/a2a")
async def a2a_endpoint(request: Request) -> JSONResponse:
    """JSON-RPC 2.0 endpoint for A2A task operations.

    Supported methods:
      - tasks/send: Execute a workflow
      - tasks/get: Get task status
      - tasks/cancel: Cancel a running task
    """
    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_error(None, -32700, "Parse error")

    # Validate JSON-RPC structure
    if not isinstance(body, dict):
        return _jsonrpc_error(None, -32600, "Invalid Request")

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    if not method:
        return _jsonrpc_error(req_id, -32600, "Invalid Request: missing method")

    handler = _METHOD_HANDLERS.get(method)
    if not handler:
        return _jsonrpc_error(
            req_id, -32601, f"Method not found: {method}"
        )

    if not isinstance(params, dict):
        return _jsonrpc_error(req_id, -32602, "Invalid params: expected object")

    try:
        result = await handler(params)
        return _jsonrpc_result(req_id, result)
    except Exception as e:
        logger.exception("A2A handler error for method=%s", method)
        return _jsonrpc_error(req_id, -32603, f"Internal error: {e}")
