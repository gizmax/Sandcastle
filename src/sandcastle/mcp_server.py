"""MCP (Model Context Protocol) server for Sandcastle.

Exposes Sandcastle workflows, runs, and schedules as MCP tools and resources.
Designed to run as a stdio child process spawned by Claude Desktop, Cursor, etc.

Usage:
    sandcastle mcp [--url URL] [--api-key KEY]

Configuration via environment variables:
    SANDCASTLE_URL      - API server URL (default: http://localhost:8080)
    SANDCASTLE_API_KEY  - API key for authentication
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_dict(obj: Any) -> Any:
    """Convert SDK dataclass to a JSON-serializable dict.

    Handles nested dataclasses, datetime objects, and plain dicts/lists.
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(item) for item in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: _to_dict(v) for k, v in obj.__dict__.items()}
    return str(obj)


def _to_dicts(data: Any) -> list[dict[str, Any]]:
    """Normalize paginated/list API responses to a list of dicts."""
    if hasattr(data, "items") and isinstance(data.items, list):
        return [_to_dict(i) for i in data.items]
    if isinstance(data, list):
        return [_to_dict(i) for i in data]
    return []


def _get_client():
    """Create a SandcastleClient from environment variables."""
    from sandcastle.sdk import SandcastleClient

    url = os.environ.get("SANDCASTLE_URL", "http://localhost:8080")
    api_key = os.environ.get("SANDCASTLE_API_KEY", "")
    return SandcastleClient(base_url=url, api_key=api_key)


def _json_result(data: Any) -> str:
    """Serialize result to JSON string for MCP response."""
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# MCP Server factory
# ---------------------------------------------------------------------------


def create_mcp_server() -> FastMCP:
    """Create and configure the Sandcastle MCP server.

    Returns a FastMCP instance with all tools and resources registered.
    """
    mcp = FastMCP(
        "Sandcastle",
        instructions=(
            "Sandcastle workflow orchestrator. Use these tools to run workflows, "
            "check run status, manage schedules, and browse available workflows."
        ),
    )

    # -------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------

    @mcp.tool()
    def run_workflow(
        workflow_name: str,
        input_data: str = "{}",
        wait: bool = False,
    ) -> str:
        """Run a saved workflow by name.

        Args:
            workflow_name: Name of the workflow to run.
            input_data: JSON string with input key-value pairs (e.g. '{"url": "https://..."}').
            wait: If true, wait for the workflow to complete before returning.
        """
        client = _get_client()
        try:
            parsed_input = json.loads(input_data) if input_data else {}
            run = client.run(workflow_name, input=parsed_input, wait=wait)
            return _json_result(_to_dict(run))
        finally:
            client.close()

    @mcp.tool()
    def run_workflow_yaml(
        yaml_content: str,
        input_data: str = "{}",
        wait: bool = False,
    ) -> str:
        """Run a workflow from inline YAML definition.

        Args:
            yaml_content: Complete YAML workflow definition.
            input_data: JSON string with input key-value pairs.
            wait: If true, wait for the workflow to complete before returning.
        """
        client = _get_client()
        try:
            parsed_input = json.loads(input_data) if input_data else {}
            run = client.run_yaml(yaml_content, input=parsed_input, wait=wait)
            return _json_result(_to_dict(run))
        finally:
            client.close()

    @mcp.tool()
    def get_run_status(run_id: str) -> str:
        """Get detailed status of a workflow run including all steps.

        Args:
            run_id: The UUID of the run to check.
        """
        client = _get_client()
        try:
            run = client.get_run(run_id)
            return _json_result(_to_dict(run))
        finally:
            client.close()

    @mcp.tool()
    def cancel_run(run_id: str) -> str:
        """Cancel a queued or running workflow.

        Args:
            run_id: The UUID of the run to cancel.
        """
        client = _get_client()
        try:
            result = client.cancel_run(run_id)
            return _json_result(result)
        finally:
            client.close()

    @mcp.tool()
    def list_runs(
        status: str = "",
        workflow: str = "",
        limit: int = 20,
    ) -> str:
        """List workflow runs with optional filters.

        Args:
            status: Filter by status (queued, running, completed, failed). Empty for all.
            workflow: Filter by workflow name. Empty for all.
            limit: Maximum number of runs to return (default 20).
        """
        client = _get_client()
        try:
            result = client.list_runs(
                status=status or None,
                workflow=workflow or None,
                limit=limit,
            )
            return _json_result(_to_dicts(result))
        finally:
            client.close()

    @mcp.tool()
    def save_workflow(name: str, yaml_content: str) -> str:
        """Save a workflow YAML definition to the server.

        Args:
            name: Workflow name (without .yaml extension).
            yaml_content: Complete YAML workflow definition.
        """
        client = _get_client()
        try:
            wf = client.save_workflow(name, yaml_content)
            return _json_result(_to_dict(wf))
        finally:
            client.close()

    @mcp.tool()
    def create_schedule(
        workflow_name: str,
        cron: str,
        input_data: str = "{}",
    ) -> str:
        """Create a cron schedule for a workflow.

        Args:
            workflow_name: Name of the workflow to schedule.
            cron: Cron expression (e.g. '0 9 * * *' for daily at 9am).
            input_data: JSON string with input data for each scheduled run.
        """
        client = _get_client()
        try:
            parsed_input = json.loads(input_data) if input_data else {}
            schedule = client.create_schedule(
                workflow_name, cron, input=parsed_input or None,
            )
            return _json_result(_to_dict(schedule))
        finally:
            client.close()

    @mcp.tool()
    def delete_schedule(schedule_id: str) -> str:
        """Delete a workflow schedule.

        Args:
            schedule_id: The UUID of the schedule to delete.
        """
        client = _get_client()
        try:
            result = client.delete_schedule(schedule_id)
            return _json_result(result)
        finally:
            client.close()

    # -------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------

    @mcp.resource("sandcastle://workflows")
    def resource_workflows() -> str:
        """List all available workflow definitions."""
        client = _get_client()
        try:
            workflows = client.list_workflows()
            return _json_result([_to_dict(w) for w in workflows])
        finally:
            client.close()

    @mcp.resource("sandcastle://schedules")
    def resource_schedules() -> str:
        """List all active workflow schedules."""
        client = _get_client()
        try:
            schedules = client.list_schedules()
            return _json_result(_to_dicts(schedules))
        finally:
            client.close()

    @mcp.resource("sandcastle://health")
    def resource_health() -> str:
        """Check Sandcastle server health status."""
        client = _get_client()
        try:
            health = client.health()
            return _json_result(_to_dict(health))
        finally:
            client.close()

    return mcp


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server with stdio transport."""
    # All logging must go to stderr - stdout is reserved for MCP JSON-RPC
    server = create_mcp_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
