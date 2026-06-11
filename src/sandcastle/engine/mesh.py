"""Sandcastle Mesh: capability-based step routing across multiple machines.

Multiple machines (a DGX Spark, a Mac mini, a server, ...) form one
orchestration mesh. Each node registers a static capability manifest
("gpu", "spark", "browser", "docker", "code") with the coordinator and
heartbeats every ``mesh_heartbeat_seconds``. Steps that declare
``requires: [gpu, browser]`` in the workflow YAML are routed at dispatch
time to a live node that satisfies ALL required capabilities; the local
machine is preferred whenever it qualifies. Steps without ``requires``
run locally exactly as before.

Execution surface decision
--------------------------
Routed steps run on the node via ``POST /api/mesh/execute-step``, served
by the node's regular Sandcastle FastAPI app. The handler wraps the step
in a one-step :class:`WorkflowDefinition` and feeds it to the existing
:func:`sandcastle.engine.executor.execute_workflow` with
``initial_context`` (upstream step outputs) + ``skip_steps`` (upstream
ids). That reuses the ENTIRE existing executor - hybrid type dispatch,
retries, sandbox creation, policies, template resolution - with zero
duplicated execution logic. The alternative (the pull-based claim/
complete worker protocol from ``engine/self_hosted_worker.py``) targets
Anthropic's hosted work queue at api.anthropic.com; reusing it here
would have required building a brand-new server-side work queue with
claim/complete/reclaim endpoints on the coordinator - net-new code, not
reuse - so the push endpoint won.

Security: every mesh endpoint is authenticated with the shared
``mesh_token`` (constant-time comparison); a node never executes steps
for unauthenticated callers, and everything is off unless
``mesh_enabled`` is set on both sides.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from sandcastle.config import settings
from sandcastle.engine.dag import (
    BrowserConfig,
    CodeConfig,
    ExecutionPlan,
    HttpConfig,
    LlmConfig,
    NotifyConfig,
    ParseConfig,
    ReportConfig,
    RetryConfig,
    StepDefinition,
    ToolConfig,
    TransformConfig,
    WorkflowDefinition,
)

logger = logging.getLogger(__name__)

# A node is declared dead after this many missed heartbeats.
DEAD_AFTER_MISSED_BEATS = 3

# Step types that are safe to route to a remote node. Control-flow types
# (condition/classify/loop/race/gate), approval gates and sub-workflow
# spawns mutate run-local scheduler state and must stay on the coordinator.
ROUTABLE_STEP_TYPES = frozenset(
    {
        "standard", "llm", "http", "code", "transform", "parse",
        "browser", "tool", "notify", "report",
    }
)

# Nested config dataclasses we can round-trip through the wire payload.
# Keys are StepDefinition field names, values are the dataclass to rebuild.
_PAYLOAD_CONFIG_TYPES: dict[str, type] = {
    "retry": RetryConfig,
    "llm_config": LlmConfig,
    "http_config": HttpConfig,
    "code_config": CodeConfig,
    "tool_config": ToolConfig,
    "transform_config": TransformConfig,
    "notify_config": NotifyConfig,
    "browser_config": BrowserConfig,
    "parse_config": ParseConfig,
    "report_config": ReportConfig,
}

# Scalar/JSON StepDefinition fields copied verbatim into the wire payload.
_PAYLOAD_SCALAR_FIELDS = (
    "id", "prompt", "depends_on", "model", "max_turns", "timeout",
    "parallel_over", "output_schema", "type", "tools",
    "context_query", "context_source", "context_max_tokens",
    "output_max_tokens",
)


class MeshError(Exception):
    """Base class for mesh errors."""


class MeshRoutingError(MeshError):
    """No mesh node satisfies a step's `requires` capabilities."""


class MeshExecutionError(MeshError):
    """A routed step failed to execute on the remote node."""


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


def _docker_socket_available() -> bool:
    """True when a local Docker socket is present."""
    from pathlib import Path

    return Path("/var/run/docker.sock").exists() or Path(
        str(Path.home() / ".docker" / "run" / "docker.sock")
    ).exists()


def _playwright_importable() -> bool:
    """True when playwright can be imported (browser steps can run here)."""
    import importlib.util

    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def detect_local_capabilities() -> list[str]:
    """Auto-detect this machine's capability manifest.

    Every node can run plain code steps ("code"). On a detected DGX Spark
    the node also reports "gpu" + "spark"; playwright => "browser";
    a reachable Docker socket => "docker".
    """
    caps = ["code"]
    try:
        from sandcastle.engine.spark import get_spark_info

        if get_spark_info().is_spark:
            caps += ["gpu", "spark"]
    except Exception:  # noqa: BLE001 - detection must never break startup
        pass
    if _playwright_importable():
        caps.append("browser")
    if _docker_socket_available():
        caps.append("docker")
    return caps


# ---------------------------------------------------------------------------
# Node registry (DB-backed, matches codebase persistence style)
# ---------------------------------------------------------------------------


def _dead_after_seconds() -> float:
    return float(settings.mesh_heartbeat_seconds * DEAD_AFTER_MISSED_BEATS)


def node_is_alive(last_heartbeat: datetime | None, now: datetime | None = None) -> bool:
    """A node is alive when its last heartbeat is younger than 3 intervals."""
    if last_heartbeat is None:
        return False
    if last_heartbeat.tzinfo is None:
        last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - last_heartbeat).total_seconds() <= _dead_after_seconds()


def _node_to_dict(node: Any, now: datetime | None = None) -> dict[str, Any]:
    """Serialize a MeshNode row with live-computed status + heartbeat age."""
    now = now or datetime.now(timezone.utc)
    last = node.last_heartbeat
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    alive = node_is_alive(last, now)
    return {
        "id": str(node.id),
        "name": node.name,
        "base_url": node.base_url,
        "capabilities": list(node.capabilities or []),
        "last_heartbeat": last.isoformat() if last else None,
        "heartbeat_age_seconds": (
            round((now - last).total_seconds(), 1) if last else None
        ),
        "status": "alive" if alive else "dead",
        "registered_at": (
            node.registered_at.isoformat() if node.registered_at else None
        ),
    }


async def register_node(
    name: str, base_url: str, capabilities: list[str]
) -> dict[str, Any]:
    """Register (or re-register) a mesh node. Upserts on base_url."""
    from sqlalchemy import select

    from sandcastle.models.db import MeshNode, async_session

    base_url = base_url.rstrip("/")
    caps = sorted({str(c).strip().lower() for c in capabilities if str(c).strip()})
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        existing = await session.scalar(
            select(MeshNode).where(MeshNode.base_url == base_url)
        )
        if existing:
            existing.name = name
            existing.capabilities = caps
            existing.last_heartbeat = now
            existing.status = "alive"
            await session.commit()
            await session.refresh(existing)
            logger.info("Mesh node re-registered: %s (%s) caps=%s", name, base_url, caps)
            return _node_to_dict(existing, now)
        node = MeshNode(
            name=name,
            base_url=base_url,
            capabilities=caps,
            last_heartbeat=now,
            status="alive",
        )
        session.add(node)
        await session.commit()
        await session.refresh(node)
        logger.info("Mesh node registered: %s (%s) caps=%s", name, base_url, caps)
        return _node_to_dict(node, now)


async def heartbeat_node(node_id: str) -> dict[str, Any] | None:
    """Record a heartbeat. Returns the node dict, or None if unknown."""
    import uuid as _uuid

    from sandcastle.models.db import MeshNode, async_session

    try:
        nid = _uuid.UUID(node_id)
    except (ValueError, TypeError):
        return None
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        node = await session.get(MeshNode, nid)
        if node is None:
            return None
        node.last_heartbeat = now
        node.status = "alive"
        await session.commit()
        await session.refresh(node)
        return _node_to_dict(node, now)


async def list_nodes() -> list[dict[str, Any]]:
    """List all registered nodes with live-computed health."""
    from sqlalchemy import select

    from sandcastle.models.db import MeshNode, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as session:
        result = await session.execute(
            select(MeshNode).order_by(MeshNode.registered_at)
        )
        return [_node_to_dict(n, now) for n in result.scalars().all()]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

# Cached local manifest (capability probes are cheap but not free).
_LOCAL_CAPS: list[str] | None = None


def get_local_capabilities() -> list[str]:
    """Cached :func:`detect_local_capabilities` for the process lifetime."""
    global _LOCAL_CAPS
    if _LOCAL_CAPS is None:
        _LOCAL_CAPS = detect_local_capabilities()
    return _LOCAL_CAPS


def _satisfies(capabilities: list[str], requires: list[str]) -> bool:
    return set(requires).issubset(set(capabilities))


async def resolve_route(step: StepDefinition) -> dict[str, Any] | None:
    """Decide where a step with ``requires`` runs.

    Returns None to run locally (local manifest satisfies all required
    capabilities - local is always preferred), or the chosen node dict.
    Raises :class:`MeshRoutingError` with the full picture (known nodes +
    capabilities) when nothing qualifies.
    """
    requires = [c.strip().lower() for c in step.requires if c.strip()]
    if not requires:
        return None

    if step.type not in ROUTABLE_STEP_TYPES:
        raise MeshRoutingError(
            f"Step '{step.id}' (type={step.type}) declares requires={requires} "
            f"but this step type cannot be routed to a mesh node. Routable "
            f"types: {sorted(ROUTABLE_STEP_TYPES)}."
        )

    # Prefer local whenever the local manifest qualifies.
    local_caps = get_local_capabilities()
    if _satisfies(local_caps, requires):
        logger.info(
            "Mesh: step '%s' requires=%s satisfied locally (caps=%s)",
            step.id, requires, local_caps,
        )
        return None

    nodes = await list_nodes() if settings.mesh_enabled else []
    candidates = [
        n for n in nodes
        if n["status"] == "alive" and _satisfies(n["capabilities"], requires)
    ]
    if candidates:
        chosen = candidates[0]
        logger.info(
            "Mesh: step '%s' requires=%s routed to node '%s' (%s)",
            step.id, requires, chosen["name"], chosen["base_url"],
        )
        return chosen

    known = ", ".join(
        f"{n['name']}[{n['status']}: {', '.join(n['capabilities']) or '-'}]"
        for n in nodes
    ) or "none"
    mesh_state = "" if settings.mesh_enabled else " (mesh is disabled: set MESH_ENABLED=true)"
    raise MeshRoutingError(
        f"Step '{step.id}' requires capabilities {requires} but no live mesh "
        f"node satisfies them{mesh_state}. Local capabilities: {local_caps}. "
        f"Known nodes: {known}. Join a capable node with: "
        f"sandcastle node join <coordinator-url> --token <mesh-token>"
    )


# ---------------------------------------------------------------------------
# Wire format: StepDefinition <-> JSON payload
# ---------------------------------------------------------------------------


def step_to_payload(step: StepDefinition) -> dict[str, Any]:
    """Serialize a routable step for the /api/mesh/execute-step wire.

    ``requires`` is intentionally dropped so the receiving node never
    re-routes (no forwarding loops).
    """
    payload: dict[str, Any] = {}
    for name in _PAYLOAD_SCALAR_FIELDS:
        payload[name] = getattr(step, name)
    for name in _PAYLOAD_CONFIG_TYPES:
        value = getattr(step, name)
        payload[name] = dataclasses.asdict(value) if value is not None else None
    return payload


def _rebuild_config(cls: type, data: dict[str, Any] | None) -> Any:
    """Rebuild a config dataclass from a dict, ignoring unknown keys."""
    if data is None:
        return None
    field_names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in field_names})


def step_from_payload(payload: dict[str, Any]) -> StepDefinition:
    """Reconstruct a StepDefinition from its wire payload."""
    kwargs: dict[str, Any] = {
        name: payload[name] for name in _PAYLOAD_SCALAR_FIELDS if name in payload
    }
    for name, cls in _PAYLOAD_CONFIG_TYPES.items():
        kwargs[name] = _rebuild_config(cls, payload.get(name))
    kwargs["depends_on"] = [str(d) for d in (kwargs.get("depends_on") or [])]
    return StepDefinition(**kwargs)


# ---------------------------------------------------------------------------
# Remote execution (coordinator side)
# ---------------------------------------------------------------------------

# Test seam: tests monkeypatch this to an httpx.ASGITransport pointed at an
# in-process "node" app, so the full route->execute->result loop runs without
# real networking.
_TRANSPORT: httpx.AsyncBaseTransport | None = None


async def execute_step_remote(node: dict[str, Any], step: Any, context: Any) -> Any:
    """Execute a step on a remote mesh node and return a StepResult."""
    from sandcastle.engine.executor import StepResult

    body = {
        "run_id": context.run_id,
        "workflow_name": context.workflow_name,
        "node_name": node.get("name", ""),
        "step": step_to_payload(step),
        "input": context.input,
        "step_outputs": dict(context.step_outputs),
        "default_tools": list(context.default_tools or []),
    }
    url = f"{node['base_url'].rstrip('/')}/api/mesh/execute-step"
    # Generous network budget on top of the step's own timeout + retries.
    max_attempts = step.retry.max_attempts if step.retry else 1
    http_timeout = (step.timeout + 60) * max(1, max_attempts)
    try:
        async with httpx.AsyncClient(transport=_TRANSPORT, timeout=http_timeout) as client:
            resp = await client.post(
                url, json=body, headers={"X-Mesh-Token": settings.mesh_token}
            )
    except httpx.HTTPError as exc:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=(
                f"Mesh node '{node.get('name')}' unreachable at {url}: "
                f"{type(exc).__name__}: {exc}"
            ),
        )
    if resp.status_code != 200:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=(
                f"Mesh node '{node.get('name')}' rejected step '{step.id}': "
                f"HTTP {resp.status_code} {resp.text[:500]}"
            ),
        )
    data = resp.json().get("data") or {}
    return StepResult(
        step_id=step.id,
        output=data.get("output"),
        cost_usd=float(data.get("cost_usd") or 0.0),
        duration_seconds=float(data.get("duration_seconds") or 0.0),
        status=data.get("status", "failed"),
        error=data.get("error"),
    )


# ---------------------------------------------------------------------------
# Node-side execution of a routed step
# ---------------------------------------------------------------------------


async def execute_step_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Run one routed step on this node and return a result dict.

    Wraps the step in a one-step WorkflowDefinition and reuses the full
    existing executor: upstream outputs arrive via ``initial_context`` and
    the upstream step ids are passed as ``skip_steps`` so template
    references like ``{steps.x.output}`` resolve exactly as they would
    have on the coordinator.
    """
    import uuid as _uuid

    from sandcastle.engine.executor import execute_workflow

    step = step_from_payload(body.get("step") or {})
    if step.type not in ROUTABLE_STEP_TYPES:
        return {
            "status": "failed",
            "error": f"Step type '{step.type}' is not executable via the mesh",
        }

    workflow = WorkflowDefinition(
        name=f"mesh:{body.get('workflow_name', 'remote')}",
        description="Sandcastle Mesh routed step",
        default_model=step.model,
        default_max_turns=step.max_turns,
        default_timeout=step.timeout,
        steps=[step],
        default_tools=list(body.get("default_tools") or []),
    )
    plan = ExecutionPlan(stages=[[step.id]])
    step_outputs = dict(body.get("step_outputs") or {})

    result = await execute_workflow(
        workflow=workflow,
        plan=plan,
        input_data=dict(body.get("input") or {}),
        run_id=str(_uuid.uuid4()),  # node-local run id (RunStep bookkeeping)
        initial_context={"step_outputs": step_outputs, "costs": []},
        skip_steps=set(step.depends_on),
    )

    output = result.outputs.get(step.id)
    failed = result.status != "completed"
    return {
        "status": "failed" if failed else "completed",
        "output": output,
        "cost_usd": result.total_cost_usd,
        "duration_seconds": (
            (result.completed_at - result.started_at).total_seconds()
            if result.completed_at and result.started_at
            else 0.0
        ),
        "error": result.error,
    }
