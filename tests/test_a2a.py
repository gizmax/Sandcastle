"""Tests for the A2A (Agent-to-Agent) protocol endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


class TestAgentCard:
    """Tests for GET /.well-known/agent.json."""

    def test_agent_card_returns_json(self):
        response = client.get("/.well-known/agent.json")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Sandcastle"

    def test_agent_card_has_required_fields(self):
        response = client.get("/.well-known/agent.json")
        data = response.json()
        assert "name" in data
        assert "description" in data
        assert "version" in data
        assert "capabilities" in data
        assert "skills" in data

    def test_agent_card_capabilities(self):
        response = client.get("/.well-known/agent.json")
        data = response.json()
        caps = data["capabilities"]
        assert caps["streaming"] is True
        assert caps["pushNotifications"] is False

    def test_agent_card_skills(self):
        response = client.get("/.well-known/agent.json")
        data = response.json()
        skills = data["skills"]
        assert len(skills) == 2
        skill_ids = {s["id"] for s in skills}
        assert "run-workflow" in skill_ids
        assert "list-workflows" in skill_ids

    def test_agent_card_has_input_output_modes(self):
        response = client.get("/.well-known/agent.json")
        data = response.json()
        assert "defaultInputModes" in data
        assert "defaultOutputModes" in data
        assert "application/json" in data["defaultOutputModes"]


class TestTasksSend:
    """Tests for tasks/send JSON-RPC method."""

    def test_list_workflows_skill(self):
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent"
            response = client.post("/a2a", json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {
                    "id": str(uuid.uuid4()),
                    "skillId": "list-workflows",
                    "message": {"parts": [{"type": "text", "text": "list"}]},
                },
            })
        assert response.status_code == 200
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "1"
        result = data["result"]
        assert result["status"]["state"] == "completed"

    def test_run_workflow_missing_name(self):
        response = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "2",
            "method": "tasks/send",
            "params": {
                "id": str(uuid.uuid4()),
                "message": {"parts": []},
            },
        })
        assert response.status_code == 200
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Missing workflow_name" in result["status"]["message"]

    def test_run_workflow_not_found(self):
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent"
            response = client.post("/a2a", json={
                "jsonrpc": "2.0",
                "id": "3",
                "method": "tasks/send",
                "params": {
                    "id": str(uuid.uuid4()),
                    "message": {
                        "parts": [{"type": "text", "text": "no-such-workflow"}],
                    },
                },
            })
        assert response.status_code == 200
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]


class TestTasksGet:
    """Tests for tasks/get JSON-RPC method."""

    def test_get_nonexistent_task(self):
        with patch("sandcastle.api.a2a.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            task_id = str(uuid.uuid4())
            response = client.post("/a2a", json={
                "jsonrpc": "2.0",
                "id": "4",
                "method": "tasks/get",
                "params": {"id": task_id},
            })

        assert response.status_code == 200
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]

    def test_get_task_invalid_id(self):
        response = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "5",
            "method": "tasks/get",
            "params": {"id": "not-a-uuid"},
        })
        assert response.status_code == 200
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Invalid" in result["status"]["message"]

    def test_get_missing_task_id(self):
        response = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "6",
            "method": "tasks/get",
            "params": {},
        })
        assert response.status_code == 200
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"


class TestTasksCancel:
    """Tests for tasks/cancel JSON-RPC method."""

    def test_cancel_nonexistent_task(self):
        with patch("sandcastle.api.a2a.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            task_id = str(uuid.uuid4())
            response = client.post("/a2a", json={
                "jsonrpc": "2.0",
                "id": "7",
                "method": "tasks/cancel",
                "params": {"id": task_id},
            })

        assert response.status_code == 200
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]


class TestJsonRpcErrors:
    """Tests for JSON-RPC 2.0 error handling."""

    def test_invalid_method(self):
        response = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "err-1",
            "method": "tasks/unknown",
            "params": {},
        })
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32601
        assert "Method not found" in data["error"]["message"]

    def test_missing_method(self):
        response = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "err-2",
            "params": {},
        })
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600

    def test_invalid_params_type(self):
        response = client.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "err-3",
            "method": "tasks/get",
            "params": "not-an-object",
        })
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32602

    def test_invalid_json_body(self):
        response = client.post(
            "/a2a",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32700

    def test_non_dict_body(self):
        response = client.post("/a2a", json=[1, 2, 3])
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600


class TestStatusMapping:
    """Test the Sandcastle-to-A2A status mapping."""

    def test_status_map(self):
        from sandcastle.api.a2a import _map_status

        assert _map_status("queued") == "submitted"
        assert _map_status("running") == "working"
        assert _map_status("completed") == "completed"
        assert _map_status("failed") == "failed"
        assert _map_status("cancelled") == "canceled"

    def test_unknown_status(self):
        from sandcastle.api.a2a import _map_status

        assert _map_status("some_other_status") == "unknown"
