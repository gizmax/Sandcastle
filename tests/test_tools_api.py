"""Tests for the /api/tools endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


class TestToolsListEndpoint:
    """Tests for GET /api/tools."""

    def test_list_all_tools(self):
        resp = client.get("/api/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "tools" in data["data"]
        tools = data["data"]["tools"]
        assert len(tools) >= 12

    def test_list_tools_by_category(self):
        resp = client.get("/api/tools?category=communication")
        assert resp.status_code == 200
        tools = resp.json()["data"]["tools"]
        assert all(t["category"] == "communication" for t in tools)
        assert any(t["name"] == "slack" for t in tools)

    def test_tool_response_shape(self):
        resp = client.get("/api/tools")
        tools = resp.json()["data"]["tools"]
        tool = tools[0]
        assert "name" in tool
        assert "description" in tool
        assert "category" in tool
        assert "functions" in tool
        assert "configured" in tool
        assert "missing_credentials" in tool

    def test_tool_functions_shape(self):
        resp = client.get("/api/tools")
        tools = resp.json()["data"]["tools"]
        tool = next(t for t in tools if len(t["functions"]) > 0)
        func = tool["functions"][0]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func

    def test_total_count(self):
        resp = client.get("/api/tools")
        data = resp.json()["data"]
        assert data["total"] == len(data["tools"])


class TestToolDetailEndpoint:
    """Tests for GET /api/tools/{tool_name}."""

    def test_get_slack_tool(self):
        resp = client.get("/api/tools/slack")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "slack"
        assert data["category"] == "communication"
        assert len(data["functions"]) == 3

    def test_get_github_tool(self):
        resp = client.get("/api/tools/github")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "github"
        assert "create_issue" in [f["name"] for f in data["functions"]]

    def test_get_unknown_tool_404(self):
        resp = client.get("/api/tools/nonexistent")
        assert resp.status_code == 404

    def test_credential_status_reported(self):
        resp = client.get("/api/tools/slack")
        data = resp.json()["data"]
        # Without TOOL_SLACK_BOT_TOKEN set, should report not configured
        assert "configured" in data
        assert "missing_credentials" in data
        assert isinstance(data["missing_credentials"], list)


class TestToolCredentialUpdateEndpoint:
    """Tests for PUT /api/tools/{tool_name}/credentials."""

    def test_save_credentials(self, monkeypatch):
        monkeypatch.delenv("TOOL_SLACK_BOT_TOKEN", raising=False)
        resp = client.put(
            "/api/tools/slack/credentials",
            json={"credentials": {"TOOL_SLACK_BOT_TOKEN": "xoxb-test-from-api"}},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "slack"
        assert data["configured"] is True
        assert data["missing_credentials"] == []
        # Cleanup: remove from os.environ
        monkeypatch.delenv("TOOL_SLACK_BOT_TOKEN", raising=False)

    def test_save_unknown_tool_404(self):
        resp = client.put(
            "/api/tools/nonexistent/credentials",
            json={"credentials": {"SOME_VAR": "value"}},
        )
        assert resp.status_code == 404

    def test_reject_invalid_credential_key(self):
        resp = client.put(
            "/api/tools/slack/credentials",
            json={"credentials": {"NOT_A_SLACK_VAR": "value"}},
        )
        assert resp.status_code == 422

    def test_empty_credentials_no_error(self):
        resp = client.put(
            "/api/tools/slack/credentials",
            json={"credentials": {}},
        )
        assert resp.status_code == 200
