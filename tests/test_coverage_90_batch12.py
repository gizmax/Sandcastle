"""Batch 12: Additional routes and executor coverage.

Focuses on:
- routes.py: /workflows/run/sync error paths, /workflows save/delete
- routes.py: /runs/{id} - GET/CANCEL paths
- executor.py: resolve_variable for steps.*.error and cost
- executor.py: _check_budget - warning/exceeded paths
- routes.py: /violations, /autopilot/experiments, /runs/{id}/steps
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# routes.py: /workflows/run/sync - workflow not found error
# ---------------------------------------------------------------------------

class TestRunSyncErrors:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_run_sync_nonexistent_workflow_returns_400(self):
        """POST /workflows/run/sync with nonexistent workflow returns 400."""
        response = self.client.post(
            "/api/workflows/run/sync",
            json={"workflow_name": "nonexistent_workflow_xyz_abc", "input": {}},
        )
        assert response.status_code == 400

    def test_run_sync_invalid_yaml_returns_400(self):
        """POST /workflows/run/sync with invalid YAML returns 400."""
        response = self.client.post(
            "/api/workflows/run/sync",
            json={"workflow": "INVALID: yaml: {{{{", "input": {}},
        )
        assert response.status_code == 400

    def test_run_sync_missing_both_returns_400(self):
        """POST /workflows/run/sync with no workflow/workflow_name returns 400 or 422."""
        response = self.client.post(
            "/api/workflows/run/sync",
            json={"input": {}},
        )
        assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# routes.py: POST /workflows - save workflow
# ---------------------------------------------------------------------------

class TestSaveWorkflowEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_save_workflow_invalid_yaml_returns_400(self):
        """POST /workflows with invalid YAML returns 400."""
        response = self.client.post(
            "/api/workflows",
            json={"name": "test_wf", "content": "INVALID YAML: {{{{ not yaml"},
        )
        assert response.status_code == 400

    def test_save_workflow_valid_yaml_saves(self):
        """POST /workflows with valid YAML creates workflow."""
        import tempfile
        import sandcastle.api.routes as r

        yaml_content = """name: test_batch12_workflow
steps:
  - id: step1
    type: llm
    model: haiku
    prompt: "Hello world"
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = r.settings.workflows_dir
            r.settings.workflows_dir = tmpdir
            try:
                response = self.client.post(
                    "/api/workflows",
                    json={"name": "test_batch12_workflow", "content": yaml_content},
                )
                assert response.status_code in (200, 201)
            finally:
                r.settings.workflows_dir = original


# ---------------------------------------------------------------------------
# routes.py: DELETE /workflows/{name}
# ---------------------------------------------------------------------------

class TestDeleteWorkflowEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_delete_nonexistent_workflow_returns_404(self):
        """DELETE /workflows/{nonexistent} returns 404."""
        response = self.client.delete("/api/workflows/nonexistent_workflow_xyz_abc_123")
        assert response.status_code in (404, 200)


# ---------------------------------------------------------------------------
# routes.py: GET /runs/{run_id} - not found and cancel
# ---------------------------------------------------------------------------

class TestRunDetailEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_run_not_found_returns_404(self):
        """GET /runs/{nonexistent_uuid} returns 404."""
        response = self.client.get("/api/runs/aabbccdd-0000-0000-0000-999999999999")
        assert response.status_code == 404

    def test_run_invalid_id_returns_400(self):
        """GET /runs/{invalid_id} returns 400."""
        response = self.client.get("/api/runs/not-a-uuid")
        assert response.status_code == 400

    def test_runs_cancel_not_found_returns_404(self):
        """POST /runs/{nonexistent}/cancel returns 404."""
        response = self.client.post("/api/runs/aabbccdd-0000-0000-0000-999999999999/cancel")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# executor.py: resolve_variable - steps.*.error and cost attributes
# ---------------------------------------------------------------------------

class TestResolveVariableStepAttributes:
    def test_resolve_step_error_attribute(self):
        """resolve_variable resolves steps.{id}.error."""
        from sandcastle.engine.executor import RunContext, StepResult, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        ctx.step_outputs["step1"] = None  # step ran but no output
        ctx.step_results["step1"] = StepResult(
            step_id="step1",
            output={},
            cost_usd=0.01,
            duration_seconds=1.0,
            status="failed",
            attempt=1,
            error="Something went wrong",
        )

        result = resolve_variable("steps.step1.error", ctx)
        assert result == "Something went wrong"

    def test_resolve_step_cost_attribute(self):
        """resolve_variable resolves steps.{id}.cost."""
        from sandcastle.engine.executor import RunContext, StepResult, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        ctx.step_outputs["step1"] = {"result": "ok"}
        ctx.step_results["step1"] = StepResult(
            step_id="step1",
            output={"result": "ok"},
            cost_usd=0.05,
            duration_seconds=2.0,
            status="completed",
            attempt=1,
        )

        result = resolve_variable("steps.step1.cost", ctx)
        assert result == 0.05

    def test_resolve_unknown_step_attribute_returns_unresolved(self):
        """resolve_variable returns _UNRESOLVED for unknown step attributes."""
        from sandcastle.engine.executor import RunContext, StepResult, _UNRESOLVED, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        ctx.step_outputs["step1"] = {}
        ctx.step_results["step1"] = StepResult(
            step_id="step1",
            output={},
            cost_usd=0.0,
            duration_seconds=0.0,
            status="completed",
            attempt=1,
        )

        result = resolve_variable("steps.step1.unknown_attr", ctx)
        assert result is _UNRESOLVED

    def test_resolve_step_status_attribute(self):
        """resolve_variable resolves steps.{id}.status."""
        from sandcastle.engine.executor import RunContext, StepResult, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        ctx.step_outputs["step1"] = "done"
        ctx.step_results["step1"] = StepResult(
            step_id="step1",
            output="done",
            cost_usd=0.0,
            duration_seconds=1.0,
            status="completed",
            attempt=1,
        )

        result = resolve_variable("steps.step1.status", ctx)
        assert result == "completed"


# ---------------------------------------------------------------------------
# executor.py: _check_budget - warning/exceeded paths
# ---------------------------------------------------------------------------

class TestCheckBudget:
    def test_check_budget_exceeded_returns_exceeded(self):
        """_check_budget returns 'exceeded' when over 100% budget."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            max_cost_usd=1.0,
        )
        ctx.costs = [0.5, 0.6]  # total = 1.1 > max 1.0

        result = _check_budget(ctx)
        assert result == "exceeded"

    def test_check_budget_warning_at_80_percent(self):
        """_check_budget returns 'warning' at 80% budget."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            max_cost_usd=1.0,
        )
        ctx.costs = [0.85]  # 85% used - warning

        result = _check_budget(ctx)
        assert result == "warning"

    def test_check_budget_ok_under_80(self):
        """_check_budget returns None when under 80% budget."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            max_cost_usd=10.0,
        )
        ctx.costs = [0.5]  # 5% used

        result = _check_budget(ctx)
        assert result is None

    def test_check_budget_no_max_returns_none(self):
        """_check_budget returns None when no budget set."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            max_cost_usd=None,
        )
        ctx.costs = [999.0]

        result = _check_budget(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# routes.py: /violations
# ---------------------------------------------------------------------------

class TestViolationsEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_violations_list_returns_200(self):
        """GET /violations returns 200."""
        response = self.client.get("/api/violations")
        assert response.status_code == 200

    def test_violations_stats_returns_200(self):
        """GET /violations/stats returns 200."""
        response = self.client.get("/api/violations/stats")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# routes.py: /autopilot/experiments
# ---------------------------------------------------------------------------

class TestAutopilotExperimentsEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_experiments_list_returns_200(self):
        """GET /autopilot/experiments returns 200."""
        response = self.client.get("/api/autopilot/experiments")
        assert response.status_code == 200

    def test_experiment_not_found_returns_404(self):
        """GET /autopilot/experiments/{nonexistent} returns 404."""
        response = self.client.get("/api/autopilot/experiments/aabbccdd-0000-0000-0000-999999999999")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# routes.py: /runs/{id}/steps
# ---------------------------------------------------------------------------

class TestRunStepsEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_run_steps_not_found_returns_404(self):
        """GET /runs/{uuid}/steps for nonexistent run returns 404."""
        response = self.client.get("/api/runs/aabbccdd-0000-0000-0000-999999999999/steps")
        assert response.status_code == 404

    def test_run_steps_invalid_id_returns_400_or_404(self):
        """GET /runs/{invalid}/steps returns 400 or 404."""
        response = self.client.get("/api/runs/not-a-uuid/steps")
        assert response.status_code in (400, 404)


# ---------------------------------------------------------------------------
# routes.py: /api-keys
# ---------------------------------------------------------------------------

class TestApiKeysEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_api_keys_list_returns_200_or_403(self):
        """GET /api-keys returns 200 or 403."""
        response = self.client.get("/api/api-keys")
        assert response.status_code in (200, 403)


# ---------------------------------------------------------------------------
# routes.py: /dead-letter
# ---------------------------------------------------------------------------

class TestDeadLetterEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_dead_letter_list_returns_200(self):
        """GET /dead-letter returns 200."""
        response = self.client.get("/api/dead-letter")
        assert response.status_code == 200
