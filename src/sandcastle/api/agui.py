"""AG-UI (Agent-User Interaction) protocol support for Sandcastle.

Streams typed JSON events from the Sandcastle event bus to AG-UI
compatible frontends via Server-Sent Events (SSE).

Endpoint:
  GET /api/agui/stream/{run_id} - SSE stream of AG-UI events for a run

Security:
  - When AUTH_REQUIRED=true, the auth middleware validates credentials
    before this handler runs. Tenant isolation ensures a user can only
    stream events for runs belonging to their tenant.
  - run_id is validated as a UUID to prevent injection.
  - Client disconnection is detected via request.is_disconnected().
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from sandcastle.config import settings
from sandcastle.engine.events import event_bus
from sandcastle.models.db import Run, async_session

logger = logging.getLogger(__name__)

agui_router = APIRouter(tags=["AG-UI Protocol"])


# -- AG-UI event types --

EVENT_RUN_STARTED = "run_started"
EVENT_STEP_STARTED = "step_started"
EVENT_TEXT_MESSAGE = "text_message"
EVENT_TOOL_CALL = "tool_call_start"
EVENT_TOOL_RESULT = "tool_call_end"
EVENT_STATE_DELTA = "state_delta"
EVENT_RUN_FINISHED = "run_finished"
EVENT_RUN_ERROR = "run_error"
EVENT_RUN_AWAITING_INPUT = "run_awaiting_input"


# -- Status mapping helpers --

_TERMINAL_STATUSES = {
    "completed", "failed", "partial", "cancelled",
    "budget_exceeded", "awaiting_approval",
}


def _run_status_str(run: Run) -> str:
    """Extract the string value of a Run's status."""
    return run.status.value if hasattr(run.status, "value") else run.status


# -- AG-UI event builders --


def _agui_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a single AG-UI SSE event line."""
    payload = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _map_event_bus_event(event: dict[str, Any], target_run_id: str) -> str | None:
    """Convert a Sandcastle EventBus event to an AG-UI SSE event.

    Returns the formatted SSE string, or None if the event should be skipped
    (e.g. belongs to a different run).
    """
    event_type = event.get("type", "")
    data = event.get("data", {})

    # Filter: only emit events for the target run
    run_id = data.get("run_id", "")
    if run_id and run_id != target_run_id:
        return None

    if event_type == "run.started":
        return _agui_event(EVENT_RUN_STARTED, {
            "run_id": data.get("run_id", ""),
            "workflow": data.get("workflow", data.get("workflow_name", "")),
        })

    if event_type == "run.completed":
        return _agui_event(EVENT_RUN_FINISHED, {
            "run_id": data.get("run_id", ""),
            "total_cost": data.get("total_cost_usd", 0),
        })

    if event_type == "run.failed":
        return _agui_event(EVENT_RUN_ERROR, {
            "run_id": data.get("run_id", ""),
            "error": data.get("error", "Unknown error"),
        })

    if event_type == "step.started":
        step_id = data.get("step_name", data.get("step_id", ""))
        return _agui_event(EVENT_STEP_STARTED, {
            "step_id": step_id,
            "type": data.get("step_type", "llm"),
        })

    if event_type == "step.completed":
        step_id = data.get("step_name", data.get("step_id", ""))
        output = data.get("output", "")
        content = output if isinstance(output, str) else json.dumps(output, default=str)

        text_event = _agui_event(EVENT_TEXT_MESSAGE, {
            "step_id": step_id,
            "content": content,
        })
        delta_event = _agui_event(EVENT_STATE_DELTA, {
            "op": "replace",
            "path": f"/steps/{step_id}/status",
            "value": "completed",
        })
        return text_event + delta_event

    if event_type == "step.failed":
        step_id = data.get("step_name", data.get("step_id", ""))
        return _agui_event(EVENT_STATE_DELTA, {
            "op": "replace",
            "path": f"/steps/{step_id}/status",
            "value": "failed",
        })

    # Unknown event type - skip
    return None


def _emit_terminal_event(run_id: str, status: str, run: Run):
    """Yield the appropriate AG-UI event for a terminal run status.

    Distinguishes between failed, awaiting_approval, and completed states
    so that clients can show the correct UI (error, approval dialog, or
    success).
    """
    if status in ("failed", "budget_exceeded"):
        yield _agui_event(EVENT_RUN_ERROR, {
            "run_id": run_id,
            "error": run.error or "Run failed",
        })
    elif status == "awaiting_approval":
        yield _agui_event(EVENT_RUN_AWAITING_INPUT, {
            "run_id": run_id,
            "message": "Run is awaiting approval",
        })
    else:
        yield _agui_event(EVENT_RUN_FINISHED, {
            "run_id": run_id,
            "total_cost": run.total_cost_usd or 0,
        })


def _get_tenant_id_safe(request: Request) -> str | None:
    """Extract tenant_id from request state without raising.

    Returns None when auth is disabled or tenant_id is not set.
    """
    return getattr(request.state, "tenant_id", None)


@agui_router.get("/stream/{run_id}")
async def agui_stream(run_id: str, request: Request) -> StreamingResponse:
    """Stream AG-UI typed events for a running workflow via SSE.

    Subscribes to the Sandcastle EventBus and transforms internal events
    to AG-UI format. Also polls the DB for initial state and terminal
    detection.

    Security:
      - run_id is validated as a UUID.
      - When auth is enabled, tenant isolation ensures only the run's
        owner can stream its events.
    """
    # Validate run_id format
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "data": None,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Invalid run ID format",
                },
            },
        )

    # Normalize run_id to the canonical UUID string (lowercase, no extra chars)
    run_id = str(run_uuid)

    # Verify the run exists and apply tenant isolation
    tenant_id = _get_tenant_id_safe(request)

    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        # Tenant isolation: only allow streaming runs belonging to the caller
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.where(Run.tenant_id == tenant_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=404,
            detail={"data": None, "error": {"code": "NOT_FOUND", "message": "Run not found"}},
        )

    async def event_generator():
        """Yield AG-UI events by subscribing to the event bus."""
        queue = await event_bus.subscribe()
        try:
            # Emit initial run_started based on current state
            async with async_session() as session:
                stmt = select(Run).where(Run.id == run_uuid)
                result = await session.execute(stmt)
                current_run = result.scalar_one_or_none()

            if current_run:
                status = _run_status_str(current_run)
                # If already running, emit run_started
                if status in ("running", "queued"):
                    yield _agui_event(EVENT_RUN_STARTED, {
                        "run_id": run_id,
                        "workflow": current_run.workflow_name,
                    })
                # If already terminal, emit the result immediately and return
                elif status in _TERMINAL_STATUSES:
                    for ev in _emit_terminal_event(
                        run_id, status, current_run
                    ):
                        yield ev
                    return

            # Stream events from the bus (with timeout)
            for _ in range(600):  # max ~10 minutes
                if await request.is_disconnected():
                    return

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Check if run is now terminal (DB poll)
                    async with async_session() as session:
                        stmt = select(Run).where(Run.id == run_uuid)
                        result = await session.execute(stmt)
                        current_run = result.scalar_one_or_none()

                    if current_run:
                        status = _run_status_str(current_run)
                        if status in _TERMINAL_STATUSES:
                            for ev in _emit_terminal_event(
                                run_id, status, current_run
                            ):
                                yield ev
                            return
                    continue

                # Transform and emit the event
                agui_event = _map_event_bus_event(event, run_id)
                if agui_event:
                    yield agui_event

                    # Check if this was a terminal event
                    event_type = event.get("type", "")
                    if event_type in ("run.completed", "run.failed"):
                        return

            # Timeout
            yield _agui_event(EVENT_RUN_ERROR, {
                "run_id": run_id,
                "error": "Stream timed out",
            })

        finally:
            await event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
