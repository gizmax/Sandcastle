"""Comprehensive API contract tests for every Sandcastle endpoint.

Tests cover:
  - Happy paths with valid input
  - Missing / invalid fields (422)
  - Auth required but missing (401)
  - Non-existent resources (404)
  - Edge cases per endpoint
  - Response format consistency
  - Security headers
  - A2A protocol (JSON-RPC 2.0)
  - AG-UI protocol (SSE)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Disable rate limiting for all tests in this module
from sandcastle.api import rate_limit as _rl_mod
_rl_mod.execution_limiter.check = AsyncMock()  # noqa: E402

from sandcastle.main import app
from sandcastle.models.db import (
    ApiKey,
    ApprovalRequest,
    ApprovalStatus,
    Run,
    RunStatus,
    RunStep,
    StepStatus,
    async_session,
)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

VALID_WORKFLOW = """
name: test-contract
description: Contract test workflow
steps:
  - id: greet
    prompt: "Say hello to {input.name}"
    model: haiku
    max_turns: 3
"""

VALID_WORKFLOW_WITH_SCHEMA = """
name: test-schema
description: With input schema
input_schema:
  required: [name]
  properties:
    name:
      type: string
    count:
      type: integer
steps:
  - id: greet
    prompt: "Hello {input.name}"
    model: haiku
"""

EMPTY_STEPS_WORKFLOW = """
name: empty
description: no steps
steps: []
"""

INVALID_YAML = "this is not valid yaml: ["

CYCLE_WORKFLOW = """
name: cycle
description: cyclic deps
steps:
  - id: a
    depends_on: [b]
    prompt: "A"
  - id: b
    depends_on: [a]
    prompt: "B"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_id() -> str:
    return str(uuid.uuid4())


async def _create_run(
    run_id: str | None = None,
    status: RunStatus = RunStatus.COMPLETED,
    workflow_name: str = "test-wf",
    total_cost_usd: float = 0.01,
    error: str | None = None,
) -> Run:
    """Insert a Run row directly into the in-memory DB."""
    rid = uuid.UUID(run_id) if run_id else uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        run = Run(
            id=rid,
            workflow_name=workflow_name,
            status=status,
            input_data={"name": "test"},
            output_data={"greet": "hello"} if status == RunStatus.COMPLETED else None,
            total_cost_usd=total_cost_usd,
            started_at=now - timedelta(seconds=5),
            completed_at=now if status != RunStatus.RUNNING else None,
            error=error,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def _create_api_key(
    name: str = "test-key",
    tenant_id: str | None = None,
    is_active: bool = True,
    expires_at: datetime | None = None,
    allowed_cidrs: list[str] | None = None,
) -> ApiKey:
    """Insert an ApiKey row directly into the DB."""
    from sandcastle.api.auth import generate_api_key, hash_key

    plaintext = generate_api_key()
    async with async_session() as session:
        key = ApiKey(
            key_hash=hash_key(plaintext),
            key_prefix=plaintext[:8],
            tenant_id=tenant_id,
            name=name,
            is_active=is_active,
            expires_at=expires_at,
            allowed_cidrs=allowed_cidrs,
        )
        session.add(key)
        await session.commit()
        await session.refresh(key)
    # Attach plaintext for test use (not stored in DB)
    key._plaintext = plaintext
    return key


async def _create_approval(
    run_id: uuid.UUID,
    step_id: str = "review",
    status: ApprovalStatus = ApprovalStatus.PENDING,
) -> ApprovalRequest:
    """Insert an ApprovalRequest row."""
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        approval = ApprovalRequest(
            run_id=run_id,
            step_id=step_id,
            status=status,
            request_data={"content": "Please review"},
            message="Approval needed",
            timeout_at=now + timedelta(hours=1),
            on_timeout="abort",
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        return approval


async def _create_run_step(
    run_id: uuid.UUID,
    step_id: str = "greet",
    status: StepStatus = StepStatus.COMPLETED,
) -> RunStep:
    """Insert a RunStep row."""
    async with async_session() as session:
        step = RunStep(
            run_id=run_id,
            step_id=step_id,
            status=status,
            output_data={"result": "hello"},
            cost_usd=0.001,
            duration_seconds=1.5,
        )
        session.add(step)
        await session.commit()
        await session.refresh(step)
        return step


# ---------------------------------------------------------------------------
# 1. HEALTH ENDPOINT
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /api/health"""

    def test_health_returns_200(self):
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_health_response_shape(self):
        response = client.get("/api/health")
        body = response.json()
        assert "data" in body
        assert "error" in body
        health = body["data"]
        assert "status" in health
        assert "runtime" in health
        assert "database" in health
        assert "redis" in health
        assert health["status"] in ("ok", "degraded")

    def test_health_content_type(self):
        response = client.get("/api/health")
        assert "application/json" in response.headers["content-type"]

    def test_health_security_headers(self):
        response = client.get("/api/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"


# ---------------------------------------------------------------------------
# 2. RUNTIME INFO
# ---------------------------------------------------------------------------


class TestRuntimeEndpoint:
    """GET /api/runtime"""

    def test_runtime_returns_200(self):
        response = client.get("/api/runtime")
        assert response.status_code == 200

    def test_runtime_has_all_fields(self):
        body = client.get("/api/runtime").json()
        data = body["data"]
        for field in ("mode", "database", "queue", "storage", "sandbox_backend", "version"):
            assert field in data, f"Missing field: {field}"
        assert "license" in data
        lic = data["license"]
        assert "status" in lic
        assert "tier" in lic

    def test_runtime_local_mode(self):
        body = client.get("/api/runtime").json()
        data = body["data"]
        # In test env we always use SQLite
        assert data["database"] == "sqlite"
        assert data["mode"] == "local"


# ---------------------------------------------------------------------------
# 3. STATS
# ---------------------------------------------------------------------------


class TestStatsEndpoint:
    """GET /api/stats"""

    def test_stats_returns_200(self):
        response = client.get("/api/stats")
        assert response.status_code == 200

    def test_stats_response_shape(self):
        body = client.get("/api/stats").json()
        data = body["data"]
        for field in (
            "total_runs_today",
            "success_rate",
            "total_cost_today",
            "avg_duration_seconds",
            "runs_by_day",
            "cost_by_workflow",
        ):
            assert field in data, f"Missing field: {field}"
        assert isinstance(data["runs_by_day"], list)
        assert isinstance(data["cost_by_workflow"], list)


# ---------------------------------------------------------------------------
# 4. TEMPLATES
# ---------------------------------------------------------------------------


class TestTemplatesEndpoint:
    """GET /api/templates, GET /api/templates/{name}"""

    def test_list_templates(self):
        response = client.get("/api/templates")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body["data"], list)

    def test_get_nonexistent_template(self):
        response = client.get("/api/templates/nonexistent-template-xyz")
        assert response.status_code == 404

    def test_templates_are_public(self):
        """Templates endpoint does not require auth even if auth_required=True."""
        response = client.get("/api/templates")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. WORKFLOW RUN (async)
# ---------------------------------------------------------------------------


class TestWorkflowRunAsync:
    """POST /api/workflows/run"""

    def test_missing_workflow_and_name(self):
        response = client.post(
            "/api/workflows/run",
            json={"input": {"name": "test"}},
        )
        # Pydantic model_validator catches missing workflow/workflow_name -> 422
        assert response.status_code == 422

    def test_invalid_yaml(self):
        response = client.post(
            "/api/workflows/run",
            json={"workflow": INVALID_YAML, "input": {}},
        )
        assert response.status_code == 400

    def test_empty_steps(self):
        response = client.post(
            "/api/workflows/run",
            json={"workflow": EMPTY_STEPS_WORKFLOW, "input": {}},
        )
        assert response.status_code == 400

    def test_cyclic_dependencies(self):
        response = client.post(
            "/api/workflows/run",
            json={"workflow": CYCLE_WORKFLOW, "input": {}},
        )
        assert response.status_code == 400

    def test_negative_budget(self):
        """max_cost_usd must be >= 0 per schema (ge=0)."""
        response = client.post(
            "/api/workflows/run",
            json={"workflow": VALID_WORKFLOW, "input": {}, "max_cost_usd": -1},
        )
        assert response.status_code == 422

    def test_zero_budget_accepted(self):
        """max_cost_usd=0 is valid (means no budget enforcement)."""
        with patch(
            "sandcastle.api.routes.enqueue_workflow",
            new_callable=AsyncMock,
        ):
            response = client.post(
                "/api/workflows/run",
                json={"workflow": VALID_WORKFLOW, "input": {}, "max_cost_usd": 0},
            )
        assert response.status_code == 202

    def test_valid_workflow_queues(self):
        with patch(
            "sandcastle.api.routes.enqueue_workflow",
            new_callable=AsyncMock,
        ):
            response = client.post(
                "/api/workflows/run",
                json={"workflow": VALID_WORKFLOW, "input": {"name": "World"}},
            )
        assert response.status_code == 202
        data = response.json()["data"]
        assert "run_id" in data
        assert data["status"] == "queued"

    def test_ssrf_callback_url(self):
        """Internal/private callback URLs should be rejected."""
        response = client.post(
            "/api/workflows/run",
            json={
                "workflow": VALID_WORKFLOW,
                "input": {},
                "callback_url": "http://169.254.169.254/latest/meta-data/",
            },
        )
        assert response.status_code == 400

    def test_missing_required_input_field(self):
        """Workflow with input_schema requiring 'name' but not provided."""
        response = client.post(
            "/api/workflows/run",
            json={"workflow": VALID_WORKFLOW_WITH_SCHEMA, "input": {}},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 6. WORKFLOW RUN (sync)
# ---------------------------------------------------------------------------


class TestWorkflowRunSync:
    """POST /api/workflows/run/sync"""

    def test_invalid_yaml_sync(self):
        response = client.post(
            "/api/workflows/run/sync",
            json={"workflow": INVALID_YAML, "input": {}},
        )
        assert response.status_code == 400

    def test_missing_workflow_sync(self):
        response = client.post(
            "/api/workflows/run/sync",
            json={"input": {"name": "test"}},
        )
        # Pydantic model_validator catches missing workflow/workflow_name -> 422
        assert response.status_code == 422

    def test_successful_sync_run(self):
        from sandcastle.engine.executor import WorkflowResult

        mock_result = WorkflowResult(
            run_id="test-sync-123",
            outputs={"greet": "Hello"},
            total_cost_usd=0.001,
            status="completed",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        with patch(
            "sandcastle.api.routes.execute_workflow",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client.post(
                "/api/workflows/run/sync",
                json={"workflow": VALID_WORKFLOW, "input": {"name": "World"}},
            )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "completed"


# ---------------------------------------------------------------------------
# 7. GET RUN
# ---------------------------------------------------------------------------


class TestGetRun:
    """GET /api/runs/{run_id}"""

    def test_invalid_uuid(self):
        response = client.get("/api/runs/not-a-uuid")
        assert response.status_code == 400
        err = response.json()
        assert err["detail"]["error"]["code"] == "INVALID_ID"

    def test_nonexistent_run(self):
        fake_id = _make_run_id()
        response = client.get(f"/api/runs/{fake_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_existing_run(self):
        run = await _create_run()
        response = client.get(f"/api/runs/{run.id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["run_id"] == str(run.id)
        assert data["status"] == "completed"
        assert data["workflow_name"] == "test-wf"

    @pytest.mark.asyncio
    async def test_get_run_with_steps(self):
        run = await _create_run()
        await _create_run_step(run.id)
        response = client.get(f"/api/runs/{run.id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["steps"] is not None
        assert len(data["steps"]) >= 1


# ---------------------------------------------------------------------------
# 8. LIST RUNS
# ---------------------------------------------------------------------------


class TestListRuns:
    """GET /api/runs"""

    def test_list_runs_default(self):
        response = client.get("/api/runs")
        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    def test_pagination_limit_too_high(self):
        """limit > 200 should be rejected by FastAPI validation."""
        response = client.get("/api/runs", params={"limit": 1000})
        assert response.status_code == 422

    def test_pagination_limit_zero(self):
        """limit=0 should be rejected (ge=1)."""
        response = client.get("/api/runs", params={"limit": 0})
        assert response.status_code == 422

    def test_pagination_negative_offset(self):
        """offset=-1 should be rejected (ge=0)."""
        response = client.get("/api/runs", params={"offset": -1})
        assert response.status_code == 422

    def test_filter_by_status(self):
        response = client.get("/api/runs", params={"status": "completed"})
        assert response.status_code == 200

    def test_filter_by_workflow(self):
        response = client.get("/api/runs", params={"workflow": "test-wf"})
        assert response.status_code == 200

    def test_meta_shape(self):
        body = client.get("/api/runs").json()
        meta = body["meta"]
        assert "total" in meta
        assert "limit" in meta
        assert "offset" in meta


# ---------------------------------------------------------------------------
# 9. CANCEL RUN
# ---------------------------------------------------------------------------


class TestCancelRun:
    """POST /api/runs/{run_id}/cancel"""

    def test_cancel_invalid_uuid(self):
        response = client.post("/api/runs/bad-id/cancel")
        assert response.status_code == 400

    def test_cancel_nonexistent_run(self):
        response = client.post(f"/api/runs/{_make_run_id()}/cancel")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_completed_run(self):
        """Cannot cancel an already completed run."""
        run = await _create_run(status=RunStatus.COMPLETED)
        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 400
        assert "INVALID_STATUS" in response.json()["detail"]["error"]["code"]

    @pytest.mark.asyncio
    async def test_cancel_running_run(self):
        """Cancelling a running run should succeed."""
        run = await _create_run(status=RunStatus.RUNNING)
        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["cancelled"] is True

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled(self):
        """Cannot cancel an already cancelled run."""
        run = await _create_run(status=RunStatus.CANCELLED)
        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 10. DELETE RUN
# ---------------------------------------------------------------------------


class TestDeleteRun:
    """DELETE /api/runs/{run_id}"""

    def test_delete_invalid_uuid(self):
        response = client.delete("/api/runs/bad-id")
        assert response.status_code == 400

    def test_delete_nonexistent_run(self):
        response = client.delete(f"/api/runs/{_make_run_id()}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_running_run_forbidden(self):
        """Cannot delete an active (running) run."""
        run = await _create_run(status=RunStatus.RUNNING)
        response = client.delete(f"/api/runs/{run.id}")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_completed_run(self):
        run = await _create_run(status=RunStatus.COMPLETED)
        response = client.delete(f"/api/runs/{run.id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["deleted"] is True

    @pytest.mark.asyncio
    async def test_delete_failed_run(self):
        run = await _create_run(status=RunStatus.FAILED, error="oops")
        response = client.delete(f"/api/runs/{run.id}")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 11. COMPARE RUNS
# ---------------------------------------------------------------------------


class TestCompareRuns:
    """GET /api/runs/compare"""

    def test_compare_missing_params(self):
        response = client.get("/api/runs/compare")
        assert response.status_code == 422

    def test_compare_invalid_uuids(self):
        response = client.get(
            "/api/runs/compare", params={"run_a": "bad", "run_b": "bad"}
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_compare_nonexistent_run(self):
        run = await _create_run()
        fake_id = _make_run_id()
        response = client.get(
            "/api/runs/compare",
            params={"run_a": str(run.id), "run_b": fake_id},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_compare_two_runs(self):
        run_a = await _create_run(workflow_name="wf-a")
        run_b = await _create_run(workflow_name="wf-a")
        response = client.get(
            "/api/runs/compare",
            params={"run_a": str(run_a.id), "run_b": str(run_b.id)},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "run_a" in data
        assert "run_b" in data
        assert "same_workflow" in data


# ---------------------------------------------------------------------------
# 12. REPLAY RUN
# ---------------------------------------------------------------------------


class TestReplayRun:
    """POST /api/runs/{run_id}/replay"""

    def test_replay_invalid_uuid(self):
        response = client.post(
            "/api/runs/bad-id/replay",
            json={"from_step": "greet"},
        )
        assert response.status_code == 400

    def test_replay_missing_from_step(self):
        response = client.post(
            f"/api/runs/{_make_run_id()}/replay",
            json={},
        )
        assert response.status_code == 422

    def test_replay_empty_from_step(self):
        response = client.post(
            f"/api/runs/{_make_run_id()}/replay",
            json={"from_step": ""},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 13. FORK RUN
# ---------------------------------------------------------------------------


class TestForkRun:
    """POST /api/runs/{run_id}/fork"""

    def test_fork_invalid_uuid(self):
        response = client.post(
            "/api/runs/bad-id/fork",
            json={"from_step": "greet"},
        )
        assert response.status_code == 400

    def test_fork_missing_from_step(self):
        response = client.post(
            f"/api/runs/{_make_run_id()}/fork",
            json={},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 14. SCHEDULES
# ---------------------------------------------------------------------------


class TestSchedules:
    """POST/GET/PATCH/DELETE /api/schedules"""

    def test_create_schedule_missing_fields(self):
        response = client.post("/api/schedules", json={})
        assert response.status_code == 422

    def test_create_schedule_empty_workflow_name(self):
        response = client.post(
            "/api/schedules",
            json={"workflow_name": "", "cron_expression": "0 * * * *"},
        )
        assert response.status_code == 422

    def test_list_schedules(self):
        response = client.get("/api/schedules")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body["data"], list)

    def test_patch_nonexistent_schedule(self):
        response = client.patch(
            f"/api/schedules/{_make_run_id()}",
            json={"enabled": False},
        )
        assert response.status_code in (400, 404)

    def test_delete_nonexistent_schedule(self):
        response = client.delete(f"/api/schedules/{_make_run_id()}")
        assert response.status_code in (400, 404)


# ---------------------------------------------------------------------------
# 15. API KEYS
# ---------------------------------------------------------------------------


class TestApiKeys:
    """POST/GET/DELETE /api/api-keys"""

    def test_create_key_missing_name(self):
        response = client.post("/api/api-keys", json={})
        assert response.status_code == 422

    def test_create_key_empty_name(self):
        response = client.post("/api/api-keys", json={"name": ""})
        assert response.status_code == 422

    def test_create_key_success(self):
        response = client.post(
            "/api/api-keys",
            json={"name": f"test-key-{uuid.uuid4().hex[:8]}"},
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert "id" in data
        assert "key" in data
        assert data["key"].startswith("sc_")

    def test_list_keys(self):
        response = client.get("/api/api-keys")
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body

    def test_deactivate_invalid_uuid(self):
        response = client.delete("/api/api-keys/bad-id")
        assert response.status_code == 400

    def test_deactivate_nonexistent_key(self):
        response = client.delete(f"/api/api-keys/{_make_run_id()}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_deactivate_existing_key(self):
        key = await _create_api_key(name="to-deactivate")
        response = client.delete(f"/api/api-keys/{key.id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["deactivated"] is True


# ---------------------------------------------------------------------------
# 16. API KEY ROTATION
# ---------------------------------------------------------------------------


class TestApiKeyRotation:
    """POST /api/api-keys/{key_id}/rotate"""

    def test_rotate_invalid_uuid(self):
        response = client.post(
            "/api/api-keys/bad-id/rotate", json={}
        )
        assert response.status_code == 400

    def test_rotate_nonexistent_key(self):
        response = client.post(
            f"/api/api-keys/{_make_run_id()}/rotate", json={}
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_rotate_inactive_key(self):
        key = await _create_api_key(name="inactive-rotate", is_active=False)
        response = client.post(f"/api/api-keys/{key.id}/rotate", json={})
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_rotate_active_key(self):
        key = await _create_api_key(name="rotate-me")
        response = client.post(f"/api/api-keys/{key.id}/rotate", json={})
        assert response.status_code == 200
        data = response.json()["data"]
        assert "new_key" in data
        assert "new_key_id" in data
        assert "old_key_id" in data
        assert data["old_key_id"] == str(key.id)

    @pytest.mark.asyncio
    async def test_rotate_custom_grace_period(self):
        key = await _create_api_key(name="rotate-grace")
        response = client.post(
            f"/api/api-keys/{key.id}/rotate",
            json={"grace_period_hours": 48},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["grace_period_hours"] == 48


# ---------------------------------------------------------------------------
# 17. API KEY ALLOWLIST
# ---------------------------------------------------------------------------


class TestApiKeyAllowlist:
    """PUT /api/api-keys/{key_id}/allowlist"""

    def test_allowlist_invalid_uuid(self):
        response = client.put(
            "/api/api-keys/bad-id/allowlist",
            json={"cidrs": ["10.0.0.0/8"]},
        )
        assert response.status_code == 400

    def test_allowlist_invalid_cidr(self):
        response = client.put(
            f"/api/api-keys/{_make_run_id()}/allowlist",
            json={"cidrs": ["not-a-cidr"]},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_allowlist_ipv4(self):
        key = await _create_api_key(name="allow-ipv4")
        response = client.put(
            f"/api/api-keys/{key.id}/allowlist",
            json={"cidrs": ["10.0.0.0/8", "192.168.1.0/24"]},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["allowed_cidrs"] == ["10.0.0.0/8", "192.168.1.0/24"]

    @pytest.mark.asyncio
    async def test_allowlist_ipv6(self):
        key = await _create_api_key(name="allow-ipv6")
        response = client.put(
            f"/api/api-keys/{key.id}/allowlist",
            json={"cidrs": ["::1/128", "fe80::/10"]},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_allowlist_clear(self):
        key = await _create_api_key(name="allow-clear")
        response = client.put(
            f"/api/api-keys/{key.id}/allowlist",
            json={"cidrs": []},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_allowlist_nonexistent_key(self):
        response = client.put(
            f"/api/api-keys/{_make_run_id()}/allowlist",
            json={"cidrs": ["10.0.0.0/8"]},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 18. BROWSE DIRECTORY
# ---------------------------------------------------------------------------


class TestBrowseDirectory:
    """GET /api/browse"""

    def test_browse_home(self):
        response = client.get("/api/browse", params={"path": "~"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert "current" in data
        assert "entries" in data

    def test_browse_nonexistent_path(self):
        response = client.get(
            "/api/browse", params={"path": "/nonexistent_xyz_abc"}
        )
        assert response.status_code == 404

    def test_browse_file_not_dir(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        response = client.get("/api/browse", params={"path": str(f)})
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 19. SETTINGS
# ---------------------------------------------------------------------------


class TestSettings:
    """GET/PATCH /api/settings"""

    def test_get_settings(self):
        response = client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "auth_required" in data
        assert "log_level" in data
        # Sensitive fields should be masked
        assert "anthropic_api_key" in data

    def test_patch_invalid_log_level(self):
        response = client.patch(
            "/api/settings",
            json={"log_level": "INVALID_LEVEL"},
        )
        assert response.status_code == 422

    def test_patch_workflow_depth_too_high(self):
        response = client.patch(
            "/api/settings",
            json={"max_workflow_depth": 50},
        )
        assert response.status_code == 422

    def test_patch_workflow_depth_too_low(self):
        response = client.patch(
            "/api/settings",
            json={"max_workflow_depth": 0},
        )
        assert response.status_code == 422

    def test_patch_negative_cost(self):
        response = client.patch(
            "/api/settings",
            json={"default_max_cost_usd": -5.0},
        )
        assert response.status_code == 422

    def test_patch_immutable_fields_silently_stripped(self):
        """Security-critical settings are not in the schema and get silently stripped."""
        response = client.patch(
            "/api/settings",
            json={"auth_required": True},
        )
        # auth_required was removed from SettingsUpdateRequest schema;
        # Pydantic ignores unknown fields, route strips non-mutable keys,
        # returns 200 with no changes applied.
        assert response.status_code == 200

    def test_patch_valid_settings(self):
        response = client.patch(
            "/api/settings",
            json={"log_level": "debug", "max_workflow_depth": 10},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 20. GENERATE WORKFLOW
# ---------------------------------------------------------------------------


class TestGenerateWorkflow:
    """POST /api/generate, POST /api/generate/chat"""

    def test_generate_missing_description(self):
        response = client.post("/api/generate", json={})
        assert response.status_code == 422

    def test_generate_empty_description(self):
        response = client.post("/api/generate", json={"description": ""})
        assert response.status_code == 422

    def test_generate_chat_missing_messages(self):
        response = client.post("/api/generate/chat", json={})
        assert response.status_code == 422

    def test_generate_chat_empty_messages(self):
        response = client.post("/api/generate/chat", json={"messages": []})
        assert response.status_code == 422

    def test_generate_chat_invalid_role(self):
        response = client.post(
            "/api/generate/chat",
            json={"messages": [{"role": "system", "content": "hello"}]},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 21. WORKFLOWS LIST/SAVE
# ---------------------------------------------------------------------------


class TestWorkflows:
    """GET /api/workflows, POST /api/workflows"""

    def test_list_workflows(self):
        response = client.get("/api/workflows")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body["data"], list)

    def test_save_missing_name(self):
        response = client.post("/api/workflows", json={"content": VALID_WORKFLOW})
        assert response.status_code == 422

    def test_save_empty_content(self):
        response = client.post(
            "/api/workflows", json={"name": "test-save", "content": ""}
        )
        assert response.status_code == 422

    def test_save_valid_workflow(self):
        response = client.post(
            "/api/workflows",
            json={"name": "test-save-wf", "content": VALID_WORKFLOW},
        )
        assert response.status_code == 201


# ---------------------------------------------------------------------------
# 22. APPROVALS
# ---------------------------------------------------------------------------


class TestApprovals:
    """GET /api/approvals, GET/POST /api/approvals/{id}"""

    def test_list_approvals(self):
        response = client.get("/api/approvals")
        assert response.status_code == 200
        assert isinstance(response.json()["data"], list)

    def test_get_nonexistent_approval(self):
        response = client.get(f"/api/approvals/{_make_run_id()}")
        assert response.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_get_existing_approval(self):
        run = await _create_run(status=RunStatus.AWAITING_APPROVAL)
        approval = await _create_approval(run.id)
        response = client.get(f"/api/approvals/{approval.id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_approve_already_approved(self):
        run = await _create_run(status=RunStatus.COMPLETED)
        approval = await _create_approval(run.id, status=ApprovalStatus.APPROVED)
        response = client.post(f"/api/approvals/{approval.id}/approve", json={})
        assert response.status_code == 409  # Conflict - already resolved

    @pytest.mark.asyncio
    async def test_reject_already_rejected(self):
        run = await _create_run(status=RunStatus.FAILED)
        approval = await _create_approval(run.id, status=ApprovalStatus.REJECTED)
        response = client.post(f"/api/approvals/{approval.id}/reject", json={})
        assert response.status_code == 409  # Conflict - already resolved


# ---------------------------------------------------------------------------
# 23. DEAD LETTER QUEUE
# ---------------------------------------------------------------------------


class TestDeadLetter:
    """GET/POST /api/dead-letter"""

    def test_list_dead_letter(self):
        response = client.get("/api/dead-letter")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body["data"], list)

    def test_retry_nonexistent_item(self):
        response = client.post(f"/api/dead-letter/{_make_run_id()}/retry")
        assert response.status_code in (400, 404)

    def test_resolve_nonexistent_item(self):
        response = client.post(
            f"/api/dead-letter/{_make_run_id()}/resolve",
            json={"reason": "manual"},
        )
        assert response.status_code in (400, 404)


# ---------------------------------------------------------------------------
# 24. AUTOPILOT EXPERIMENTS
# ---------------------------------------------------------------------------


class TestAutoPilot:
    """GET /api/autopilot/experiments, stats"""

    def test_list_experiments(self):
        response = client.get("/api/autopilot/experiments")
        assert response.status_code == 200

    def test_get_nonexistent_experiment(self):
        response = client.get(f"/api/autopilot/experiments/{_make_run_id()}")
        assert response.status_code in (400, 404)

    def test_autopilot_stats(self):
        response = client.get("/api/autopilot/stats")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "total_experiments" in data


# ---------------------------------------------------------------------------
# 25. VIOLATIONS
# ---------------------------------------------------------------------------


class TestViolations:
    """GET /api/violations, /api/runs/{id}/violations, /api/violations/stats"""

    def test_list_violations(self):
        response = client.get("/api/violations")
        assert response.status_code == 200
        assert isinstance(response.json()["data"], list)

    def test_violations_stats(self):
        response = client.get("/api/violations/stats")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "total_violations_30d" in data

    def test_run_violations_invalid_uuid(self):
        response = client.get("/api/runs/bad-id/violations")
        assert response.status_code == 400

    def test_run_violations_nonexistent_run(self):
        response = client.get(f"/api/runs/{_make_run_id()}/violations")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 26. OPTIMIZER
# ---------------------------------------------------------------------------


class TestOptimizer:
    """GET /api/optimizer/decisions, stats, alerts"""

    def test_list_decisions(self):
        response = client.get("/api/optimizer/decisions")
        assert response.status_code == 200

    def test_decisions_for_nonexistent_run(self):
        response = client.get(f"/api/optimizer/decisions/{_make_run_id()}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_decisions_for_existing_run(self):
        run = await _create_run()
        response = client.get(f"/api/optimizer/decisions/{run.id}")
        assert response.status_code == 200

    def test_optimizer_stats(self):
        response = client.get("/api/optimizer/stats")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "total_decisions_30d" in data

    def test_optimizer_alerts(self):
        response = client.get("/api/optimizer/alerts")
        assert response.status_code == 200

    def test_clear_alerts(self):
        response = client.delete("/api/optimizer/alerts")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 27. TOOLS
# ---------------------------------------------------------------------------


class TestTools:
    """GET /api/tools, /api/tools/{name}"""

    def test_list_tools(self):
        response = client.get("/api/tools")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "tools" in data
        assert "total" in data

    def test_get_nonexistent_tool(self):
        response = client.get("/api/tools/nonexistent-tool-xyz")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 28. EVAL
# ---------------------------------------------------------------------------


class TestEval:
    """POST /api/eval/run, GET /api/eval/runs, GET /api/eval/stats"""

    def test_eval_run_missing_yaml(self):
        response = client.post("/api/eval/run", json={})
        assert response.status_code == 422

    def test_eval_concurrency_too_high(self):
        response = client.post(
            "/api/eval/run",
            json={"suite_yaml": "name: test", "concurrency": 100},
        )
        assert response.status_code == 422

    def test_eval_concurrency_zero(self):
        response = client.post(
            "/api/eval/run",
            json={"suite_yaml": "name: test", "concurrency": 0},
        )
        assert response.status_code == 422

    def test_list_eval_runs(self):
        response = client.get("/api/eval/runs")
        assert response.status_code == 200

    def test_eval_stats(self):
        response = client.get("/api/eval/stats")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "total_runs" in data

    def test_get_nonexistent_eval_run(self):
        response = client.get(f"/api/eval/runs/{_make_run_id()}")
        assert response.status_code in (404, 200)  # May return 404 or 200 with null


# ---------------------------------------------------------------------------
# 29. MEMORY
# ---------------------------------------------------------------------------


class TestMemory:
    """GET/POST/DELETE /api/memories"""

    def test_list_memories_missing_scope(self):
        response = client.get("/api/memories")
        assert response.status_code == 422

    def test_add_memory_missing_fields(self):
        response = client.post("/api/memories", json={})
        assert response.status_code == 422

    def test_search_memories_missing_fields(self):
        response = client.post("/api/memories/search", json={})
        assert response.status_code == 422

    def test_delete_memory_nonexistent(self):
        """Delete memory returns 404 when memory not found."""
        with patch(
            "sandcastle.engine.memory.delete_memory",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = client.delete("/api/memories/nonexistent-id")
        assert response.status_code == 404

    def test_delete_all_memories_missing_scope(self):
        response = client.delete("/api/memories")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 30. HUB
# ---------------------------------------------------------------------------


class TestHub:
    """Hub endpoints - /api/hub/*"""

    def test_hub_registry(self):
        """Should not fail even if GitHub is unreachable (graceful fallback)."""
        response = client.get("/api/hub/registry")
        assert response.status_code == 200

    def test_hub_collections(self):
        response = client.get("/api/hub/collections")
        assert response.status_code == 200

    def test_hub_installed(self):
        response = client.get("/api/hub/installed")
        assert response.status_code == 200
        assert isinstance(response.json()["data"], list)

    def test_hub_playground(self):
        response = client.post(
            "/api/hub/playground",
            json={"slug": "test/demo", "inputs": {"q": "hello"}},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["slug"] == "test/demo"
        assert data["status"] == "completed"

    def test_install_invalid_slug(self):
        """Slug must be author/name format."""
        response = client.post("/api/hub/install/no-slash")
        assert response.status_code == 400

    def test_uninstall_invalid_slug(self):
        response = client.delete("/api/hub/install/no-slash")
        assert response.status_code == 400

    def test_uninstall_not_installed(self):
        response = client.delete("/api/hub/install/fake/nonexistent")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 31. WORKFLOW VERSIONS
# ---------------------------------------------------------------------------


class TestWorkflowVersions:
    """GET /api/workflows/{name}/versions"""

    def test_list_versions_nonexistent_workflow(self):
        response = client.get("/api/workflows/nonexistent-wf/versions")
        assert response.status_code == 404

    def test_get_version_nonexistent(self):
        response = client.get("/api/workflows/nonexistent-wf/versions/1")
        assert response.status_code == 404

    def test_promote_nonexistent(self):
        response = client.post(
            "/api/workflows/nonexistent-wf/promote", json={}
        )
        assert response.status_code == 404

    def test_rollback_nonexistent(self):
        response = client.post(
            "/api/workflows/nonexistent-wf/rollback", json={}
        )
        assert response.status_code == 404

    def test_diff_versions_missing_params(self):
        response = client.get("/api/workflows/test-wf/versions/diff")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 32. WORKFLOW EXPORT
# ---------------------------------------------------------------------------


class TestWorkflowExport:
    """GET /api/workflows/{name}/export"""

    def test_export_nonexistent_workflow(self):
        response = client.get("/api/workflows/nonexistent-wf/export")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 33. CHECK UPDATE
# ---------------------------------------------------------------------------


class TestCheckUpdate:
    """GET /api/check-update"""

    def test_check_update(self):
        response = client.get("/api/check-update")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "current_version" in data
        assert "latest_version" in data
        assert "update_available" in data
        assert isinstance(data["update_available"], bool)


# ---------------------------------------------------------------------------
# 34. A2A PROTOCOL
# ---------------------------------------------------------------------------


class TestA2AProtocol:
    """A2A JSON-RPC endpoints: /.well-known/agent.json, /a2a"""

    def test_agent_card(self):
        response = client.get("/.well-known/agent.json")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Sandcastle"
        assert "version" in data
        assert "capabilities" in data
        assert "skills" in data
        assert len(data["skills"]) >= 2

    def test_agent_card_fields(self):
        data = client.get("/.well-known/agent.json").json()
        assert "streaming" in data["capabilities"]
        assert "defaultInputModes" in data
        assert "defaultOutputModes" in data

    def test_a2a_parse_error(self):
        response = client.post(
            "/a2a",
            content="not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == -32700

    def test_a2a_invalid_request_not_dict(self):
        response = client.post("/a2a", json="string_not_dict")
        assert response.status_code == 200
        body = response.json()
        assert body["error"]["code"] == -32600

    def test_a2a_missing_method(self):
        response = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "params": {}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["error"]["code"] == -32600

    def test_a2a_unknown_method(self):
        response = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": "tasks/nonexistent"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["error"]["code"] == -32601

    def test_a2a_invalid_params_type(self):
        response = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": "bad"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["error"]["code"] == -32602

    def test_a2a_tasks_get_missing_id(self):
        response = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {}},
        )
        assert response.status_code == 200
        body = response.json()
        result = body["result"]
        assert result["status"]["state"] == "failed"

    def test_a2a_tasks_get_invalid_uuid(self):
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/get",
                "params": {"id": "not-a-uuid"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        result = body["result"]
        assert result["status"]["state"] == "failed"

    def test_a2a_tasks_get_nonexistent(self):
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/get",
                "params": {"id": _make_run_id()},
            },
        )
        assert response.status_code == 200
        body = response.json()
        result = body["result"]
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]

    def test_a2a_tasks_cancel_missing_id(self):
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/cancel",
                "params": {},
            },
        )
        assert response.status_code == 200
        body = response.json()
        result = body["result"]
        assert result["status"]["state"] == "failed"

    def test_a2a_tasks_send_missing_workflow(self):
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/send",
                "params": {
                    "message": {"parts": []},
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        result = body["result"]
        assert result["status"]["state"] == "failed"
        assert "Missing workflow_name" in result["status"]["message"]

    def test_a2a_response_format(self):
        """All A2A responses must follow JSON-RPC 2.0 format."""
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 42,
                "method": "tasks/get",
                "params": {"id": _make_run_id()},
            },
        )
        body = response.json()
        assert body["jsonrpc"] == "2.0"
        assert body["id"] == 42


# ---------------------------------------------------------------------------
# 35. AG-UI PROTOCOL
# ---------------------------------------------------------------------------


class TestAGUIProtocol:
    """GET /api/agui/stream/{run_id}"""

    def test_agui_invalid_run_id(self):
        response = client.get("/api/agui/stream/not-a-uuid")
        assert response.status_code == 400

    def test_agui_nonexistent_run(self):
        response = client.get(f"/api/agui/stream/{_make_run_id()}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_agui_completed_run_immediate(self):
        """A completed run should return SSE data immediately."""
        run = await _create_run(status=RunStatus.COMPLETED)
        response = client.get(f"/api/agui/stream/{run.id}")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "data:" in response.text

    @pytest.mark.asyncio
    async def test_agui_failed_run(self):
        """A failed run should emit run_error event."""
        run = await _create_run(status=RunStatus.FAILED, error="something broke")
        response = client.get(f"/api/agui/stream/{run.id}")
        assert response.status_code == 200
        assert "run_error" in response.text


# ---------------------------------------------------------------------------
# 36. RESPONSE FORMAT CONSISTENCY
# ---------------------------------------------------------------------------


class TestResponseFormatConsistency:
    """Verify all responses use consistent shape."""

    def test_success_response_has_data(self):
        response = client.get("/api/health")
        body = response.json()
        assert "data" in body
        assert "error" in body
        assert body["error"] is None

    def test_error_has_code_and_message(self):
        response = client.get(f"/api/runs/not-a-uuid")
        body = response.json()
        err = body.get("detail", body).get("error", {})
        assert "code" in err
        assert "message" in err

    def test_404_api_route(self):
        response = client.get("/api/nonexistent-route")
        assert response.status_code == 404

    def test_content_type_json(self):
        for path in ["/api/health", "/api/runtime", "/api/stats"]:
            response = client.get(path)
            assert "application/json" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# 37. SECURITY HEADERS
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    """Verify security headers are present on all responses."""

    def test_x_content_type_options(self):
        response = client.get("/api/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self):
        response = client.get("/api/health")
        assert response.headers.get("X-Frame-Options") == "DENY"

    def test_referrer_policy(self):
        response = client.get("/api/health")
        assert "strict-origin" in response.headers.get("Referrer-Policy", "")

    def test_permissions_policy(self):
        response = client.get("/api/health")
        assert "camera=()" in response.headers.get("Permissions-Policy", "")

    def test_security_headers_on_error(self):
        """Security headers should be present even on error responses."""
        response = client.get(f"/api/runs/not-a-uuid")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_no_csp_on_api_routes(self):
        """CSP should NOT be on /api routes."""
        response = client.get("/api/health")
        assert "Content-Security-Policy" not in response.headers


# ---------------------------------------------------------------------------
# 38. AUTH MIDDLEWARE EDGE CASES (when auth_required=True)
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    """Test auth behavior when auth_required is toggled."""

    @pytest.mark.asyncio
    async def test_auth_required_no_key(self):
        """When auth is required, missing API key returns 401."""
        from sandcastle.config import settings

        original = settings.auth_required
        try:
            settings.auth_required = True
            response = client.get("/api/stats")
            assert response.status_code == 401
            body = response.json()
            assert body["error"]["code"] == "UNAUTHORIZED"
        finally:
            settings.auth_required = original

    @pytest.mark.asyncio
    async def test_auth_required_invalid_key(self):
        """When auth is required, invalid API key returns 401."""
        from sandcastle.config import settings

        original = settings.auth_required
        try:
            settings.auth_required = True
            response = client.get(
                "/api/stats",
                headers={"X-API-Key": "sc_invalid_key_here"},
            )
            assert response.status_code == 401
        finally:
            settings.auth_required = original

    @pytest.mark.asyncio
    async def test_auth_required_valid_key(self):
        """When auth is required, valid API key works."""
        from sandcastle.config import settings

        key = await _create_api_key(name="auth-test")
        original = settings.auth_required
        try:
            settings.auth_required = True
            response = client.get(
                "/api/stats",
                headers={"X-API-Key": key._plaintext},
            )
            assert response.status_code == 200
        finally:
            settings.auth_required = original

    @pytest.mark.asyncio
    async def test_auth_bearer_token(self):
        """Auth via Bearer token in Authorization header."""
        from sandcastle.config import settings

        key = await _create_api_key(name="bearer-test")
        original = settings.auth_required
        try:
            settings.auth_required = True
            response = client.get(
                "/api/stats",
                headers={"Authorization": f"Bearer {key._plaintext}"},
            )
            assert response.status_code == 200
        finally:
            settings.auth_required = original

    @pytest.mark.asyncio
    async def test_auth_expired_key(self):
        """Expired API key returns 401."""
        from sandcastle.config import settings

        expired_at = datetime.now(timezone.utc) - timedelta(hours=1)
        key = await _create_api_key(name="expired-test", expires_at=expired_at)
        original = settings.auth_required
        try:
            settings.auth_required = True
            response = client.get(
                "/api/stats",
                headers={"X-API-Key": key._plaintext},
            )
            assert response.status_code == 401
            body = response.json()
            assert body["error"]["code"] == "KEY_EXPIRED"
        finally:
            settings.auth_required = original

    def test_public_endpoints_skip_auth(self):
        """Health and templates don't need auth even when auth_required=True."""
        from sandcastle.config import settings

        original = settings.auth_required
        try:
            settings.auth_required = True
            health = client.get("/api/health")
            assert health.status_code == 200
            templates = client.get("/api/templates")
            assert templates.status_code == 200
        finally:
            settings.auth_required = original


# ---------------------------------------------------------------------------
# 39. OPENAPI SPEC
# ---------------------------------------------------------------------------


class TestOpenAPISpec:
    """GET /api/docs, /api/openapi.json"""

    def test_openapi_json_loads(self):
        response = client.get("/api/openapi.json")
        assert response.status_code == 200
        spec = response.json()
        assert "openapi" in spec
        assert "paths" in spec
        assert "info" in spec

    def test_docs_page(self):
        response = client.get("/api/docs")
        assert response.status_code == 200

    def test_redoc_page(self):
        response = client.get("/api/redoc")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 40. PDF ARTIFACT DOWNLOAD
# ---------------------------------------------------------------------------


class TestPDFArtifact:
    """GET /api/runs/{run_id}/steps/{step_id}/pdf"""

    def test_pdf_invalid_run_id(self):
        response = client.get("/api/runs/bad-id/steps/greet/pdf")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_pdf_nonexistent_run(self):
        response = client.get(f"/api/runs/{_make_run_id()}/steps/greet/pdf")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_pdf_nonexistent_step(self):
        run = await _create_run()
        response = client.get(f"/api/runs/{run.id}/steps/nonexistent/pdf")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 41. EVENTS SSE
# ---------------------------------------------------------------------------


class TestEventSSE:
    """GET /api/events - SSE streaming endpoint.

    Note: The /api/events endpoint is a long-polling SSE stream that blocks
    indefinitely. We cannot use the regular TestClient to test it without
    hanging. Instead, we verify the route exists by checking it appears
    in the OpenAPI spec.
    """

    def test_events_endpoint_in_openapi(self):
        """The events endpoint should be registered in the OpenAPI spec."""
        spec = client.get("/api/openapi.json").json()
        assert "/api/events" in spec["paths"]


# ---------------------------------------------------------------------------
# 42. RUN STREAM
# ---------------------------------------------------------------------------


class TestRunStream:
    """GET /api/runs/{run_id}/stream"""

    def test_stream_invalid_uuid(self):
        response = client.get("/api/runs/bad-id/stream")
        assert response.status_code == 400

    def test_stream_nonexistent_run(self):
        response = client.get(f"/api/runs/{_make_run_id()}/stream")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 43. TOOL CONNECTIONS
# ---------------------------------------------------------------------------


class TestToolConnections:
    """Tool connection CRUD endpoints."""

    def test_list_connections_nonexistent_tool(self):
        response = client.get("/api/tools/nonexistent/connections")
        assert response.status_code == 404

    def test_create_connection_missing_fields(self):
        response = client.post("/api/tools/slack/connections", json={})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 44. IDEMPOTENCY
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Idempotency key handling on /api/workflows/run."""

    def test_idempotent_run(self):
        """Two runs with the same idempotency key should return the same run_id."""
        idem_key = f"idem-{uuid.uuid4().hex[:8]}"
        with patch(
            "sandcastle.api.routes.enqueue_workflow",
            new_callable=AsyncMock,
        ):
            resp1 = client.post(
                "/api/workflows/run",
                json={
                    "workflow": VALID_WORKFLOW,
                    "input": {"name": "test"},
                    "idempotency_key": idem_key,
                },
            )
            assert resp1.status_code == 202
            run_id_1 = resp1.json()["data"]["run_id"]

            resp2 = client.post(
                "/api/workflows/run",
                json={
                    "workflow": VALID_WORKFLOW,
                    "input": {"name": "test"},
                    "idempotency_key": idem_key,
                },
            )
            assert resp2.status_code == 202
            data2 = resp2.json()["data"]
            assert data2["run_id"] == run_id_1
            assert data2.get("idempotent") is True


# ---------------------------------------------------------------------------
# 45. WORKFLOW INPUT VALIDATION
# ---------------------------------------------------------------------------


class TestWorkflowInputValidation:
    """Test input_schema validation on workflow run."""

    def test_missing_required_field(self):
        response = client.post(
            "/api/workflows/run",
            json={"workflow": VALID_WORKFLOW_WITH_SCHEMA, "input": {}},
        )
        assert response.status_code == 400
        err = response.json()["detail"]["error"]
        assert "INVALID_INPUT" in err["code"]

    def test_valid_input(self):
        with patch(
            "sandcastle.api.routes.enqueue_workflow",
            new_callable=AsyncMock,
        ):
            response = client.post(
                "/api/workflows/run",
                json={
                    "workflow": VALID_WORKFLOW_WITH_SCHEMA,
                    "input": {"name": "Alice", "count": 5},
                },
            )
        assert response.status_code == 202

    def test_type_coercion_integer(self):
        """String '5' should be coerced to integer 5."""
        with patch(
            "sandcastle.api.routes.enqueue_workflow",
            new_callable=AsyncMock,
        ):
            response = client.post(
                "/api/workflows/run",
                json={
                    "workflow": VALID_WORKFLOW_WITH_SCHEMA,
                    "input": {"name": "Alice", "count": "5"},
                },
            )
        assert response.status_code == 202

    def test_invalid_integer_coercion(self):
        response = client.post(
            "/api/workflows/run",
            json={
                "workflow": VALID_WORKFLOW_WITH_SCHEMA,
                "input": {"name": "Alice", "count": "not-a-number"},
            },
        )
        assert response.status_code == 400
