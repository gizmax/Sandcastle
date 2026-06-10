"""Sandcastle Mesh tests: registration, heartbeat/death, routing, auth, E2E.

The "E2E" tests run two in-process nodes by pointing the coordinator's
HTTP client at an ``httpx.ASGITransport`` wrapped around the same FastAPI
app - the full route -> POST /api/mesh/execute-step -> executor loop runs
with no real networking.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from sandcastle.config import settings
from sandcastle.engine import mesh
from sandcastle.engine.dag import StepDefinition, TransformConfig, parse_yaml_string
from sandcastle.engine.executor import RunContext
from sandcastle.main import app

client = TestClient(app)

MESH_TOKEN = "test-mesh-token-123"


@pytest.fixture
def mesh_on(monkeypatch):
    """Enable the mesh with a known token for the duration of a test."""
    monkeypatch.setattr(settings, "mesh_enabled", True)
    monkeypatch.setattr(settings, "mesh_token", MESH_TOKEN)
    monkeypatch.setattr(settings, "mesh_heartbeat_seconds", 15)
    yield


@pytest.fixture(autouse=True)
def _clean_mesh_nodes():
    """Each test starts with an empty node registry and no cached caps."""
    import asyncio

    async def _wipe():
        from sqlalchemy import delete

        from sandcastle.models.db import MeshNode, async_session

        async with async_session() as session:
            await session.execute(delete(MeshNode))
            await session.commit()

    asyncio.new_event_loop().run_until_complete(_wipe())
    mesh._LOCAL_CAPS = None
    yield
    mesh._LOCAL_CAPS = None


def _headers(token: str = MESH_TOKEN) -> dict:
    return {"X-Mesh-Token": token}


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_code_always_present(self):
        assert "code" in mesh.detect_local_capabilities()

    def test_spark_adds_gpu_and_spark(self, monkeypatch):
        from sandcastle.engine.spark import SparkInfo

        monkeypatch.setattr(
            "sandcastle.engine.spark.get_spark_info",
            lambda: SparkInfo(is_spark=True, gpu_name="GB10"),
        )
        caps = mesh.detect_local_capabilities()
        assert "gpu" in caps and "spark" in caps

    def test_browser_when_playwright_importable(self, monkeypatch):
        monkeypatch.setattr(mesh, "_playwright_importable", lambda: True)
        assert "browser" in mesh.detect_local_capabilities()

    def test_docker_when_socket_present(self, monkeypatch):
        monkeypatch.setattr(mesh, "_docker_socket_available", lambda: True)
        assert "docker" in mesh.detect_local_capabilities()


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


class TestLiveness:
    def test_alive_within_three_beats(self, mesh_on):
        now = datetime.now(timezone.utc)
        assert mesh.node_is_alive(now - timedelta(seconds=44), now) is True

    def test_dead_after_three_missed_beats(self, mesh_on):
        now = datetime.now(timezone.utc)
        assert mesh.node_is_alive(now - timedelta(seconds=46), now) is False

    def test_never_heartbeated_is_dead(self):
        assert mesh.node_is_alive(None) is False

    def test_naive_datetime_treated_as_utc(self, mesh_on):
        now = datetime.now(timezone.utc)
        naive = (now - timedelta(seconds=5)).replace(tzinfo=None)
        assert mesh.node_is_alive(naive, now) is True


# ---------------------------------------------------------------------------
# Registration + heartbeat API
# ---------------------------------------------------------------------------


class TestRegistrationApi:
    def test_register_and_list(self, mesh_on):
        resp = client.post(
            "/api/mesh/register",
            json={
                "name": "spark-1",
                "base_url": "http://spark.local:8080",
                "capabilities": ["GPU", "spark", "code"],
            },
            headers=_headers(),
        )
        assert resp.status_code == 200
        node = resp.json()["data"]
        assert node["name"] == "spark-1"
        assert node["status"] == "alive"
        assert sorted(node["capabilities"]) == ["code", "gpu", "spark"]  # lowercased

        listing = client.get("/api/mesh/nodes")
        assert listing.status_code == 200
        data = listing.json()["data"]
        assert data["enabled"] is True
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["id"] == node["id"]

    def test_register_upserts_on_base_url(self, mesh_on):
        body = {"name": "n1", "base_url": "http://node:8080", "capabilities": ["code"]}
        first = client.post("/api/mesh/register", json=body, headers=_headers())
        body2 = {"name": "n1-renamed", "base_url": "http://node:8080", "capabilities": ["browser"]}
        second = client.post("/api/mesh/register", json=body2, headers=_headers())
        assert first.json()["data"]["id"] == second.json()["data"]["id"]
        assert second.json()["data"]["capabilities"] == ["browser"]

    def test_register_rejects_non_http_url(self, mesh_on):
        resp = client.post(
            "/api/mesh/register",
            json={"name": "x", "base_url": "ftp://nope", "capabilities": []},
            headers=_headers(),
        )
        assert resp.status_code == 422

    def test_heartbeat_known_node(self, mesh_on):
        node = client.post(
            "/api/mesh/register",
            json={"name": "n", "base_url": "http://n:1", "capabilities": ["code"]},
            headers=_headers(),
        ).json()["data"]
        resp = client.post(
            "/api/mesh/heartbeat", json={"node_id": node["id"]}, headers=_headers()
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "alive"

    def test_heartbeat_unknown_node_404(self, mesh_on):
        resp = client.post(
            "/api/mesh/heartbeat",
            json={"node_id": "00000000-0000-0000-0000-000000000000"},
            headers=_headers(),
        )
        assert resp.status_code == 404

    async def test_node_goes_dead_without_heartbeats(self, mesh_on):
        from sqlalchemy import update

        from sandcastle.models.db import MeshNode, async_session

        node = await mesh.register_node("dying", "http://dying:1", ["code"])
        async with async_session() as session:
            await session.execute(
                update(MeshNode).values(
                    last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=100)
                )
            )
            await session.commit()
        nodes = await mesh.list_nodes()
        assert nodes[0]["id"] == node["id"]
        assert nodes[0]["status"] == "dead"


# ---------------------------------------------------------------------------
# Token auth (security: never execute for unauthenticated callers)
# ---------------------------------------------------------------------------


class TestMeshAuth:
    REGISTER_BODY = {"name": "x", "base_url": "http://x:1", "capabilities": []}

    def test_mesh_disabled_403(self, monkeypatch):
        monkeypatch.setattr(settings, "mesh_enabled", False)
        monkeypatch.setattr(settings, "mesh_token", MESH_TOKEN)
        resp = client.post("/api/mesh/register", json=self.REGISTER_BODY, headers=_headers())
        assert resp.status_code == 403

    def test_no_token_configured_403(self, monkeypatch):
        monkeypatch.setattr(settings, "mesh_enabled", True)
        monkeypatch.setattr(settings, "mesh_token", "")
        resp = client.post("/api/mesh/register", json=self.REGISTER_BODY, headers=_headers(""))
        assert resp.status_code == 403

    def test_wrong_token_401(self, mesh_on):
        resp = client.post(
            "/api/mesh/register", json=self.REGISTER_BODY, headers=_headers("wrong")
        )
        assert resp.status_code == 401

    def test_missing_token_401(self, mesh_on):
        resp = client.post("/api/mesh/register", json=self.REGISTER_BODY)
        assert resp.status_code == 401

    def test_execute_step_requires_token(self, mesh_on):
        resp = client.post(
            "/api/mesh/execute-step",
            json={"step": {"id": "s", "type": "transform"}},
            headers=_headers("nope"),
        )
        assert resp.status_code == 401

    def test_nodes_read_requires_api_key_when_auth_enabled(self, mesh_on, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        resp = client.get("/api/mesh/nodes")
        assert resp.status_code == 401  # admin reads stay behind regular auth


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _fake_nodes(*nodes: dict) -> list[dict]:
    base = {
        "id": "id",
        "base_url": "http://node:1",
        "capabilities": [],
        "status": "alive",
        "heartbeat_age_seconds": 1.0,
        "last_heartbeat": None,
        "registered_at": None,
    }
    return [{**base, **n} for n in nodes]


class TestRouting:
    async def test_no_requires_runs_local(self):
        step = StepDefinition(id="s", prompt="x")
        assert step.requires == []
        assert await mesh.resolve_route(step) is None

    async def test_local_preferred_when_it_qualifies(self, mesh_on, monkeypatch):
        monkeypatch.setattr(mesh, "get_local_capabilities", lambda: ["code", "gpu"])
        step = StepDefinition(id="s", prompt="x", requires=["gpu"])
        assert await mesh.resolve_route(step) is None

    async def test_picks_node_satisfying_all_capabilities(self, mesh_on, monkeypatch):
        monkeypatch.setattr(mesh, "get_local_capabilities", lambda: ["code"])

        async def fake_list():
            return _fake_nodes(
                {"id": "a", "name": "gpu-only", "capabilities": ["gpu"]},
                {"id": "b", "name": "gpu-browser", "capabilities": ["gpu", "browser"]},
            )

        monkeypatch.setattr(mesh, "list_nodes", fake_list)
        step = StepDefinition(id="s", prompt="x", requires=["gpu", "browser"])
        chosen = await mesh.resolve_route(step)
        assert chosen is not None and chosen["name"] == "gpu-browser"

    async def test_dead_nodes_skipped(self, mesh_on, monkeypatch):
        monkeypatch.setattr(mesh, "get_local_capabilities", lambda: ["code"])

        async def fake_list():
            return _fake_nodes(
                {"id": "a", "name": "dead-gpu", "capabilities": ["gpu"], "status": "dead"},
            )

        monkeypatch.setattr(mesh, "list_nodes", fake_list)
        step = StepDefinition(id="s", prompt="x", requires=["gpu"])
        with pytest.raises(mesh.MeshRoutingError) as exc:
            await mesh.resolve_route(step)
        # Error lists known nodes + their capabilities + the join command
        msg = str(exc.value)
        assert "dead-gpu" in msg and "gpu" in msg and "sandcastle node join" in msg

    async def test_unsatisfied_requires_clear_error(self, mesh_on, monkeypatch):
        monkeypatch.setattr(mesh, "get_local_capabilities", lambda: ["code"])

        async def fake_list():
            return []

        monkeypatch.setattr(mesh, "list_nodes", fake_list)
        step = StepDefinition(id="train", prompt="x", requires=["gpu"])
        with pytest.raises(mesh.MeshRoutingError) as exc:
            await mesh.resolve_route(step)
        msg = str(exc.value)
        assert "train" in msg and "['gpu']" in msg and "none" in msg

    async def test_mesh_disabled_but_requires_set(self, monkeypatch):
        monkeypatch.setattr(settings, "mesh_enabled", False)
        monkeypatch.setattr(mesh, "get_local_capabilities", lambda: ["code"])
        step = StepDefinition(id="s", prompt="x", requires=["gpu"])
        with pytest.raises(mesh.MeshRoutingError, match="mesh is disabled"):
            await mesh.resolve_route(step)

    async def test_control_flow_types_not_routable(self, mesh_on, monkeypatch):
        monkeypatch.setattr(mesh, "get_local_capabilities", lambda: ["code"])
        step = StepDefinition(id="s", prompt="x", type="approval", requires=["gpu"])
        with pytest.raises(mesh.MeshRoutingError, match="cannot be routed"):
            await mesh.resolve_route(step)


# ---------------------------------------------------------------------------
# YAML schema (backward compatible)
# ---------------------------------------------------------------------------


class TestRequiresYaml:
    def test_requires_parsed(self):
        wf = parse_yaml_string(
            """
name: mesh-test
steps:
  - id: train
    prompt: "train it"
    requires: [GPU, spark]
  - id: plain
    prompt: "no requires"
"""
        )
        assert wf.get_step("train").requires == ["gpu", "spark"]
        assert wf.get_step("plain").requires == []

    def test_requires_scalar_coerced(self):
        wf = parse_yaml_string(
            """
name: mesh-test
steps:
  - id: s
    prompt: "x"
    requires: gpu
"""
        )
        assert wf.get_step("s").requires == ["gpu"]


# ---------------------------------------------------------------------------
# Wire payload roundtrip
# ---------------------------------------------------------------------------


class TestPayloadRoundtrip:
    def test_transform_step_roundtrip(self):
        step = StepDefinition(
            id="t",
            type="transform",
            transform_config=TransformConfig(template="Hello {input.name}"),
            requires=["browser"],
            depends_on=["earlier"],
        )
        rebuilt = mesh.step_from_payload(mesh.step_to_payload(step))
        assert rebuilt.id == "t"
        assert rebuilt.type == "transform"
        assert rebuilt.transform_config.template == "Hello {input.name}"
        assert rebuilt.depends_on == ["earlier"]
        # requires is dropped on the wire so a node never re-routes
        assert rebuilt.requires == []

    def test_unknown_config_keys_ignored(self):
        payload = mesh.step_to_payload(StepDefinition(id="s", prompt="x"))
        payload["transform_config"] = {"template": "x", "future_field": True}
        rebuilt = mesh.step_from_payload(payload)
        assert rebuilt.transform_config.template == "x"


# ---------------------------------------------------------------------------
# E2E-ish: in-process nodes via ASGITransport (no real network)
# ---------------------------------------------------------------------------


class TestEndToEnd:
    @pytest.fixture
    def asgi_mesh(self, mesh_on, monkeypatch):
        """Route coordinator HTTP calls into the in-process app."""
        monkeypatch.setattr(mesh, "_TRANSPORT", httpx.ASGITransport(app=app))
        yield

    async def test_routed_step_executes_on_remote_node(self, asgi_mesh, monkeypatch):
        # Two registered "machines": a GPU box and a browser box.
        await mesh.register_node("spark", "http://spark-node", ["code", "gpu", "spark"])
        await mesh.register_node("mac", "http://mac-node", ["code", "browser"])
        monkeypatch.setattr(mesh, "get_local_capabilities", lambda: ["code"])

        step = StepDefinition(
            id="scrape",
            type="transform",
            transform_config=TransformConfig(template="Hello {input.name}"),
            requires=["browser"],
        )
        target = await mesh.resolve_route(step)
        assert target is not None and target["name"] == "mac"

        context = RunContext(run_id="run-1", input={"name": "Mesh"}, workflow_name="wf")
        result = await mesh.execute_step_remote(target, step, context)
        assert result.status == "completed", result.error
        assert result.output == "Hello Mesh"

    async def test_remote_node_sees_upstream_outputs(self, asgi_mesh, monkeypatch):
        await mesh.register_node("mac", "http://mac-node", ["browser"])
        monkeypatch.setattr(mesh, "get_local_capabilities", lambda: ["code"])

        step = StepDefinition(
            id="second",
            type="transform",
            transform_config=TransformConfig(template="got: {steps.first.output}"),
            depends_on=["first"],
            requires=["browser"],
        )
        target = await mesh.resolve_route(step)
        context = RunContext(
            run_id="run-2",
            input={},
            workflow_name="wf",
            step_outputs={"first": "upstream-value"},
        )
        result = await mesh.execute_step_remote(target, step, context)
        assert result.status == "completed", result.error
        assert result.output == "got: upstream-value"

    async def test_unreachable_node_fails_step_cleanly(self, mesh_on, monkeypatch):
        # No ASGI transport and a non-routable host: httpx raises -> failed StepResult
        monkeypatch.setattr(
            mesh, "_TRANSPORT",
            httpx.MockTransport(lambda request: (_ for _ in ()).throw(
                httpx.ConnectError("boom")
            )),
        )
        node = {"name": "ghost", "base_url": "http://ghost:9"}
        step = StepDefinition(id="s", prompt="x", type="transform",
                              transform_config=TransformConfig(template="t"))
        context = RunContext(run_id="r", input={}, workflow_name="wf")
        result = await mesh.execute_step_remote(node, step, context)
        assert result.status == "failed"
        assert "unreachable" in result.error

    def test_execute_step_endpoint_rejects_non_routable_type(self, mesh_on):
        resp = client.post(
            "/api/mesh/execute-step",
            json={"step": {"id": "a", "type": "approval"}},
            headers=_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "failed"
        assert "not executable" in data["error"]

    def test_execute_step_endpoint_validates_payload(self, mesh_on):
        resp = client.post("/api/mesh/execute-step", json={}, headers=_headers())
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Backward compat: workflows without `requires` use the unchanged local path
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    async def test_workflow_without_requires_never_touches_mesh(self, monkeypatch):
        from sandcastle.engine.dag import build_plan
        from sandcastle.engine.executor import execute_workflow

        def _boom(*a, **k):
            raise AssertionError("mesh routing must not run for requires-less steps")

        monkeypatch.setattr(mesh, "resolve_route", _boom)

        wf = parse_yaml_string(
            """
name: compat
steps:
  - id: t
    type: transform
    transform_config:
      template: "plain {input.x}"
"""
        )
        result = await execute_workflow(wf, build_plan(wf), {"x": "ok"})
        assert result.status == "completed"
        assert result.outputs["t"] == "plain ok"
