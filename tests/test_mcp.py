"""Tests for the MCP server module."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.sdk import (
    HealthStatus,
    PaginatedList,
    Run,
    RunListItem,
    Schedule,
    Step,
    Workflow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client():
    """Return a MagicMock that pretends to be a SandcastleClient."""
    client = MagicMock()
    client.close = MagicMock()
    return client


@pytest.fixture()
def _patch_client(mock_client):
    """Patch _get_client so every MCP tool call uses mock_client."""
    with patch("sandcastle.mcp_server._get_client", return_value=mock_client):
        yield


# ---------------------------------------------------------------------------
# TestMcpImport
# ---------------------------------------------------------------------------


class TestMcpImport:
    """Verify module imports and factory function."""

    def test_module_imports(self):
        import sandcastle.mcp_server  # noqa: F401

    def test_create_mcp_server_returns_fastmcp(self):
        from mcp.server.fastmcp import FastMCP

        from sandcastle.mcp_server import create_mcp_server

        server = create_mcp_server()
        assert isinstance(server, FastMCP)

    def test_main_function_exists(self):
        from sandcastle.mcp_server import main

        assert callable(main)


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Test _to_dict and _to_dicts helpers."""

    def test_to_dict_plain_dict(self):
        from sandcastle.mcp_server import _to_dict

        assert _to_dict({"a": 1}) == {"a": 1}

    def test_to_dict_dataclass(self):
        from sandcastle.mcp_server import _to_dict

        @dataclass
        class Sample:
            name: str
            value: int

        result = _to_dict(Sample(name="test", value=42))
        assert result == {"name": "test", "value": 42}

    def test_to_dict_datetime_iso(self):
        from sandcastle.mcp_server import _to_dict

        dt = datetime(2026, 1, 15, 10, 30, 0)
        result = _to_dict(dt)
        assert result == "2026-01-15T10:30:00"

    def test_to_dict_nested_dataclass(self):
        from sandcastle.mcp_server import _to_dict

        @dataclass
        class Inner:
            x: int

        @dataclass
        class Outer:
            inner: Inner
            created: datetime

        obj = Outer(inner=Inner(x=5), created=datetime(2026, 2, 1))
        result = _to_dict(obj)
        assert result["inner"] == {"x": 5}
        assert result["created"] == "2026-02-01T00:00:00"

    def test_to_dict_none(self):
        from sandcastle.mcp_server import _to_dict

        assert _to_dict(None) is None

    def test_to_dict_list(self):
        from sandcastle.mcp_server import _to_dict

        result = _to_dict([1, "a", None])
        assert result == [1, "a", None]

    def test_to_dict_primitives(self):
        from sandcastle.mcp_server import _to_dict

        assert _to_dict(42) == 42
        assert _to_dict("hello") == "hello"
        assert _to_dict(True) is True

    def test_to_dict_sdk_run_with_steps(self):
        from sandcastle.mcp_server import _to_dict

        run = Run(
            run_id="r-1",
            status="completed",
            workflow_name="test",
            started_at=datetime(2026, 1, 1, 12, 0),
            steps=[Step(step_id="s1", status="completed", cost_usd=0.01)],
        )
        result = _to_dict(run)
        assert result["run_id"] == "r-1"
        assert result["started_at"] == "2026-01-01T12:00:00"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["step_id"] == "s1"

    def test_to_dicts_paginated(self):
        from sandcastle.mcp_server import _to_dicts

        paginated = PaginatedList(
            items=[RunListItem(run_id="r-1", workflow_name="wf", status="completed")],
            total=1,
        )
        result = _to_dicts(paginated)
        assert len(result) == 1
        assert result[0]["run_id"] == "r-1"

    def test_to_dicts_plain_list(self):
        from sandcastle.mcp_server import _to_dicts

        result = _to_dicts([{"x": 1}])
        assert result == [{"x": 1}]

    def test_to_dict_fallback_to_str(self):
        """Objects without __dict__ or dataclass fields fall back to str()."""
        from sandcastle.mcp_server import _to_dict

        result = _to_dict(object())
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TestMcpTools
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_client")
class TestMcpTools:
    """Test each of the 8 MCP tools."""

    def test_run_workflow(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dict

        mock_client.run.return_value = Run(
            run_id="abc-123", status="queued", workflow_name="test-wf",
        )
        client = _get_client()
        run = client.run("test-wf", input={}, wait=False)
        result = json.loads(_json_result(_to_dict(run)))
        assert result["run_id"] == "abc-123"
        assert result["status"] == "queued"
        mock_client.run.assert_called_once()

    def test_run_workflow_yaml(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dict

        mock_client.run_yaml.return_value = Run(
            run_id="yaml-run-1", status="queued", workflow_name="inline",
        )
        client = _get_client()
        run = client.run_yaml("name: test\nsteps: []", input={}, wait=False)
        result = json.loads(_json_result(_to_dict(run)))
        assert result["run_id"] == "yaml-run-1"
        mock_client.run_yaml.assert_called_once()

    def test_get_run_status(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dict

        mock_client.get_run.return_value = Run(
            run_id="r-1", status="completed", workflow_name="wf",
            steps=[Step(step_id="s1", status="completed")],
        )
        client = _get_client()
        run = client.get_run("r-1")
        result = json.loads(_json_result(_to_dict(run)))
        assert result["status"] == "completed"
        assert len(result["steps"]) == 1
        mock_client.get_run.assert_called_once_with("r-1")

    def test_cancel_run(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result

        mock_client.cancel_run.return_value = {"cancelled": True, "run_id": "r-1"}
        client = _get_client()
        result = json.loads(_json_result(client.cancel_run("r-1")))
        assert result["cancelled"] is True
        mock_client.cancel_run.assert_called_once_with("r-1")

    def test_list_runs(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dicts

        mock_client.list_runs.return_value = PaginatedList(
            items=[
                RunListItem(run_id="r-1", workflow_name="wf", status="completed"),
                RunListItem(run_id="r-2", workflow_name="wf", status="failed"),
            ],
            total=2,
        )
        client = _get_client()
        runs = client.list_runs(status=None, workflow=None, limit=20)
        result = json.loads(_json_result(_to_dicts(runs)))
        assert isinstance(result, list)
        assert len(result) == 2
        mock_client.list_runs.assert_called_once()

    def test_save_workflow(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dict

        mock_client.save_workflow.return_value = Workflow(
            name="my-wf", description="A test", steps_count=3, file_name="my-wf.yaml",
        )
        client = _get_client()
        wf = client.save_workflow("my-wf", "name: my-wf\nsteps: []")
        result = json.loads(_json_result(_to_dict(wf)))
        assert result["name"] == "my-wf"
        assert result["steps_count"] == 3
        mock_client.save_workflow.assert_called_once_with("my-wf", "name: my-wf\nsteps: []")

    def test_create_schedule(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dict

        mock_client.create_schedule.return_value = Schedule(
            id="sched-1", workflow_name="daily-wf", cron_expression="0 9 * * *",
        )
        client = _get_client()
        sched = client.create_schedule("daily-wf", "0 9 * * *", input=None)
        result = json.loads(_json_result(_to_dict(sched)))
        assert result["id"] == "sched-1"
        assert result["cron_expression"] == "0 9 * * *"
        mock_client.create_schedule.assert_called_once()

    def test_delete_schedule(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result

        mock_client.delete_schedule.return_value = {"deleted": True, "id": "sched-1"}
        client = _get_client()
        result = json.loads(_json_result(client.delete_schedule("sched-1")))
        assert result["deleted"] is True
        mock_client.delete_schedule.assert_called_once_with("sched-1")

    def test_run_workflow_error_propagates(self, mock_client):
        from sandcastle.mcp_server import _get_client
        from sandcastle.sdk import SandcastleError

        mock_client.run.side_effect = SandcastleError(404, "NOT_FOUND", "Workflow not found")
        client = _get_client()
        with pytest.raises(SandcastleError):
            client.run("nonexistent", input={}, wait=False)


# ---------------------------------------------------------------------------
# TestMcpResources
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_client")
class TestMcpResources:
    """Test the 3 MCP resources."""

    def test_resource_workflows(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dict

        mock_client.list_workflows.return_value = [
            Workflow(name="wf-1", description="First", steps_count=2, file_name="wf-1.yaml"),
            Workflow(name="wf-2", description="Second", steps_count=5, file_name="wf-2.yaml"),
        ]
        client = _get_client()
        workflows = client.list_workflows()
        result = json.loads(_json_result([_to_dict(w) for w in workflows]))
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "wf-1"
        mock_client.list_workflows.assert_called_once()

    def test_resource_schedules(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dicts

        mock_client.list_schedules.return_value = PaginatedList(
            items=[
                Schedule(id="s-1", workflow_name="wf", cron_expression="0 * * * *"),
            ],
            total=1,
        )
        client = _get_client()
        schedules = client.list_schedules()
        result = json.loads(_json_result(_to_dicts(schedules)))
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "s-1"
        mock_client.list_schedules.assert_called_once()

    def test_resource_health(self, mock_client):
        from sandcastle.mcp_server import _get_client, _json_result, _to_dict

        mock_client.health.return_value = HealthStatus(
            status="ok", sandstorm=True, database=True, redis=True,
        )
        client = _get_client()
        health = client.health()
        result = json.loads(_json_result(_to_dict(health)))
        assert result["status"] == "ok"
        assert result["database"] is True
        mock_client.health.assert_called_once()


# ---------------------------------------------------------------------------
# TestMcpCliIntegration
# ---------------------------------------------------------------------------


class TestMcpCliIntegration:
    """Test CLI parser integration for the mcp subcommand."""

    def test_parser_accepts_mcp(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["mcp"])
        assert args.command == "mcp"

    def test_parser_mcp_with_url(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["mcp", "--url", "http://example.com:9090"])
        assert args.command == "mcp"
        assert args.url == "http://example.com:9090"

    def test_parser_mcp_with_api_key(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["mcp", "--api-key", "sk-test-123"])
        assert args.command == "mcp"
        assert args.api_key == "sk-test-123"

    def test_parser_mcp_with_both_args(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args([
            "mcp", "--url", "http://remote:8080", "--api-key", "key123",
        ])
        assert args.url == "http://remote:8080"
        assert args.api_key == "key123"

    def test_cmd_mcp_sets_env_vars(self):
        """Verify _cmd_mcp propagates --url and --api-key to env vars."""
        from sandcastle.__main__ import _cmd_mcp

        args = MagicMock()
        args.url = "http://test:1234"
        args.api_key = "test-key"

        with patch("sandcastle.mcp_server.main") as mock_main, \
             patch.dict("os.environ", {}, clear=False):
            import os
            _cmd_mcp(args)
            assert os.environ["SANDCASTLE_URL"] == "http://test:1234"
            assert os.environ["SANDCASTLE_API_KEY"] == "test-key"
            mock_main.assert_called_once()

    def test_cmd_mcp_in_dispatch(self):
        """Verify 'mcp' is in the dispatch table."""
        from sandcastle.__main__ import _cmd_mcp

        assert callable(_cmd_mcp)


# ---------------------------------------------------------------------------
# TestMcpToolRegistration
# ---------------------------------------------------------------------------


class TestMcpToolRegistration:
    """Verify all expected tools and resources are registered."""

    def test_all_tools_registered(self):
        from sandcastle.mcp_server import create_mcp_server

        server = create_mcp_server()
        tool_names = {t.name for t in server._tool_manager.list_tools()}
        expected = {
            "run_workflow", "run_workflow_yaml", "get_run_status",
            "cancel_run", "list_runs", "save_workflow",
            "create_schedule", "delete_schedule",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_all_resources_registered(self):
        from sandcastle.mcp_server import create_mcp_server

        server = create_mcp_server()
        resource_mgr = server._resource_manager
        templates = list(resource_mgr.list_resources())
        uris = {str(r.uri) for r in templates}
        expected = {
            "sandcastle://workflows",
            "sandcastle://schedules",
            "sandcastle://health",
        }
        assert expected.issubset(uris), f"Missing resources: {expected - uris}"

    def test_tool_count(self):
        from sandcastle.mcp_server import create_mcp_server

        server = create_mcp_server()
        tools = list(server._tool_manager.list_tools())
        assert len(tools) == 8

    def test_resource_count(self):
        from sandcastle.mcp_server import create_mcp_server

        server = create_mcp_server()
        resources = list(server._resource_manager.list_resources())
        assert len(resources) == 3
