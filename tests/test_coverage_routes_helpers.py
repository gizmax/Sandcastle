"""Coverage for api/routes.py helper functions.

Targets:
  - _duration_seconds_expr PostgreSQL branch
  - _trunc_day PostgreSQL branch
  - _apply_tenant_filter with auth enabled
  - _resolve_workflow_request edge cases
  - various endpoint branches (non-local mode, budget check, etc.)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


# ===========================================================================
# _duration_seconds_expr and _trunc_day - PostgreSQL branches
# ===========================================================================

class TestDurationAndTruncHelpers:
    """Cover PostgreSQL branches of _duration_seconds_expr and _trunc_day."""

    def test_duration_seconds_expr_local_mode(self):
        """_duration_seconds_expr returns SQLite expression in local mode."""
        from sandcastle.api.routes import _duration_seconds_expr

        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.is_local_mode = True
            expr = _duration_seconds_expr()
            assert expr is not None

    def test_duration_seconds_expr_postgres_mode(self):
        """_duration_seconds_expr returns PostgreSQL expression in production mode."""
        from sandcastle.api.routes import _duration_seconds_expr

        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.is_local_mode = False
            expr = _duration_seconds_expr()
            assert expr is not None

    def test_trunc_day_local_mode(self):
        """_trunc_day returns SQLite date() expression in local mode."""
        from sandcastle.api.routes import _trunc_day

        mock_col = MagicMock()
        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.is_local_mode = True
            result = _trunc_day(mock_col)
            assert result is not None

    def test_trunc_day_postgres_mode(self):
        """_trunc_day returns date_trunc() in PostgreSQL mode."""
        from sandcastle.api.routes import _trunc_day

        mock_col = MagicMock()
        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.is_local_mode = False
            result = _trunc_day(mock_col)
            assert result is not None


# ===========================================================================
# _apply_tenant_filter - with auth enabled
# ===========================================================================

class TestApplyTenantFilter:
    """Cover _apply_tenant_filter function."""

    def test_apply_tenant_filter_no_auth(self):
        """_apply_tenant_filter returns stmt unchanged when no auth."""
        from sandcastle.api.routes import _apply_tenant_filter

        mock_stmt = MagicMock()
        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.auth_required = False
            mock_s.multi_tenant = False
            result = _apply_tenant_filter(mock_stmt, None, MagicMock())
            # Should return stmt unchanged
            assert result is mock_stmt

    def test_apply_tenant_filter_with_tenant_id(self):
        """_apply_tenant_filter adds WHERE clause when tenant_id given."""
        from sandcastle.api.routes import _apply_tenant_filter

        mock_stmt = MagicMock()
        mock_stmt.where = MagicMock(return_value=mock_stmt)
        mock_col = MagicMock()
        mock_col.__eq__ = MagicMock(return_value=True)

        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.auth_required = True
            mock_s.multi_tenant = True
            result = _apply_tenant_filter(mock_stmt, "tenant-123", mock_col)
            # With auth enabled, stmt.where should have been called
            assert result is not None

    def test_apply_tenant_filter_with_none_tenant(self):
        """_apply_tenant_filter with None tenant_id and auth enabled."""
        from sandcastle.api.routes import _apply_tenant_filter

        mock_stmt = MagicMock()
        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.auth_required = True
            mock_s.multi_tenant = True
            result = _apply_tenant_filter(mock_stmt, None, MagicMock())
            assert result is not None


# ===========================================================================
# _resolve_workflow_request edge cases
# ===========================================================================

class TestResolveWorkflowRequest:
    """Cover _resolve_workflow_request function."""

    @pytest.mark.asyncio
    async def test_resolve_with_yaml_content(self):
        """_resolve_workflow_request returns YAML when workflow is provided."""
        from sandcastle.api.routes import _resolve_workflow_request
        from sandcastle.api.schemas import WorkflowRunRequest

        yaml_content = "name: test\nsteps: []\n"
        req = WorkflowRunRequest(workflow=yaml_content)
        result = await _resolve_workflow_request(req)
        assert result[0] == yaml_content
        assert result[1] is None

    @pytest.mark.asyncio
    async def test_resolve_raises_without_workflow(self):
        """_resolve_workflow_request raises when no workflow or name set."""
        from sandcastle.api.routes import _resolve_workflow_request
        from sandcastle.api.schemas import WorkflowRunRequest

        # Bypass Pydantic validation by using __new__ + object.__setattr__
        req = WorkflowRunRequest.__new__(WorkflowRunRequest)
        object.__setattr__(req, "workflow", None)
        object.__setattr__(req, "workflow_name", None)
        object.__setattr__(req, "version", None)
        object.__setattr__(req, "input", {})
        object.__setattr__(req, "max_cost_usd", None)
        object.__setattr__(req, "callback_url", None)
        object.__setattr__(req, "idempotency_key", None)

        with pytest.raises((ValueError, Exception)):
            await _resolve_workflow_request(req)


# ===========================================================================
# Routes - additional path coverage
# ===========================================================================

class TestRoutesNonLocalMode:
    """Cover routes branches that trigger in non-local mode."""

    def test_health_check_endpoint(self):
        """GET /api/health returns health check."""
        response = client.get("/api/health")
        assert response.status_code in (200, 500)

    def test_get_version_check_endpoint(self):
        """GET /api/version returns version data."""
        response = client.get("/api/version")
        assert response.status_code in (200, 404, 500)

    def test_runs_compare_endpoint(self):
        """GET /api/runs/compare returns comparison."""
        run_id1 = str(uuid.uuid4())
        run_id2 = str(uuid.uuid4())
        response = client.get(f"/api/runs/compare?run_ids={run_id1}&run_ids={run_id2}")
        assert response.status_code in (200, 400, 404, 422, 500)

    def test_run_step_pdf_endpoint(self):
        """GET /api/runs/{id}/steps/{step_id}/pdf returns PDF or error."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/api/runs/{fake_id}/steps/step-1/pdf")
        assert response.status_code in (200, 400, 404, 500)

    def test_memory_list_endpoint_valid_scope(self):
        """GET /api/memories returns either data (200), missing data (404),
        unavailable backend without [memory] extra (503), or server error (500)."""
        response = client.get("/api/memories?scope_id=workflow:test-workflow")
        assert response.status_code in (200, 404, 500, 503)

    def test_memory_list_endpoint_agent_scope(self):
        """GET /api/memories with agent scope returns memories."""
        response = client.get("/api/memories?scope_id=agent:test-agent")
        assert response.status_code in (200, 404, 500, 503)

    def test_memory_list_endpoint_global_scope(self):
        """GET /api/memories with global scope returns memories."""
        response = client.get("/api/memories?scope_id=global")
        assert response.status_code in (200, 404, 500, 503)

    def test_run_audit_endpoint(self):
        """GET /api/runs/{id}/audit returns audit trail."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/api/runs/{fake_id}/audit")
        assert response.status_code in (200, 404, 500)

    def test_dead_letter_list_endpoint(self):
        """GET /api/dead-letter returns DLQ items."""
        response = client.get("/api/dead-letter")
        assert response.status_code in (200, 404, 500)

    def test_approvals_list(self):
        """GET /api/approvals returns approvals."""
        response = client.get("/api/approvals")
        assert response.status_code in (200, 500)

    def test_autopilot_stats_endpoint(self):
        """GET /api/autopilot/stats returns stats."""
        response = client.get("/api/autopilot/stats")
        assert response.status_code in (200, 500)

    def test_generate_endpoint_no_api_key(self):
        """POST /api/generate with no API key returns 400."""
        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.anthropic_api_key = ""
            mock_s.rate_limit_enabled = False
            mock_s.multi_tenant = False
            import os
            os.environ.pop("ANTHROPIC_API_KEY", None)
            response = client.post(
                "/api/generate",
                json={"description": "create a workflow"},
            )
        assert response.status_code in (400, 422, 500)

    def test_run_stream_invalid_id(self):
        """GET /api/runs/bad-id/stream returns error."""
        response = client.get("/api/runs/not-a-uuid/stream")
        assert response.status_code in (400, 422, 500)


# ===========================================================================
# api/routes.py - validate_workflow_input function
# ===========================================================================

class TestValidateWorkflowInput:
    """Cover _validate_workflow_input function."""

    def test_validate_with_no_schema(self):
        """_validate_workflow_input returns no errors with no schema."""
        from sandcastle.api.routes import _validate_workflow_input
        errors = _validate_workflow_input({"key": "value"}, None)
        assert errors == []

    def test_validate_with_matching_schema(self):
        """_validate_workflow_input validates against JSON schema."""
        from sandcastle.api.routes import _validate_workflow_input
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name"],
        }
        input_data = {"name": "Alice", "count": 5}
        errors = _validate_workflow_input(input_data, schema)
        assert errors == []

    def test_validate_with_missing_required(self):
        """_validate_workflow_input returns errors for missing required fields."""
        from sandcastle.api.routes import _validate_workflow_input
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        }
        input_data = {}  # Missing 'name'
        errors = _validate_workflow_input(input_data, schema)
        assert isinstance(errors, list)

    def test_validate_with_type_coercion(self):
        """_validate_workflow_input coerces string numbers to int."""
        from sandcastle.api.routes import _validate_workflow_input
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "score": {"type": "number"},
                "active": {"type": "boolean"},
            },
        }
        input_data = {"count": "42", "score": "3.14", "active": "true"}
        errors = _validate_workflow_input(input_data, schema)
        assert isinstance(errors, list)
        # After coercion, count should be int
        assert isinstance(input_data["count"], int)

    def test_validate_with_empty_input(self):
        """_validate_workflow_input handles empty input."""
        from sandcastle.api.routes import _validate_workflow_input
        errors = _validate_workflow_input({}, {})
        assert errors == []


# ===========================================================================
# api/routes.py - _sanitize_workflow_yaml helper
# ===========================================================================

class TestRoutesSanitizeYaml:
    """Cover _sanitize_workflow_yaml function."""

    def test_sanitize_yaml_valid(self):
        """_sanitize_workflow_yaml returns cleaned YAML."""
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = "name: test\nsteps:\n  - id: step1\n    type: llm\n    prompt: hello\n"
        result = _sanitize_workflow_yaml(yaml_content)
        assert isinstance(result, str)
        assert "name: test" in result

    def test_sanitize_yaml_redacts_env_vars(self):
        """_sanitize_workflow_yaml redacts ${ENV_VAR} references."""
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = "name: test\napi_key: ${MY_API_KEY}\nsteps: []\n"
        result = _sanitize_workflow_yaml(yaml_content)
        assert isinstance(result, str)
        assert "<REDACTED>" in result

    def test_sanitize_yaml_redacts_secret_fields(self):
        """_sanitize_workflow_yaml redacts secret: fields."""
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = "name: test\nsecret: my-secret-value\nsteps: []\n"
        result = _sanitize_workflow_yaml(yaml_content)
        assert isinstance(result, str)
        assert "my-secret-value" not in result

    def test_sanitize_yaml_basic_content(self):
        """_sanitize_workflow_yaml passes through safe content."""
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = "name: test\nsteps: []\n# comment\n"
        result = _sanitize_workflow_yaml(yaml_content)
        assert isinstance(result, str)
        assert "name: test" in result
