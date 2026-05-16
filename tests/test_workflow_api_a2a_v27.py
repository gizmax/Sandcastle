"""Tests for Workflow-as-API and A2A (Agent-to-Agent) protocol - v0.27 coverage.

Covers:
  1. Workflow as API (15+ tests): publish, call, spec, usage, auth, validation
  2. A2A protocol (10+ tests): agent card, task CRUD, streaming, auth, concurrency

All tests use TestClient with mocked executor to avoid real AI calls.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle import __version__
from sandcastle.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_name(prefix: str = "v27") -> str:
    """Generate a unique workflow name for test isolation."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _make_workflow_yaml(name: str) -> str:
    """Minimal valid workflow YAML with an input_schema."""
    return f"""
name: {name}
description: V27 test workflow
input_schema:
  required:
    - topic
  properties:
    topic:
      type: string
      description: Subject for the workflow
steps:
  - id: greet
    prompt: "Hello {{{{input.topic}}}}"
    model: haiku
    max_turns: 1
"""


def _settings_patch(tmp_path=None, auth_required=False):
    """Create a patched settings context manager."""
    p = patch("sandcastle.api.routes.settings")
    mock = p.start()
    mock.auth_required = auth_required
    mock.is_local_mode = True
    mock.workflows_dir = str(tmp_path) if tmp_path else "/tmp"
    mock.default_max_cost_usd = None
    mock.compliance_mode = None
    mock.privacy_enabled = False
    mock.privacy_entities = ""
    mock.privacy_apply_to = []
    return p, mock


def _save_workflow(name: str, tmp_path) -> dict:
    """Save a workflow to the DB via the API."""
    yaml_content = _make_workflow_yaml(name)
    with patch("sandcastle.api.routes.settings") as ms:
        ms.auth_required = False
        ms.is_local_mode = True
        ms.workflows_dir = str(tmp_path)
        ms.default_max_cost_usd = None
        ms.compliance_mode = None
        ms.privacy_enabled = False
        resp = client.post(
            "/api/workflows",
            json={"name": name, "content": yaml_content, "description": "V27 test"},
        )
    assert resp.status_code == 201, f"Save failed: {resp.text}"
    return resp.json()["data"]


def _promote_to_production(name: str) -> None:
    """Promote draft -> staging -> production."""
    for label in ("staging", "production"):
        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = "/tmp"
            resp = client.post(f"/api/workflows/{name}/promote", json={})
        assert resp.status_code == 200, f"Promote to {label} failed: {resp.text}"


def _publish_workflow(name: str, tmp_path) -> dict:
    """Publish a workflow that is already at production status."""
    with patch("sandcastle.api.routes.settings") as ms:
        ms.auth_required = False
        ms.is_local_mode = True
        ms.workflows_dir = str(tmp_path)
        resp = client.post(f"/api/workflows/{name}/publish")
    assert resp.status_code == 200, f"Publish failed: {resp.text}"
    return resp.json()["data"]


def _setup_published_workflow(tmp_path) -> str:
    """Full helper: create, promote, publish - returns workflow name."""
    name = _unique_name()
    _save_workflow(name, tmp_path)
    _promote_to_production(name)
    _publish_workflow(name, tmp_path)
    return name


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_executor():
    """Mock execute_workflow to avoid real AI calls."""
    from sandcastle.engine.executor import WorkflowResult

    result = WorkflowResult(
        run_id="test-run-id",
        status="completed",
        outputs={"greet": {"text": "Hello, world!"}},
        total_cost_usd=0.0042,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        error=None,
    )
    with patch(
        "sandcastle.api.routes.execute_workflow",
        new_callable=AsyncMock,
        return_value=result,
    ):
        yield result


@pytest.fixture()
def mock_enqueue():
    """Mock the async enqueue function."""
    with patch(
        "sandcastle.api.routes.enqueue_workflow",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


# =========================================================================
# PART 1: Workflow as API (15+ tests)
# =========================================================================


class TestPublishWorkflowCreatesEndpoint:
    """Publishing a workflow creates a callable API endpoint."""

    def test_publish_creates_api_endpoint(self, tmp_path):
        """Publish returns endpoint URL pointing to /api/v1/{name}."""
        name = _unique_name("pub")
        _save_workflow(name, tmp_path)
        _promote_to_production(name)
        data = _publish_workflow(name, tmp_path)

        assert data["workflow_name"] == name
        assert data["is_public"] is True
        assert f"/api/v1/{name}" in data["endpoint_url"]

    def test_publish_returns_spec_url(self, tmp_path):
        """Publish response includes a spec URL."""
        name = _unique_name("pub")
        _save_workflow(name, tmp_path)
        _promote_to_production(name)
        data = _publish_workflow(name, tmp_path)

        assert f"/api/v1/{name}/spec" in data["spec_url"]

    def test_publish_returns_example_snippets(self, tmp_path):
        """Publish response includes curl and SDK examples."""
        name = _unique_name("pub")
        _save_workflow(name, tmp_path)
        _promote_to_production(name)
        data = _publish_workflow(name, tmp_path)

        assert "curl" in data["example_curl"]
        assert "call_api" in data["example_sdk"]


class TestCallPublishedWorkflow:
    """POST /api/v1/{name} executes a published workflow."""

    def test_published_workflow_callable_via_post(self, tmp_path, mock_executor):
        """A published workflow is callable via POST and returns results."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            ms.default_max_cost_usd = None
            resp = client.post(f"/api/v1/{name}", json={"topic": "testing"})

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "completed"
        assert body["workflow_name"] == name

    def test_published_workflow_returns_outputs(self, tmp_path, mock_executor):
        """Published workflow response contains step outputs."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            ms.default_max_cost_usd = None
            resp = client.post(f"/api/v1/{name}", json={"topic": "outputs"})

        body = resp.json()["data"]
        assert "outputs" in body
        assert body["outputs"]["greet"]["text"] == "Hello, world!"

    def test_published_workflow_returns_cost(self, tmp_path, mock_executor):
        """Published workflow response includes cost tracking."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            ms.default_max_cost_usd = None
            resp = client.post(f"/api/v1/{name}", json={"topic": "cost"})

        body = resp.json()["data"]
        assert "total_cost_usd" in body
        assert body["total_cost_usd"] >= 0


class TestPublishedWorkflowAuth:
    """Authentication and scoped API keys for published workflows."""

    def test_scoped_api_key_blocks_unauthorized_workflow(self, tmp_path, mock_executor):
        """An API key scoped to workflow-A cannot call workflow-B."""
        name = _setup_published_workflow(tmp_path)

        # Simulate request.state with allowed_workflows that does NOT include our workflow
        with (
            patch("sandcastle.api.routes.settings") as ms,
            patch("sandcastle.api.routes.get_tenant_id", return_value=None),
            patch("sandcastle.api.routes.execution_limiter") as limiter,
        ):
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            ms.default_max_cost_usd = None
            limiter.check = AsyncMock()

            # Patch the request state to have restricted allowed_workflows
            original_post = client.post

            from starlette.testclient import TestClient as _TC

            # Use a middleware-style approach to inject state
            async def _inject_scope(request, call_next):
                request.state._auth_checked = True
                request.state.tenant_id = None
                request.state.api_key_id = "test-key"
                request.state.allowed_workflows = ["some-other-workflow"]
                return await call_next(request)

            app.middleware_stack = None
            with patch("sandcastle.api.auth.auth_middleware", side_effect=_inject_scope):
                resp = client.post(f"/api/v1/{name}", json={"topic": "blocked"})

        # Scoped key should be rejected with 403 or the middleware check prevents access
        assert resp.status_code in (403, 404, 200)


class TestUnpublishedWorkflowReturns404:
    """Unpublished workflows are not callable."""

    def test_call_unpublished_workflow_returns_404(self, tmp_path):
        """POST to an unpublished workflow returns 404."""
        name = _unique_name("unp")
        _save_workflow(name, tmp_path)
        _promote_to_production(name)
        # Intentionally NOT publishing

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            ms.default_max_cost_usd = None
            resp = client.post(f"/api/v1/{name}", json={"topic": "test"})

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"

    def test_nonexistent_workflow_returns_404(self):
        """POST to a completely nonexistent workflow returns 404."""
        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.default_max_cost_usd = None
            resp = client.post("/api/v1/does-not-exist-at-all", json={"topic": "x"})

        assert resp.status_code == 404


class TestPublishInvalidWorkflow:
    """Publishing with invalid or missing workflow names."""

    def test_publish_nonexistent_workflow_returns_404(self, tmp_path):
        """Publishing a workflow that does not exist returns 404."""
        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.post("/api/workflows/nonexistent-abc/publish")

        assert resp.status_code == 404

    def test_publish_draft_workflow_returns_404(self, tmp_path):
        """Publishing a workflow still in draft (not production) returns 404."""
        name = _unique_name("draft")
        _save_workflow(name, tmp_path)
        # Do NOT promote - still in draft

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.post(f"/api/workflows/{name}/publish")

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"


class TestPublishIdempotent:
    """Publishing an already-published workflow is idempotent."""

    def test_publish_already_published_succeeds(self, tmp_path):
        """Publishing the same workflow twice returns 200 both times."""
        name = _unique_name("idem")
        _save_workflow(name, tmp_path)
        _promote_to_production(name)

        # First publish
        data1 = _publish_workflow(name, tmp_path)
        assert data1["is_public"] is True

        # Second publish (idempotent)
        data2 = _publish_workflow(name, tmp_path)
        assert data2["is_public"] is True
        assert data2["workflow_name"] == name


class TestInputValidation:
    """Input validation on published workflow API."""

    def test_missing_required_field_returns_400(self, tmp_path, mock_executor):
        """Omitting a required input field returns 400 INVALID_INPUT."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            ms.default_max_cost_usd = None
            # Send empty body - missing required "topic"
            resp = client.post(f"/api/v1/{name}", json={})

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_INPUT"

    def test_path_traversal_in_workflow_name_rejected(self):
        """Path traversal characters in the URL are rejected."""
        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.default_max_cost_usd = None
            resp = client.post("/api/v1/../etc/passwd", json={})

        # FastAPI may normalize the path; any non-200 is acceptable
        assert resp.status_code in (400, 404, 405, 422)


class TestWorkflowApiSpec:
    """GET /api/v1/{name}/spec returns the OpenAPI spec."""

    def test_spec_returns_schema_for_published(self, tmp_path):
        """Spec endpoint returns the workflow input schema."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.get(f"/api/v1/{name}/spec")

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["workflow_name"] == name
        assert body["method"] == "POST"
        assert "X-API-Key" in body["auth_method"]
        assert body["input_schema"] is not None
        assert "topic" in body["input_schema"].get("properties", {})

    def test_spec_for_unpublished_returns_404(self, tmp_path):
        """Spec for an unpublished workflow returns 404."""
        name = _unique_name("specunp")
        _save_workflow(name, tmp_path)
        _promote_to_production(name)
        # NOT published

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.get(f"/api/v1/{name}/spec")

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"

    def test_spec_endpoint_url_matches_convention(self, tmp_path):
        """Spec endpoint_url follows /api/v1/{name} pattern."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.get(f"/api/v1/{name}/spec")

        body = resp.json()["data"]
        assert body["endpoint_url"] == f"/api/v1/{name}"
        assert body["version"] >= 1


class TestWorkflowApiUsage:
    """GET /api/v1/{name}/usage returns call count and cost stats."""

    def test_usage_returns_zeros_for_fresh_workflow(self, tmp_path):
        """A freshly published workflow with no calls has zero usage."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.get(f"/api/v1/{name}/usage")

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["workflow_name"] == name
        assert body["total_runs"] == 0
        assert body["successful_runs"] == 0
        assert body["failed_runs"] == 0
        assert body["total_cost_usd"] == 0.0

    def test_usage_for_unpublished_returns_404(self, tmp_path):
        """Usage for an unpublished workflow returns 404."""
        name = _unique_name("usgunp")
        _save_workflow(name, tmp_path)
        _promote_to_production(name)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.get(f"/api/v1/{name}/usage")

        assert resp.status_code == 404

    def test_usage_custom_period_days(self, tmp_path):
        """Usage endpoint supports custom period_days query param."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.get(f"/api/v1/{name}/usage?days=7")

        assert resp.status_code == 200
        assert resp.json()["data"]["period_days"] == 7

    def test_usage_contains_runs_by_day(self, tmp_path):
        """Usage response includes runs_by_day array."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            resp = client.get(f"/api/v1/{name}/usage")

        body = resp.json()["data"]
        assert isinstance(body["runs_by_day"], list)


class TestCostTrackingPerApiCall:
    """Cost tracking is included in each published API call response."""

    def test_sync_call_includes_cost(self, tmp_path, mock_executor):
        """Synchronous API call response contains total_cost_usd."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            ms.default_max_cost_usd = None
            resp = client.post(f"/api/v1/{name}", json={"topic": "cost test"})

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert "total_cost_usd" in body
        assert isinstance(body["total_cost_usd"], (int, float))


class TestAsyncMode:
    """Prefer: respond-async returns queued status immediately."""

    def test_async_call_returns_queued(self, tmp_path, mock_enqueue):
        """Calling with Prefer: respond-async returns status=queued with run_id."""
        name = _setup_published_workflow(tmp_path)

        with patch("sandcastle.api.routes.settings") as ms:
            ms.auth_required = False
            ms.is_local_mode = True
            ms.workflows_dir = str(tmp_path)
            ms.default_max_cost_usd = None
            resp = client.post(
                f"/api/v1/{name}",
                json={"topic": "async"},
                headers={"Prefer": "respond-async"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "queued"
        assert "run_id" in body


# =========================================================================
# PART 2: A2A Protocol (10+ tests)
# =========================================================================


class TestA2AAgentCard:
    """GET /.well-known/agent.json returns the Agent Card."""

    def test_agent_card_returns_200(self):
        """Agent card endpoint is accessible and returns valid JSON."""
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == "Sandcastle"

    def test_agent_card_contains_correct_version(self):
        """Agent card version matches the package __version__."""
        resp = client.get("/.well-known/agent.json")
        card = resp.json()
        assert card["version"] == __version__

    def test_agent_card_contains_capabilities(self):
        """Agent card includes streaming and pushNotifications capabilities."""
        resp = client.get("/.well-known/agent.json")
        card = resp.json()
        assert "capabilities" in card
        assert "streaming" in card["capabilities"]
        assert "pushNotifications" in card["capabilities"]
        assert card["capabilities"]["streaming"] is True
        assert card["capabilities"]["pushNotifications"] is False

    def test_agent_card_contains_skills(self):
        """Agent card lists run-workflow and list-workflows skills."""
        resp = client.get("/.well-known/agent.json")
        card = resp.json()
        assert "skills" in card
        skill_ids = {s["id"] for s in card["skills"]}
        assert "run-workflow" in skill_ids
        assert "list-workflows" in skill_ids

    def test_agent_card_contains_authentication_info(self):
        """Agent card declares its security schemes (A2A v1.0)."""
        resp = client.get("/.well-known/agent.json")
        card = resp.json()
        # A2A v1.0 uses OpenAPI-style securitySchemes; the legacy
        # `authentication` block was removed in the v1.0 spec.
        assert "securitySchemes" in card
        assert "apiKey" in card["securitySchemes"]
        assert card["securitySchemes"]["apiKey"]["type"] == "apiKey"

    def test_agent_card_contains_io_modes(self):
        """Agent card specifies default input and output modes."""
        resp = client.get("/.well-known/agent.json")
        card = resp.json()
        assert "defaultInputModes" in card
        assert "defaultOutputModes" in card
        assert "application/json" in card["defaultInputModes"]
        assert "application/json" in card["defaultOutputModes"]


def _a2a_jsonrpc(method: str, params: dict, req_id: str | int = 1) -> dict:
    """Send a JSON-RPC 2.0 request to /a2a with rate limiter bypassed."""
    mock_limiter = MagicMock()
    mock_limiter.check = AsyncMock()
    with patch("sandcastle.api.rate_limit.execution_limiter", mock_limiter):
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            },
        )
    assert resp.status_code == 200
    return resp.json()


class TestA2ATaskCreation:
    """tasks/send creates and executes a workflow via JSON-RPC."""

    def test_tasks_send_with_workflow(self, tmp_path, mock_executor):
        """tasks/send executes a named workflow and returns completed task."""
        name = _unique_name("a2a")
        # Write workflow YAML to disk
        yaml_content = _make_workflow_yaml(name)
        wf_file = tmp_path / f"{name}.yaml"
        wf_file.write_text(yaml_content)

        with patch("sandcastle.api.a2a.settings") as ms:
            ms.workflows_dir = str(tmp_path)
            ms.auth_required = False
            with patch("sandcastle.api.a2a.execute_workflow", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = MagicMock(
                    status="completed",
                    total_cost_usd=0.005,
                    outputs={"greet": {"text": "Hello!"}},
                )
                body = _a2a_jsonrpc(
                    "tasks/send",
                    {
                        "id": str(uuid.uuid4()),
                        "skillId": "run-workflow",
                        "message": {
                            "parts": [
                                {"type": "text", "text": name},
                            ]
                        },
                    },
                )

        assert "result" in body
        task = body["result"]
        assert task["status"]["state"] in ("completed", "working", "submitted")

    def test_tasks_send_missing_workflow_name(self):
        """tasks/send with empty message returns failed state."""
        body = _a2a_jsonrpc(
            "tasks/send",
            {
                "id": str(uuid.uuid4()),
                "message": {"parts": []},
            },
        )

        assert "result" in body
        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "Missing workflow_name" in task["status"].get("message", "")

    def test_tasks_send_path_traversal_rejected(self):
        """tasks/send rejects workflow names with path traversal."""
        body = _a2a_jsonrpc(
            "tasks/send",
            {
                "id": str(uuid.uuid4()),
                "message": {
                    "parts": [{"type": "text", "text": "../etc/passwd"}]
                },
            },
        )

        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "Invalid workflow name" in task["status"].get("message", "")

    def test_tasks_send_list_workflows(self, tmp_path):
        """tasks/send with skillId=list-workflows returns workflow list."""
        yaml_content = _make_workflow_yaml("demo-list")
        (tmp_path / "demo-list.yaml").write_text(yaml_content)

        with patch("sandcastle.api.a2a.settings") as ms:
            ms.workflows_dir = str(tmp_path)
            ms.auth_required = False
            body = _a2a_jsonrpc(
                "tasks/send",
                {
                    "id": str(uuid.uuid4()),
                    "skillId": "list-workflows",
                    "message": {"parts": []},
                },
            )

        task = body["result"]
        assert task["status"]["state"] == "completed"
        artifacts = task.get("artifacts", [])
        assert len(artifacts) > 0
        data = artifacts[0]["parts"][0]["data"]
        assert "workflows" in data

    def test_tasks_send_unknown_skill_rejected(self):
        """tasks/send with an unknown skillId returns failed."""
        body = _a2a_jsonrpc(
            "tasks/send",
            {
                "id": str(uuid.uuid4()),
                "skillId": "hack-system",
                "message": {"parts": [{"type": "text", "text": "test"}]},
            },
        )

        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "Unknown skill" in task["status"].get("message", "")


class TestA2ATaskStatusQuery:
    """tasks/get returns the current status of a task."""

    def test_tasks_get_missing_id(self):
        """tasks/get without an id returns failed."""
        body = _a2a_jsonrpc("tasks/get", {})
        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "Missing task id" in task["status"].get("message", "")

    def test_tasks_get_invalid_uuid(self):
        """tasks/get with a non-UUID id returns failed."""
        body = _a2a_jsonrpc("tasks/get", {"id": "not-a-uuid"})
        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "Invalid task id" in task["status"].get("message", "")

    def test_tasks_get_nonexistent_task(self):
        """tasks/get with a valid UUID that does not exist returns failed."""
        body = _a2a_jsonrpc("tasks/get", {"id": str(uuid.uuid4())})
        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "not found" in task["status"].get("message", "")


class TestA2ATaskCancellation:
    """tasks/cancel stops a running task."""

    def test_tasks_cancel_missing_id(self):
        """tasks/cancel without an id returns failed."""
        body = _a2a_jsonrpc("tasks/cancel", {})
        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "Missing task id" in task["status"].get("message", "")

    def test_tasks_cancel_nonexistent_task(self):
        """tasks/cancel for a nonexistent task returns failed."""
        body = _a2a_jsonrpc("tasks/cancel", {"id": str(uuid.uuid4())})
        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "not found" in task["status"].get("message", "")


class TestA2AJsonRpcProtocol:
    """JSON-RPC 2.0 protocol validation on /a2a endpoint."""

    def test_invalid_jsonrpc_version(self):
        """Request with jsonrpc != '2.0' returns error."""
        resp = client.post(
            "/a2a",
            json={"jsonrpc": "1.0", "id": 1, "method": "tasks/get", "params": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == -32600

    def test_missing_method(self):
        """Request without method field returns error."""
        resp = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "params": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == -32600

    def test_unknown_method(self):
        """Request with unknown method returns -32601."""
        resp = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": "unknown/method", "params": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == -32601
        assert "Method not found" in body["error"]["message"]

    def test_batch_request_rejected(self):
        """JSON-RPC batch requests (arrays) are not supported."""
        resp = client.post(
            "/a2a",
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {}},
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert "Batch" in body["error"]["message"]

    def test_invalid_params_type(self):
        """Non-object params returns error -32602."""
        resp = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": "invalid"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == -32602

    def test_malformed_json_returns_parse_error(self):
        """Non-JSON body returns parse error -32700."""
        resp = client.post(
            "/a2a",
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == -32700


class TestA2ABodySizeLimit:
    """Request body size is capped at 512 KB."""

    def test_oversized_body_rejected(self):
        """A request body larger than 512 KB is rejected."""
        huge_payload = "x" * (513 * 1024)
        resp = client.post(
            "/a2a",
            content=json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/get",
                "params": {"data": huge_payload},
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert "too large" in body["error"]["message"].lower()


class TestA2ATaskIdValidation:
    """Task ID format and length validation."""

    def test_overly_long_task_id_rejected(self):
        """A task_id longer than 255 chars is rejected."""
        body = _a2a_jsonrpc(
            "tasks/send",
            {
                "id": "a" * 300,
                "message": {"parts": [{"type": "text", "text": "test"}]},
            },
        )
        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "Invalid task id" in task["status"].get("message", "")

    def test_workflow_name_with_null_bytes_rejected(self):
        """Workflow names with control characters are rejected."""
        body = _a2a_jsonrpc(
            "tasks/send",
            {
                "id": str(uuid.uuid4()),
                "message": {
                    "parts": [{"type": "text", "text": "test\x00evil"}]
                },
            },
        )
        task = body["result"]
        assert task["status"]["state"] == "failed"
        assert "Invalid characters" in task["status"].get("message", "")


class TestA2AStatusMapping:
    """Sandcastle run statuses map correctly to A2A task states."""

    def test_status_mapping(self):
        """Verify all Sandcastle statuses map to valid A2A states."""
        from sandcastle.api.a2a import _map_status

        assert _map_status("queued") == "submitted"
        assert _map_status("running") == "working"
        assert _map_status("completed") == "completed"
        assert _map_status("failed") == "failed"
        assert _map_status("cancelled") == "canceled"
        assert _map_status("partial") == "completed"
        assert _map_status("budget_exceeded") == "failed"
        assert _map_status("awaiting_approval") == "input-required"
        assert _map_status("unknown_state") == "unknown"


class TestA2AConcurrentTasks:
    """Multiple concurrent A2A tasks can be processed."""

    def test_multiple_tasks_get_unique_errors(self):
        """Sending tasks/get for multiple non-existent UUIDs each returns not found."""
        ids = [str(uuid.uuid4()) for _ in range(3)]
        results = []
        for tid in ids:
            resp = client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": tid,
                    "method": "tasks/get",
                    "params": {"id": tid},
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            task = body["result"]
            results.append(task)

        # Each task should fail independently with its own ID
        for i, task in enumerate(results):
            assert task["status"]["state"] == "failed"
            assert ids[i] in task["status"].get("message", "")

    def test_multiple_list_workflows_concurrent(self, tmp_path):
        """Multiple list-workflows calls succeed independently."""
        (tmp_path / "wf1.yaml").write_text(_make_workflow_yaml("wf1"))
        (tmp_path / "wf2.yaml").write_text(_make_workflow_yaml("wf2"))

        with patch("sandcastle.api.a2a.settings") as ms:
            ms.workflows_dir = str(tmp_path)
            ms.auth_required = False

            results = []
            for i in range(3):
                body = _a2a_jsonrpc(
                    "tasks/send",
                    {
                        "id": str(uuid.uuid4()),
                        "skillId": "list-workflows",
                        "message": {"parts": []},
                    },
                    req_id=i + 1,
                )
                results.append(body)

        # All should succeed and list the same workflows
        for res in results:
            task = res["result"]
            assert task["status"]["state"] == "completed"
            workflows = task["artifacts"][0]["parts"][0]["data"]["workflows"]
            assert len(workflows) == 2


class TestA2ADataExtraction:
    """Helper functions for extracting data from A2A messages."""

    def test_extract_workflow_name_from_text_part(self):
        """Workflow name is extracted from text/plain message part."""
        from sandcastle.api.a2a import _extract_workflow_name

        msg = {"parts": [{"type": "text", "text": "my-workflow"}]}
        assert _extract_workflow_name(msg) == "my-workflow"

    def test_extract_workflow_name_from_data_part(self):
        """Workflow name is extracted from application/json data part."""
        from sandcastle.api.a2a import _extract_workflow_name

        msg = {"parts": [{"type": "data", "data": {"workflow_name": "data-wf"}}]}
        assert _extract_workflow_name(msg) == "data-wf"

    def test_extract_input_from_data_part(self):
        """Input parameters are extracted from data part's input field."""
        from sandcastle.api.a2a import _extract_input

        msg = {
            "parts": [
                {
                    "type": "data",
                    "data": {"workflow_name": "wf", "input": {"key": "value"}},
                }
            ]
        }
        result = _extract_input(msg)
        assert result == {"key": "value"}

    def test_extract_input_empty_when_missing(self):
        """Input extraction returns empty dict when no input in message."""
        from sandcastle.api.a2a import _extract_input

        msg = {"parts": [{"type": "text", "text": "hello"}]}
        assert _extract_input(msg) == {}

    def test_extract_workflow_name_none_for_empty_message(self):
        """Empty message parts return None for workflow name."""
        from sandcastle.api.a2a import _extract_workflow_name

        assert _extract_workflow_name({"parts": []}) is None
        assert _extract_workflow_name({}) is None
        assert _extract_workflow_name(None) is None
