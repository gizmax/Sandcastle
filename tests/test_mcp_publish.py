"""Tests for MCP-first publishing: CLI command + workflow tool registration.

Covers the v0.31 prep feature that lets Sandcastle workflows be discovered
and called as MCP tools by Claude Desktop, Cursor, Windsurf, and any other
MCP client.

Mocks execute_workflow so no real LLM calls happen.
"""

from __future__ import annotations

import asyncio
import io
import json
import textwrap
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


WORKFLOW_YAML = textwrap.dedent(
    """\
    name: mcp-publish-demo
    description: Demo workflow used by MCP publishing tests.
    input_schema:
      type: object
      properties:
        topic:
          type: string
      required: [topic]
    steps:
      - id: echo
        type: code
        code: |
          result = {"topic": ctx.input.get("topic", "")}
    """,
)


@pytest.fixture()
def workflows_dir(tmp_path, monkeypatch):
    """Create a tmp workflows directory pointed at by settings."""
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "mcp-publish-demo.yaml").write_text(WORKFLOW_YAML)

    # Point Sandcastle's settings at our isolated dir for this test.
    from sandcastle.config import settings

    monkeypatch.setattr(settings, "workflows_dir", str(wf_dir))
    return wf_dir


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    """discover_publishable_workflows finds YAML files in the workflows dir."""

    def test_discover_finds_demo_workflow(self, workflows_dir):
        from sandcastle.mcp_server import discover_publishable_workflows

        rows = discover_publishable_workflows()
        names = {r["name"] for r in rows}
        assert "mcp-publish-demo" in names

    def test_discover_sets_tool_name_and_schema(self, workflows_dir):
        from sandcastle.mcp_server import discover_publishable_workflows

        rows = [r for r in discover_publishable_workflows()
                if r["name"] == "mcp-publish-demo"]
        assert len(rows) == 1
        row = rows[0]
        # Tool name must be a valid identifier and start with a letter.
        assert row["tool_name"].replace("_", "").isalnum()
        assert row["tool_name"][0].isalpha()
        # Schema mirrors the workflow's input_schema.
        assert row["input_schema"]["type"] == "object"
        assert "topic" in row["input_schema"]["properties"]

    def test_sanitize_tool_name_strips_unsafe_chars(self):
        from sandcastle.mcp_server import _sanitize_tool_name

        assert _sanitize_tool_name("foo-bar.baz") == "foo_bar_baz"
        # Leading digit gets prefixed so it stays a valid identifier.
        assert _sanitize_tool_name("123wf").startswith("wf_")
        # Empty input does not crash.
        assert _sanitize_tool_name("") == "workflow"


# ---------------------------------------------------------------------------
# CLI: publish-mcp
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str]) -> tuple[str, str, int]:
    """Invoke sandcastle CLI in-process and capture stdout/stderr."""
    from sandcastle.__main__ import main

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0
    try:
        with patch("sys.argv", ["sandcastle", *argv]), \
             redirect_stdout(stdout_buf), \
             redirect_stderr(stderr_buf):
            main()
    except SystemExit as exc:  # argparse / explicit sys.exit
        exit_code = int(exc.code or 0)
    return stdout_buf.getvalue(), stderr_buf.getvalue(), exit_code


class TestPublishMcpCli:
    """sandcastle publish-mcp emits JSON list and config snippets."""

    def test_list_outputs_json_array(self, workflows_dir):
        stdout, _, code = _run_cli(["publish-mcp"])
        assert code == 0
        parsed = json.loads(stdout)
        assert isinstance(parsed, list)
        names = {row["name"] for row in parsed}
        assert "mcp-publish-demo" in names
        sample = next(r for r in parsed if r["name"] == "mcp-publish-demo")
        assert "tool_name" in sample
        assert "description" in sample
        assert "source_path" in sample

    def test_snippet_for_known_workflow(self, workflows_dir):
        stdout, stderr, code = _run_cli(["publish-mcp", "mcp-publish-demo"])
        assert code == 0
        config = json.loads(stdout)
        assert "mcpServers" in config
        servers = config["mcpServers"]
        assert len(servers) == 1
        (entry,) = servers.values()
        assert entry["command"] == "sandcastle"
        assert "mcp" in entry["args"]
        assert entry["env"]["SANDCASTLE_PUBLISH"] == "mcp-publish-demo"
        # Stderr carries the human-facing paste instructions.
        assert "Claude Desktop" in stderr
        assert "Cursor" in stderr

    def test_unknown_workflow_fails(self, workflows_dir):
        stdout, stderr, code = _run_cli(["publish-mcp", "no-such-wf"])
        assert code == 1
        assert "not publishable" in stderr

    def test_parser_accepts_publish_mcp(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["publish-mcp"])
        assert args.command == "publish-mcp"
        assert args.workflow is None
        args = parser.parse_args(["publish-mcp", "my-wf"])
        assert args.workflow == "my-wf"


# ---------------------------------------------------------------------------
# MCP server registration of workflow tools
# ---------------------------------------------------------------------------


class TestMcpServerRegistersWorkflows:
    """create_mcp_server adds one MCP tool per published workflow."""

    def test_published_workflow_is_registered_as_tool(self, workflows_dir):
        from sandcastle.mcp_server import (
            _sanitize_tool_name,
            create_mcp_server,
        )

        server = create_mcp_server()
        tool_names = {t.name for t in server._tool_manager.list_tools()}
        assert _sanitize_tool_name("mcp-publish-demo") in tool_names

    def test_publish_filter_limits_tools(self, workflows_dir, monkeypatch):
        # Set scope to a non-existent workflow - no per-workflow tool registered.
        monkeypatch.setenv("SANDCASTLE_PUBLISH", "nothing-here")
        from sandcastle.mcp_server import (
            _sanitize_tool_name,
            create_mcp_server,
        )

        server = create_mcp_server()
        tool_names = {t.name for t in server._tool_manager.list_tools()}
        assert _sanitize_tool_name("mcp-publish-demo") not in tool_names
        # Core tools must still be present.
        assert "run_workflow" in tool_names

    def test_manifest_payload_lists_primitives(self, workflows_dir):
        from sandcastle.mcp_server import _manifest_payload

        payload = _manifest_payload()
        # MCP spec rev 2025-11-25 added Elicitation as the 6th primitive.
        assert set(payload["primitives"]) == {
            "tools", "resources", "prompts", "sampling", "roots", "elicitation",
        }
        assert payload.get("spec_revision") == "2025-11-25"
        tools = payload["tools"]
        assert any(t["workflow"] == "mcp-publish-demo" for t in tools)

    def test_workflow_tool_invokes_execute_workflow(self, workflows_dir):
        """Calling a workflow tool routes to execute_workflow (mocked)."""
        from sandcastle.engine.executor import WorkflowResult
        from sandcastle.mcp_server import (
            _register_workflow_tool,
            _sanitize_tool_name,
            discover_publishable_workflows,
        )
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test-publish")
        wf_meta = next(
            w for w in discover_publishable_workflows()
            if w["name"] == "mcp-publish-demo"
        )
        _register_workflow_tool(mcp, wf_meta)

        mock_exec = AsyncMock(return_value=WorkflowResult(
            run_id="mock-run-1",
            outputs={"echo": {"topic": "mcp"}},
            total_cost_usd=0.0,
            status="completed",
        ))
        with patch(
            "sandcastle.engine.executor.execute_workflow", mock_exec,
        ):
            tool_name = _sanitize_tool_name("mcp-publish-demo")
            result = asyncio.run(asyncio.wait_for(
                mcp.call_tool(tool_name, {"input_data": '{"topic": "mcp"}'}),
                timeout=10,
            ))

        # FastMCP returns either content blocks or a (blocks, structured) tuple.
        if isinstance(result, tuple):
            blocks = result[0]
        else:
            blocks = result
        text_payload = None
        for block in blocks:
            text = getattr(block, "text", None)
            if text:
                text_payload = text
                break
        assert text_payload is not None
        parsed = json.loads(text_payload)
        assert parsed["status"] == "completed"
        assert parsed["run_id"] == "mock-run-1"
        mock_exec.assert_awaited_once()


# ---------------------------------------------------------------------------
# Build client config helper
# ---------------------------------------------------------------------------


class TestBuildClientConfig:
    def test_default_server_key(self):
        from sandcastle.mcp_server import build_client_config

        cfg = build_client_config()
        assert "mcpServers" in cfg
        assert "sandcastle" in cfg["mcpServers"]
        entry = cfg["mcpServers"]["sandcastle"]
        assert entry["command"] == "sandcastle"
        assert entry["args"] == ["mcp"]
        assert entry["env"] == {}

    def test_workflow_scoped_server_key(self):
        from sandcastle.mcp_server import build_client_config

        cfg = build_client_config("daily-digest")
        keys = list(cfg["mcpServers"].keys())
        assert keys == ["sandcastle-daily_digest"]
        assert cfg["mcpServers"][keys[0]]["env"]["SANDCASTLE_PUBLISH"] == "daily-digest"
