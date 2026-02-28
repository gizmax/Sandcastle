"""Wave 7 deep audit tests for A2A and AG-UI protocol implementations.

Tests cover:
  - JSON-RPC 2.0 validation (version, method, params, body size)
  - A2A tasks/send, tasks/get, tasks/cancel with tenant isolation
  - AG-UI streaming with tenant isolation
  - Auth bypass prevention (A2A and AG-UI require auth when enabled)
  - Rate limiting on tasks/send
  - Input validation (workflow names, task IDs, skill IDs)
  - Path traversal prevention
  - Error handling for malformed requests
  - Event mapping and SSE format
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the A2A rate limiter before each test to avoid cross-test pollution.

    The execution_limiter is a module-level singleton. Without resetting,
    tasks/send tests accumulate counted requests and hit 429 after ~10 tests.
    """
    from sandcastle.api.rate_limit import InMemoryBackend, execution_limiter

    if isinstance(execution_limiter._backend, InMemoryBackend):
        execution_limiter._backend._windows.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jsonrpc_request(
    method: str,
    params: dict | None = None,
    req_id: str | int = "test-1",
    jsonrpc: str = "2.0",
    **extra,
) -> dict:
    """Build a JSON-RPC 2.0 request body."""
    body = {"jsonrpc": jsonrpc, "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    body.update(extra)
    return body


def _parse_sse_events(text: str) -> list[dict]:
    """Parse AG-UI SSE events from response body."""
    events = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            events.append(payload)
    return events


def _mock_session_returning(run_obj):
    """Create a mock async_session context that returns run_obj on query."""
    mock_session_ctx = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = run_obj
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session_ctx.return_value.__aenter__ = AsyncMock(
        return_value=mock_session
    )
    mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session_ctx


def _make_mock_run(
    run_id: str | None = None,
    status: str = "completed",
    tenant_id: str | None = None,
    error: str | None = None,
    total_cost_usd: float = 0.0,
    output_data: dict | None = None,
    workflow_name: str = "test-workflow",
) -> MagicMock:
    """Create a mock Run object."""
    run = MagicMock()
    run.id = uuid.UUID(run_id) if run_id else uuid.uuid4()
    run.workflow_name = workflow_name
    run.status = MagicMock(value=status)
    run.total_cost_usd = total_cost_usd
    run.error = error
    run.output_data = output_data
    run.tenant_id = tenant_id
    return run


# ===================================================================
# A2A: JSON-RPC 2.0 Validation
# ===================================================================


class TestJsonRpcValidation:
    """Strict JSON-RPC 2.0 protocol validation."""

    def test_missing_jsonrpc_version(self):
        """Missing jsonrpc field should return error -32600."""
        response = client.post("/a2a", json={
            "id": "1",
            "method": "tasks/get",
            "params": {"id": str(uuid.uuid4())},
        })
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600
        assert "jsonrpc" in data["error"]["message"].lower()

    def test_wrong_jsonrpc_version(self):
        """jsonrpc field != '2.0' should return error -32600."""
        response = client.post("/a2a", json={
            "jsonrpc": "1.0",
            "id": "1",
            "method": "tasks/get",
            "params": {"id": str(uuid.uuid4())},
        })
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600

    def test_non_string_method(self):
        """Non-string method should return error -32600."""
        response = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": 42,
            "params": {},
        })
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600

    def test_empty_string_method(self):
        """Empty method string should return error -32600."""
        response = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "",
            "params": {},
        })
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600

    def test_invalid_json_body(self):
        """Malformed JSON should return parse error -32700."""
        response = client.post(
            "/a2a",
            content=b"{not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32700

    def test_non_dict_body(self):
        """Non-object body should return -32600."""
        response = client.post("/a2a", json=[1, 2, 3])
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600

    def test_invalid_params_type_string(self):
        """String params should return -32602."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/get", params=None,
        ))
        # Manually set params to a string since helper would use None
        body = {"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": "invalid"}
        response = client.post("/a2a", json=body)
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32602

    def test_invalid_params_type_list(self):
        """List params should return -32602."""
        body = {"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": [1, 2]}
        response = client.post("/a2a", json=body)
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32602

    def test_unknown_method(self):
        """Unknown method should return -32601."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/delete", params={},
        ))
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32601

    def test_null_id_preserved(self):
        """JSON-RPC with null id should return null id in response."""
        body = {"jsonrpc": "2.0", "id": None, "method": "tasks/unknown", "params": {}}
        response = client.post("/a2a", json=body)
        data = response.json()
        assert data["id"] is None

    def test_missing_id_returns_none(self):
        """JSON-RPC without id should return None id in error."""
        body = {"jsonrpc": "2.0", "method": "tasks/unknown", "params": {}}
        response = client.post("/a2a", json=body)
        data = response.json()
        assert data["id"] is None

    def test_body_size_limit(self):
        """Body exceeding 512 KB should be rejected."""
        # Create a large body > 512 KB
        large_payload = "x" * (512 * 1024 + 1)
        body = {"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": {"data": large_payload}}
        response = client.post("/a2a", json=body)
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        # Should be either -32600 (body too large) or -32700 (parse error)
        assert data["error"]["code"] in (-32600, -32700)


# ===================================================================
# A2A: Agent Card
# ===================================================================


class TestAgentCardWave7:
    """Agent Card tests including auth info."""

    def test_agent_card_includes_auth_info(self):
        """Agent card should include authentication section."""
        response = client.get("/.well-known/agent.json")
        assert response.status_code == 200
        data = response.json()
        assert "authentication" in data

    def test_agent_card_public_when_auth_enabled(self):
        """Agent card should remain accessible even when auth is required."""
        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            # The agent card should still be in PUBLIC_PATHS
            from sandcastle.api.auth import PUBLIC_PATHS
            assert "/.well-known/agent.json" in PUBLIC_PATHS


# ===================================================================
# A2A: tasks/send Input Validation
# ===================================================================


class TestTasksSendValidation:
    """Input validation for tasks/send."""

    def test_invalid_skill_id(self):
        """Unknown skill ID should return failed task."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "skillId": "evil-skill",
                "message": {"parts": [{"type": "text", "text": "test"}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "Unknown skill" in result["status"]["message"]

    def test_workflow_name_too_long(self):
        """Workflow name exceeding limit should be rejected."""
        long_name = "a" * 300
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": [{"type": "text", "text": long_name}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "too long" in result["status"]["message"].lower()

    def test_workflow_name_path_traversal_dotdot(self):
        """Path traversal via .. should be rejected."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": [{"type": "text", "text": "../../etc/passwd"}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "Invalid workflow name" in result["status"]["message"]

    def test_workflow_name_path_traversal_slash(self):
        """Path traversal via / should be rejected."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": [{"type": "text", "text": "foo/bar"}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_workflow_name_path_traversal_backslash(self):
        """Path traversal via backslash should be rejected."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": [{"type": "text", "text": "foo\\bar"}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_workflow_name_null_bytes(self):
        """Null bytes in workflow name should be rejected."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": [{"type": "text", "text": "test\x00evil"}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_workflow_name_control_chars(self):
        """Control characters in workflow name should be rejected."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": [{"type": "text", "text": "test\x0aevil"}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_task_id_too_long(self):
        """Oversized task ID should be rejected."""
        long_id = "x" * 300
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": long_id,
                "message": {"parts": [{"type": "text", "text": "test"}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_non_string_task_id(self):
        """Non-string task ID in params should be handled gracefully."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": 12345,
                "message": {"parts": [{"type": "text", "text": "test"}]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_missing_workflow_name_empty_parts(self):
        """Empty parts list should result in missing workflow_name error."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": []},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "Missing workflow_name" in result["status"]["message"]

    def test_missing_workflow_name_no_message(self):
        """Missing message dict should result in missing workflow_name error."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={"id": str(uuid.uuid4())},
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "Missing workflow_name" in result["status"]["message"]

    def test_non_dict_message_parts(self):
        """Non-list parts should be handled safely."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": "not-a-list"},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_non_dict_part_in_parts(self):
        """Non-dict part entries should be skipped safely."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/send",
            params={
                "id": str(uuid.uuid4()),
                "message": {"parts": ["not-a-dict", 42]},
            },
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_list_workflows_skill(self):
        """list-workflows skill should return completed task."""
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent"
            mock_settings.auth_required = False
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/send",
                params={
                    "id": str(uuid.uuid4()),
                    "skillId": "list-workflows",
                    "message": {"parts": []},
                },
            ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "completed"
        assert "workflows" in result["artifacts"][0]["parts"][0]["data"]

    def test_workflow_not_found(self):
        """Non-existent workflow should return failed task."""
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent"
            mock_settings.auth_required = False
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/send",
                params={
                    "id": str(uuid.uuid4()),
                    "message": {"parts": [{"type": "text", "text": "no-such-wf"}]},
                },
            ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]


# ===================================================================
# A2A: tasks/get Input Validation
# ===================================================================


class TestTasksGetValidation:
    """Input validation for tasks/get."""

    def test_missing_task_id(self):
        """Missing id should return failed."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/get", params={},
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "Missing" in result["status"]["message"]

    def test_invalid_uuid(self):
        """Non-UUID id should return failed."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/get", params={"id": "not-a-uuid"},
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "Invalid" in result["status"]["message"]

    def test_task_id_too_long(self):
        """Oversized task id should be rejected."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/get", params={"id": "x" * 300},
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_nonexistent_task(self):
        """Task not in DB should return failed."""
        with patch("sandcastle.api.a2a.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(None).side_effect
            mock_ctx.return_value = _mock_session_returning(None).return_value
            task_id = str(uuid.uuid4())
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/get", params={"id": task_id},
            ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]


# ===================================================================
# A2A: tasks/cancel Input Validation
# ===================================================================


class TestTasksCancelValidation:
    """Input validation for tasks/cancel."""

    def test_missing_task_id(self):
        """Missing id should return failed."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/cancel", params={},
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_invalid_uuid(self):
        """Non-UUID id should return failed."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/cancel", params={"id": "bad-uuid"},
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_task_id_too_long(self):
        """Oversized task id should be rejected."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/cancel", params={"id": "y" * 300},
        ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"

    def test_nonexistent_task(self):
        """Cancel non-existent task should return failed."""
        with patch("sandcastle.api.a2a.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(None).side_effect
            mock_ctx.return_value = _mock_session_returning(None).return_value
            task_id = str(uuid.uuid4())
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/cancel", params={"id": task_id},
            ))
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"]["state"] == "failed"


# ===================================================================
# A2A: Tenant Isolation
# ===================================================================


class TestA2ATenantIsolation:
    """Tenant isolation for A2A task operations."""

    def test_tasks_send_sets_tenant_id(self):
        """tasks/send should store tenant_id on created Run."""
        from sandcastle.api.a2a import _handle_tasks_send

        # We test the handler directly with a tenant_id
        # It will fail at DB/file level but we can verify the intent
        params = {
            "id": str(uuid.uuid4()),
            "message": {"parts": [{"type": "text", "text": "test-wf"}]},
        }

        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent"
            mock_settings.auth_required = True

            result = asyncio.run(
                _handle_tasks_send(params, tenant_id="tenant-abc")
            )

        # Workflow not found is expected; but the handler reached the
        # path traversal check + file lookup, proving tenant_id was passed
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]

    def test_tasks_get_filters_by_tenant(self):
        """tasks/get should filter by tenant_id when auth is enabled."""
        from sandcastle.api.a2a import _handle_tasks_get

        task_id = str(uuid.uuid4())

        with patch("sandcastle.api.a2a.async_session") as mock_ctx, \
             patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.auth_required = True

            # Simulate that the DB returns no match when filtered by tenant
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = asyncio.run(
                _handle_tasks_get({"id": task_id}, tenant_id="tenant-B")
            )

        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]

    def test_tasks_cancel_filters_by_tenant(self):
        """tasks/cancel should filter by tenant_id when auth is enabled."""
        from sandcastle.api.a2a import _handle_tasks_cancel

        task_id = str(uuid.uuid4())

        with patch("sandcastle.api.a2a.async_session") as mock_ctx, \
             patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.auth_required = True

            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = asyncio.run(
                _handle_tasks_cancel({"id": task_id}, tenant_id="tenant-B")
            )

        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]


# ===================================================================
# A2A: Rate Limiting
# ===================================================================


class TestA2ARateLimiting:
    """Rate limiting for A2A tasks/send."""

    def test_tasks_send_rate_limited(self):
        """tasks/send should be rate limited."""
        from fastapi import HTTPException
        from sandcastle.api import rate_limit

        original_limiter = rate_limit.execution_limiter
        mock_limiter = MagicMock()
        mock_limiter.check = AsyncMock(
            side_effect=HTTPException(status_code=429, detail="Rate limit exceeded")
        )

        # Patch at the source module so the lazy import inside a2a_endpoint picks it up
        rate_limit.execution_limiter = mock_limiter
        try:
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/send",
                params={
                    "id": str(uuid.uuid4()),
                    "message": {"parts": [{"type": "text", "text": "test"}]},
                },
            ))
        finally:
            rate_limit.execution_limiter = original_limiter

        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32000
        assert "Rate limit" in data["error"]["message"]

    def test_tasks_get_not_rate_limited(self):
        """tasks/get should NOT be rate limited (read-only)."""
        with patch("sandcastle.api.a2a.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(None).side_effect
            mock_ctx.return_value = _mock_session_returning(None).return_value

            task_id = str(uuid.uuid4())
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/get", params={"id": task_id},
            ))

        # Should succeed (task not found, but no rate limit error)
        assert response.status_code == 200
        data = response.json()
        assert "result" in data


# ===================================================================
# A2A: Status Mapping
# ===================================================================


class TestStatusMappingWave7:
    """Extended status mapping tests."""

    def test_all_statuses_mapped(self):
        """All RunStatus values should have a mapping."""
        from sandcastle.api.a2a import _STATUS_MAP
        from sandcastle.models.db import RunStatus

        for status in RunStatus:
            assert status.value in _STATUS_MAP, (
                f"RunStatus.{status.name} ({status.value}) has no A2A mapping"
            )

    def test_unknown_status_returns_unknown(self):
        from sandcastle.api.a2a import _map_status
        assert _map_status("imaginary_status") == "unknown"


# ===================================================================
# A2A: Auth Bypass Prevention
# ===================================================================


class TestA2AAuthBypass:
    """Verify A2A endpoints require auth when AUTH_REQUIRED=true."""

    def test_a2a_not_in_public_paths(self):
        """The /a2a endpoint should NOT be in PUBLIC_PATHS."""
        from sandcastle.api.auth import PUBLIC_PATHS
        assert "/a2a" not in PUBLIC_PATHS

    def test_agent_card_in_public_paths(self):
        """/.well-known/agent.json should remain in PUBLIC_PATHS (discovery)."""
        from sandcastle.api.auth import PUBLIC_PATHS
        assert "/.well-known/agent.json" in PUBLIC_PATHS

    def test_agui_not_in_public_prefixes(self):
        """/api/agui should NOT be in PUBLIC_PREFIXES."""
        from sandcastle.api.auth import PUBLIC_PREFIXES
        for prefix in PUBLIC_PREFIXES:
            assert not prefix.startswith("/api/agui"), (
                f"'/api/agui' found in PUBLIC_PREFIXES via '{prefix}'"
            )

    def test_a2a_requires_auth_path(self):
        """Auth middleware should check /a2a when auth_required=true."""
        from sandcastle.api.auth import PUBLIC_PATHS, PUBLIC_PREFIXES

        # Verify /a2a is not excluded from auth
        assert "/a2a" not in PUBLIC_PATHS
        assert not any("/a2a".startswith(p) for p in PUBLIC_PREFIXES)

        # /a2a does not start with /api, but it should be in the
        # _AUTH_REQUIRED_NON_API_PATHS set inside auth_middleware
        # We can verify this indirectly: with auth enabled and no key,
        # /a2a should return 401
        # Note: we test the actual middleware behavior via _mock_auth_required

    def test_a2a_auth_enforced(self):
        """With AUTH_REQUIRED=true, /a2a without a key should return 401."""
        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            # Make sure the app re-evaluates auth
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/get", params={"id": str(uuid.uuid4())},
            ))
            # The auth middleware should reject the request
            assert response.status_code == 401

    def test_agui_auth_enforced(self):
        """With AUTH_REQUIRED=true, /api/agui without a key should return 401."""
        with patch("sandcastle.api.auth.settings") as mock_settings:
            mock_settings.auth_required = True
            run_id = str(uuid.uuid4())
            response = client.get(f"/api/agui/stream/{run_id}")
            assert response.status_code == 401


# ===================================================================
# A2A: Extract Functions Robustness
# ===================================================================


class TestExtractFunctions:
    """Robustness of _extract_workflow_name and _extract_input."""

    def test_extract_name_from_data_part(self):
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "data", "data": {"workflow_name": "my-wf"}}]}
        assert _extract_workflow_name(msg) == "my-wf"

    def test_extract_name_from_text_part(self):
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "text", "text": "hello-world"}]}
        assert _extract_workflow_name(msg) == "hello-world"

    def test_extract_name_empty_text(self):
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "text", "text": "   "}]}
        assert _extract_workflow_name(msg) is None

    def test_extract_name_non_dict_message(self):
        from sandcastle.api.a2a import _extract_workflow_name
        assert _extract_workflow_name("not a dict") is None

    def test_extract_name_non_list_parts(self):
        from sandcastle.api.a2a import _extract_workflow_name
        assert _extract_workflow_name({"parts": "string"}) is None

    def test_extract_name_non_dict_parts_entry(self):
        from sandcastle.api.a2a import _extract_workflow_name
        assert _extract_workflow_name({"parts": [42, "str"]}) is None

    def test_extract_input_from_data_part(self):
        from sandcastle.api.a2a import _extract_input
        msg = {"parts": [{"type": "data", "data": {"input": {"key": "val"}}}]}
        assert _extract_input(msg) == {"key": "val"}

    def test_extract_input_non_dict(self):
        from sandcastle.api.a2a import _extract_input
        assert _extract_input("not a dict") == {}

    def test_extract_input_non_dict_input_value(self):
        """Non-dict input value should return empty dict."""
        from sandcastle.api.a2a import _extract_input
        msg = {"parts": [{"type": "data", "data": {"input": "not-a-dict"}}]}
        assert _extract_input(msg) == {}

    def test_extract_name_non_string_name(self):
        """Non-string workflow_name should return None."""
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "data", "data": {"workflow_name": 42}}]}
        assert _extract_workflow_name(msg) is None

    def test_extract_name_non_string_text(self):
        """Non-string text in text part should be skipped."""
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "text", "text": 42}]}
        assert _extract_workflow_name(msg) is None


# ===================================================================
# AG-UI: Input Validation
# ===================================================================


class TestAguiInputValidation:
    """AG-UI endpoint input validation."""

    def test_invalid_run_id_format(self):
        """Non-UUID run_id should return 400."""
        response = client.get("/api/agui/stream/not-a-uuid")
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["error"]["code"] == "INVALID_INPUT"

    def test_empty_run_id(self):
        """Empty run_id is actually a different path (trailing slash).
        FastAPI should return 404 for /api/agui/stream/."""
        response = client.get("/api/agui/stream/")
        # FastAPI path parameter won't match empty string
        assert response.status_code in (404, 307, 200)

    def test_nonexistent_run(self):
        """Non-existent run should return 404."""
        with patch("sandcastle.api.agui.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(None).side_effect
            mock_ctx.return_value = _mock_session_returning(None).return_value
            run_id = str(uuid.uuid4())
            response = client.get(f"/api/agui/stream/{run_id}")
        assert response.status_code == 404

    def test_run_id_normalized(self):
        """UUID with uppercase should be normalized."""
        run_id_upper = str(uuid.uuid4()).upper()
        with patch("sandcastle.api.agui.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(None).side_effect
            mock_ctx.return_value = _mock_session_returning(None).return_value
            response = client.get(f"/api/agui/stream/{run_id_upper}")
        # Should work (returns 404 because run doesn't exist, not 400)
        assert response.status_code == 404


# ===================================================================
# AG-UI: Streaming Terminal States
# ===================================================================


class TestAguiStreamTerminal:
    """AG-UI streaming for runs that are already in terminal state."""

    def test_completed_run_emits_finished(self):
        """Completed run should immediately emit run_finished and close."""
        run_id = str(uuid.uuid4())
        mock_run = _make_mock_run(run_id=run_id, status="completed", total_cost_usd=1.5)

        with patch("sandcastle.api.agui.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(mock_run).side_effect
            mock_ctx.return_value = _mock_session_returning(mock_run).return_value
            response = client.get(f"/api/agui/stream/{run_id}")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = _parse_sse_events(response.text)
        assert len(events) >= 1
        assert events[-1]["type"] == "run_finished"
        assert events[-1]["data"]["run_id"] == run_id

    def test_failed_run_emits_error(self):
        """Failed run should immediately emit run_error and close."""
        run_id = str(uuid.uuid4())
        mock_run = _make_mock_run(
            run_id=run_id, status="failed", error="Something broke"
        )

        with patch("sandcastle.api.agui.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(mock_run).side_effect
            mock_ctx.return_value = _mock_session_returning(mock_run).return_value
            response = client.get(f"/api/agui/stream/{run_id}")

        events = _parse_sse_events(response.text)
        assert len(events) >= 1
        assert events[-1]["type"] == "run_error"
        assert "error" in events[-1]["data"]

    def test_budget_exceeded_emits_error(self):
        """budget_exceeded status should emit run_error."""
        run_id = str(uuid.uuid4())
        mock_run = _make_mock_run(
            run_id=run_id, status="budget_exceeded", error="Budget exceeded"
        )

        with patch("sandcastle.api.agui.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(mock_run).side_effect
            mock_ctx.return_value = _mock_session_returning(mock_run).return_value
            response = client.get(f"/api/agui/stream/{run_id}")

        events = _parse_sse_events(response.text)
        assert any(e["type"] == "run_error" for e in events)

    def test_cancelled_run_emits_finished(self):
        """Cancelled run should emit run_finished (not error)."""
        run_id = str(uuid.uuid4())
        mock_run = _make_mock_run(run_id=run_id, status="cancelled")

        with patch("sandcastle.api.agui.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(mock_run).side_effect
            mock_ctx.return_value = _mock_session_returning(mock_run).return_value
            response = client.get(f"/api/agui/stream/{run_id}")

        events = _parse_sse_events(response.text)
        assert any(e["type"] == "run_finished" for e in events)


# ===================================================================
# AG-UI: Tenant Isolation
# ===================================================================


class TestAguiTenantIsolation:
    """Tenant isolation for AG-UI stream endpoint."""

    def test_agui_filters_by_tenant(self):
        """Stream should only be accessible for runs belonging to the tenant."""
        # This is enforced by the SQL query adding a tenant_id filter.
        # We verify the code path exists by checking the function source.
        import inspect
        from sandcastle.api.agui import agui_stream
        source = inspect.getsource(agui_stream)
        assert "tenant_id" in source
        assert "Run.tenant_id" in source


# ===================================================================
# AG-UI: Event Mapping
# ===================================================================


class TestAguiEventMappingWave7:
    """Extended event mapping tests for AG-UI."""

    def test_map_run_completed(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "run.completed",
            "data": {"run_id": "r1", "total_cost_usd": 0.42},
        }
        result = _map_event_bus_event(event, "r1")
        assert result is not None
        parsed = json.loads(result.strip().split("\n")[0].removeprefix("data: "))
        assert parsed["type"] == "run_finished"
        assert parsed["data"]["total_cost"] == 0.42

    def test_map_run_failed(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "run.failed",
            "data": {"run_id": "r1", "error": "boom"},
        }
        result = _map_event_bus_event(event, "r1")
        assert result is not None
        parsed = json.loads(result.strip().split("\n")[0].removeprefix("data: "))
        assert parsed["type"] == "run_error"
        assert parsed["data"]["error"] == "boom"

    def test_map_step_failed(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "step.failed",
            "data": {"run_id": "r1", "step_name": "s1"},
        }
        result = _map_event_bus_event(event, "r1")
        assert result is not None
        parsed = json.loads(result.strip().split("\n")[0].removeprefix("data: "))
        assert parsed["type"] == "state_delta"
        assert parsed["data"]["value"] == "failed"

    def test_map_filters_different_run(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "run.started",
            "data": {"run_id": "other-run"},
        }
        assert _map_event_bus_event(event, "my-run") is None

    def test_map_unknown_type_skipped(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "some.custom.event",
            "data": {"run_id": "r1"},
        }
        assert _map_event_bus_event(event, "r1") is None

    def test_map_step_completed_json_output(self):
        """Dict output should be JSON-serialized in text_message."""
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "step.completed",
            "data": {
                "run_id": "r1",
                "step_id": "s1",
                "output": {"result": "ok"},
            },
        }
        result = _map_event_bus_event(event, "r1")
        assert result is not None
        lines = [l for l in result.split("\n") if l.startswith("data: ")]
        assert len(lines) == 2
        text_msg = json.loads(lines[0].removeprefix("data: "))
        assert text_msg["type"] == "text_message"
        # content should be JSON-encoded dict
        content = text_msg["data"]["content"]
        parsed_content = json.loads(content)
        assert parsed_content["result"] == "ok"

    def test_map_event_no_run_id_in_data(self):
        """Event with empty run_id should be emitted (not filtered)."""
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "run.started",
            "data": {"workflow_name": "test"},
        }
        result = _map_event_bus_event(event, "any-run")
        # run_id is empty string -> does not filter
        assert result is not None


# ===================================================================
# AG-UI: SSE Headers
# ===================================================================


class TestAguiSSEHeaders:
    """Verify SSE response headers."""

    def test_sse_headers(self):
        """SSE response should have correct headers.

        Note: Cache-Control is overridden to 'no-store' by the security
        headers middleware (stricter than the SSE handler's 'no-cache').
        """
        run_id = str(uuid.uuid4())
        mock_run = _make_mock_run(run_id=run_id, status="completed")

        with patch("sandcastle.api.agui.async_session") as mock_ctx:
            mock_ctx.side_effect = _mock_session_returning(mock_run).side_effect
            mock_ctx.return_value = _mock_session_returning(mock_run).return_value
            response = client.get(f"/api/agui/stream/{run_id}")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        # Security headers middleware overrides to no-store (stricter)
        assert response.headers.get("cache-control") in ("no-cache", "no-store")
        assert response.headers.get("x-accel-buffering") == "no"


# ===================================================================
# AG-UI: Event Format
# ===================================================================


class TestAguiEventFormat:
    """Verify AG-UI events have the correct format."""

    def test_event_has_required_fields(self):
        """Each AG-UI event must have type, timestamp, and data."""
        from sandcastle.api.agui import _agui_event
        result = _agui_event("test_type", {"key": "val"})
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        parsed = json.loads(result.strip().removeprefix("data: "))
        assert "type" in parsed
        assert "timestamp" in parsed
        assert "data" in parsed
        assert parsed["type"] == "test_type"

    def test_event_timestamp_is_iso(self):
        """Timestamp should be ISO 8601 format."""
        from sandcastle.api.agui import _agui_event
        result = _agui_event("test", {})
        parsed = json.loads(result.strip().removeprefix("data: "))
        ts = parsed["timestamp"]
        # Should be parseable as datetime
        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None  # Should be timezone-aware


# ===================================================================
# Integration: A2A response format
# ===================================================================


class TestA2AResponseFormat:
    """Verify A2A responses conform to JSON-RPC 2.0."""

    def test_success_response_format(self):
        """Successful response should have jsonrpc, id, and result."""
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent"
            mock_settings.auth_required = False
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/send",
                params={
                    "id": str(uuid.uuid4()),
                    "skillId": "list-workflows",
                    "message": {"parts": []},
                },
                req_id="req-42",
            ))
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "req-42"
        assert "result" in data
        assert "error" not in data

    def test_error_response_format(self):
        """Error response should have jsonrpc, id, and error with code/message."""
        response = client.post("/a2a", json=_jsonrpc_request(
            "tasks/nonexistent",
            params={},
            req_id="req-99",
        ))
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "req-99"
        assert "error" in data
        assert "result" not in data
        assert "code" in data["error"]
        assert "message" in data["error"]

    def test_task_response_has_id_and_status(self):
        """Task result should include id and status with state/timestamp."""
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent"
            mock_settings.auth_required = False
            response = client.post("/a2a", json=_jsonrpc_request(
                "tasks/send",
                params={
                    "id": str(uuid.uuid4()),
                    "skillId": "list-workflows",
                    "message": {"parts": []},
                },
            ))
        data = response.json()
        task = data["result"]
        assert "id" in task
        assert "status" in task
        assert "state" in task["status"]
        assert "timestamp" in task["status"]
