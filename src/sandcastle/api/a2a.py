"""A2A (Agent-to-Agent) protocol server for Sandcastle.

Implements Google's A2A protocol to allow external AI agents to discover
and interact with Sandcastle workflows via a standardized JSON-RPC 2.0
interface.

Endpoints:
  GET  /.well-known/agent.json  - Agent Card (discovery, public)
  POST /a2a                     - JSON-RPC 2.0 task operations

Security:
  - The Agent Card endpoint is intentionally public (discovery).
  - The /a2a JSON-RPC endpoint respects auth when AUTH_REQUIRED=true.
    Auth middleware runs before this handler; tenant_id is extracted for
    tenant isolation on tasks/get and tasks/cancel.
  - Rate limiting is applied to tasks/send (expensive - creates sandboxes).
  - Request body size is capped at 512 KB to prevent DoS.
  - JSON-RPC 2.0 version field is validated.
  - Workflow names are validated against path traversal.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from sandcastle import __version__
from sandcastle.config import settings
from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.storage import create_storage
from sandcastle.models.db import Run, RunStatus, async_session

logger = logging.getLogger(__name__)

a2a_router = APIRouter(tags=["A2A Protocol"])

# Maximum JSON-RPC request body size (512 KB)
_MAX_BODY_SIZE = 512 * 1024

# Maximum allowed length for workflow names to prevent abuse
_MAX_WORKFLOW_NAME_LENGTH = 255

# Maximum allowed length for task IDs
_MAX_TASK_ID_LENGTH = 255

# Allowed skill IDs for tasks/send
_VALID_SKILL_IDS = {"run-workflow", "list-workflows"}


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


def _get_tenant_id_safe(request: Request) -> str | None:
    """Extract tenant_id from request state, returning None if unavailable.

    Unlike auth.get_tenant_id(), this does not raise on missing auth state
    because A2A endpoints may be accessed without auth when AUTH_REQUIRED=false.
    """
    return getattr(request.state, "tenant_id", None)


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
        "authentication": {
            "schemes": ["apiKey"],
            "credentials": None if not settings.auth_required else "required",
        },
    }


@a2a_router.get("/.well-known/agent.json")
async def agent_card(request: Request) -> JSONResponse:
    """Return the A2A Agent Card for service discovery.

    This endpoint is intentionally public per the A2A protocol spec -
    agents need to discover capabilities before authenticating.
    """
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
    yaml_files = sorted([*workflows_dir.glob("*.yaml"), *workflows_dir.glob("*.yml")])
    for yaml_file in yaml_files:
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
    if not isinstance(message, dict):
        return None
    parts = message.get("parts", [])
    if not isinstance(parts, list):
        return None
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "data":
            data = part.get("data", {})
            if isinstance(data, dict):
                name = data.get("workflow_name")
                if isinstance(name, str):
                    return name
        if part.get("type") == "text":
            text = part.get("text", "")
            if isinstance(text, str):
                text = text.strip()
                if text:
                    return text
    return None


def _extract_input(message: dict[str, Any]) -> dict[str, Any]:
    """Extract workflow input parameters from an A2A message."""
    if not isinstance(message, dict):
        return {}
    parts = message.get("parts", [])
    if not isinstance(parts, list):
        return {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "data":
            data = part.get("data", {})
            if isinstance(data, dict):
                input_val = data.get("input", {})
                if isinstance(input_val, dict):
                    return input_val
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


async def _handle_tasks_send(
    params: dict[str, Any],
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Handle tasks/send - create and execute a workflow.

    Args:
        params: JSON-RPC params dict.
        tenant_id: Tenant ID from auth middleware (None if auth disabled).
    """
    task_id = params.get("id", str(uuid.uuid4()))

    # Validate task ID format and length
    if not isinstance(task_id, str) or len(task_id) > _MAX_TASK_ID_LENGTH:
        return _build_task_response(
            "unknown", "failed", error="Invalid task id"
        )

    message = params.get("message", {})

    # Validate and restrict skill IDs
    skill_id = params.get("skillId", "run-workflow")
    if not isinstance(skill_id, str) or skill_id not in _VALID_SKILL_IDS:
        return _build_task_response(
            task_id, "failed", error=f"Unknown skill: {skill_id}"
        )

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

    # Validate workflow name length
    if len(workflow_name) > _MAX_WORKFLOW_NAME_LENGTH:
        return _build_task_response(
            task_id, "failed", error="Workflow name too long"
        )

    workflow_input = _extract_input(message)

    # Reject path traversal in workflow name
    if ".." in workflow_name or "/" in workflow_name or "\\" in workflow_name:
        return _build_task_response(
            task_id, "failed", error=f"Invalid workflow name: '{workflow_name}'"
        )

    # Reject null bytes and control characters in workflow name
    if any(ord(c) < 32 for c in workflow_name):
        return _build_task_response(
            task_id, "failed", error="Invalid characters in workflow name"
        )

    # Load workflow YAML
    workflows_dir = Path(settings.workflows_dir).resolve()
    slug = re.sub(r"[^a-z0-9]+", "-", workflow_name.lower()).strip("-")
    yaml_content: str | None = None
    for candidate in [
        workflows_dir / f"{workflow_name}.yaml",
        workflows_dir / f"{slug}.yaml",
    ]:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(workflows_dir):
            continue
        if resolved.exists() and resolved.is_file():
            yaml_content = resolved.read_text()
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

    # Create DB run record with tenant isolation
    run_id = task_id
    try:
        async with async_session() as session:
            try:
                run_uuid = uuid.UUID(run_id) if run_id else uuid.uuid4()
            except ValueError:
                run_uuid = uuid.uuid4()
            db_run = Run(
                id=run_uuid,
                workflow_name=workflow.name,
                status=RunStatus.RUNNING,
                input_data=workflow_input,
                started_at=datetime.now(timezone.utc),
                tenant_id=tenant_id,
            )
            run_id = str(db_run.id)
            session.add(db_run)
            await session.commit()
    except Exception as e:
        logger.error("A2A tasks/send DB error: %s", e)
        return _build_task_response(
            task_id, "failed", error="Failed to create task"
        )

    # Execute the workflow
    storage = create_storage()
    try:
        result = await execute_workflow(
            workflow=workflow,
            plan=plan,
            input_data=workflow_input,
            run_id=run_id,
            storage=storage,
        )
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
        "outputs": result.outputs,
    }
    a2a_state = _map_status(result.status)
    return _build_task_response(run_id, a2a_state, output_data=output)


async def _handle_tasks_get(
    params: dict[str, Any],
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Handle tasks/get - return task status from the DB.

    When auth is enabled, only returns tasks belonging to the caller's tenant.
    """
    task_id = params.get("id")
    if not task_id:
        return _build_task_response("unknown", "failed", error="Missing task id")

    if not isinstance(task_id, str) or len(task_id) > _MAX_TASK_ID_LENGTH:
        return _build_task_response("unknown", "failed", error="Invalid task id")

    try:
        run_uuid = uuid.UUID(task_id)
    except ValueError:
        return _build_task_response(
            task_id, "failed", error="Invalid task id format"
        )

    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        # Tenant isolation: filter by tenant_id when auth is enabled
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.where(Run.tenant_id == tenant_id)
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


async def _handle_tasks_cancel(
    params: dict[str, Any],
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Handle tasks/cancel - cancel a running workflow.

    When auth is enabled, only allows cancelling tasks belonging to the caller's tenant.
    """
    task_id = params.get("id")
    if not task_id:
        return _build_task_response("unknown", "failed", error="Missing task id")

    if not isinstance(task_id, str) or len(task_id) > _MAX_TASK_ID_LENGTH:
        return _build_task_response("unknown", "failed", error="Invalid task id")

    try:
        run_uuid = uuid.UUID(task_id)
    except ValueError:
        return _build_task_response(
            task_id, "failed", error="Invalid task id format"
        )

    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        # Tenant isolation
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.where(Run.tenant_id == tenant_id)
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

    _cancel_flags[task_id] = None

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

    Security:
      - Auth middleware runs before this handler (unless AUTH_REQUIRED=false).
      - Rate limiting is applied to tasks/send.
      - Request body size is capped at 512 KB.
      - JSON-RPC version field is validated.
    """
    # Enforce body size limit to prevent DoS
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_BODY_SIZE:
                return _jsonrpc_error(
                    None, -32600, "Request body too large"
                )
        except ValueError:
            pass

    try:
        body_bytes = await request.body()
    except Exception:
        return _jsonrpc_error(None, -32700, "Parse error")

    if len(body_bytes) > _MAX_BODY_SIZE:
        return _jsonrpc_error(None, -32600, "Request body too large")

    try:
        import json
        body = json.loads(body_bytes)
    except Exception:
        return _jsonrpc_error(None, -32700, "Parse error")

    # Validate JSON-RPC structure
    if isinstance(body, list):
        # JSON-RPC 2.0 batch requests are not supported
        return _jsonrpc_error(
            None, -32600,
            "Batch requests are not supported"
        )

    if not isinstance(body, dict):
        return _jsonrpc_error(None, -32600, "Invalid Request")

    req_id = body.get("id")

    # Validate JSON-RPC version field
    jsonrpc_version = body.get("jsonrpc")
    if jsonrpc_version != "2.0":
        return _jsonrpc_error(
            req_id, -32600,
            "Invalid Request: jsonrpc field must be '2.0'"
        )

    method = body.get("method")
    params = body.get("params", {})

    if not method or not isinstance(method, str):
        return _jsonrpc_error(req_id, -32600, "Invalid Request: missing method")

    handler = _METHOD_HANDLERS.get(method)
    if not handler:
        return _jsonrpc_error(
            req_id, -32601, f"Method not found: {method}"
        )

    if not isinstance(params, dict):
        return _jsonrpc_error(req_id, -32602, "Invalid params: expected object")

    # Rate limit tasks/send (expensive - creates sandboxes)
    if method == "tasks/send":
        from sandcastle.api.rate_limit import execution_limiter

        try:
            await execution_limiter.check(request)
        except Exception as exc:
            # Convert HTTPException to JSON-RPC error format
            status_code = getattr(exc, "status_code", 429)
            if status_code == 429:
                return _jsonrpc_error(
                    req_id, -32000,
                    "Rate limit exceeded",
                    data={"retry_after": 60},
                )
            raise

    # Extract tenant_id for tenant isolation
    tenant_id = _get_tenant_id_safe(request)

    try:
        result = await handler(params, tenant_id=tenant_id)
        return _jsonrpc_result(req_id, result)
    except Exception:
        logger.exception("A2A handler error for method=%s", method)
        return _jsonrpc_error(req_id, -32603, "Internal error")
