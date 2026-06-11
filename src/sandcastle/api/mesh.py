"""Sandcastle Mesh API: node registry, heartbeats and routed step execution.

Mounted at ``/api/mesh``. Three planes:

* ``POST /register`` / ``POST /heartbeat`` - node -> coordinator control
  plane, authenticated with the shared ``mesh_token`` (constant-time).
* ``POST /execute-step`` - coordinator -> node execution plane, same
  token. A node NEVER executes a step for an unauthenticated caller and
  everything 403s while ``mesh_enabled`` is off.
* ``GET /nodes`` - operator read, admin-gated via the regular API-key
  auth (it does not accept the mesh token).
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from sandcastle.api.schemas import ApiResponse, ErrorResponse
from sandcastle.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mesh", tags=["mesh"])

class MeshRegisterRequest(BaseModel):
    """Node self-registration payload."""

    name: str = Field(..., min_length=1, max_length=255)
    base_url: str = Field(..., min_length=1, max_length=2048)
    capabilities: list[str] = Field(default_factory=list)


class MeshHeartbeatRequest(BaseModel):
    """Node heartbeat payload."""

    node_id: str = Field(..., min_length=1, max_length=64)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ApiResponse(
            error=ErrorResponse(code=code, message=message)
        ).model_dump(),
    )


def require_mesh_token(request: Request) -> None:
    """Authenticate a mesh-plane request with the shared mesh token.

    Fails closed: mesh disabled -> 403; no token configured -> 403 (we
    never fall back to "open"); missing/wrong token -> 401. Comparison is
    constant-time.
    """
    if not settings.mesh_enabled:
        raise _error(403, "MESH_DISABLED", "Sandcastle Mesh is disabled (set MESH_ENABLED=true)")
    if not settings.mesh_token:
        raise _error(
            403, "MESH_TOKEN_UNSET",
            "MESH_TOKEN is not configured; refusing all mesh requests",
        )
    provided = request.headers.get("X-Mesh-Token", "")
    if not provided or not hmac.compare_digest(provided, settings.mesh_token):
        raise _error(401, "UNAUTHORIZED", "Invalid mesh token")


def _require_admin(request: Request) -> None:
    """Admin gate for operator reads (lazy import avoids a routes.py cycle)."""
    from sandcastle.api.auth import is_admin

    if not is_admin(request):
        raise _error(403, "FORBIDDEN", "Admin access required")


@router.get("/nodes")
async def get_nodes(request: Request) -> ApiResponse:
    """List registered mesh nodes with live health (admin-gated)."""
    _require_admin(request)
    from sandcastle.engine.mesh import get_local_capabilities, list_nodes

    nodes = await list_nodes()
    return ApiResponse(
        data={
            "enabled": settings.mesh_enabled,
            "heartbeat_seconds": settings.mesh_heartbeat_seconds,
            "local_capabilities": get_local_capabilities(),
            "nodes": nodes,
        }
    )


@router.post("/register")
async def register(request: Request, body: MeshRegisterRequest) -> ApiResponse:
    """Register (or refresh) a mesh node. Token-authed."""
    require_mesh_token(request)
    from sandcastle.engine.mesh import register_node

    base_url = body.base_url.strip()
    if not base_url.startswith(("http://", "https://")):
        raise _error(422, "VALIDATION_ERROR", "base_url must start with http:// or https://")
    node = await register_node(body.name.strip(), base_url, body.capabilities)
    return ApiResponse(data=node)


@router.post("/heartbeat")
async def heartbeat(request: Request, body: MeshHeartbeatRequest) -> ApiResponse:
    """Record a node heartbeat. Token-authed."""
    require_mesh_token(request)
    from sandcastle.engine.mesh import heartbeat_node

    node = await heartbeat_node(body.node_id)
    if node is None:
        raise _error(404, "NOT_FOUND", "Unknown mesh node; re-register with /api/mesh/register")
    return ApiResponse(data=node)


@router.post("/execute-step")
async def execute_step(request: Request, body: dict[str, Any]) -> ApiResponse:
    """Execute one routed step on this node. Token-authed, never anonymous."""
    require_mesh_token(request)
    from sandcastle.engine.mesh import execute_step_payload

    if not isinstance(body.get("step"), dict) or not body["step"].get("id"):
        raise _error(422, "VALIDATION_ERROR", "Payload must include a step object with an id")
    try:
        result = await execute_step_payload(body)
    except Exception as exc:  # noqa: BLE001 - surface as a failed step, not a 500
        logger.exception("Mesh step execution crashed")
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return ApiResponse(data=result)
