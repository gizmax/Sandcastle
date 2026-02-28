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
import re
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Allowed run statuses for filtering (empty string = no filter)
_VALID_STATUSES = frozenset({
    "queued", "running", "completed", "failed", "cancelled",
    "partial", "error", "budget_exceeded", "awaiting_approval",
})

# Maximum items per listing request
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 20

# Maximum length for string parameters to prevent abuse
_MAX_PARAM_LENGTH = 10_000

# Maximum YAML content length (512 KB)
_MAX_YAML_LENGTH = 512_000

# Pattern for validating identifiers (workflow names, IDs, etc.)
# Allows alphanumeric, hyphens, underscores, dots - no path separators
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_identifier(value: str, name: str) -> str:
    """Validate a string used as an identifier (run_id, schedule_id, etc.).

    Rejects empty strings, strings with path separators, control characters,
    and strings that are too long.

    Raises ValueError with a descriptive message.
    """
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    if len(stripped) > 256:
        raise ValueError(f"{name} must not exceed 256 characters")
    if "/" in stripped or "\\" in stripped:
        raise ValueError(f"{name} must not contain path separators")
    if ".." in stripped:
        raise ValueError(f"{name} must not contain '..'")
    # Reject control characters (except normal whitespace)
    if any(ord(c) < 32 and c not in ("\t",) for c in stripped):
        raise ValueError(f"{name} must not contain control characters")
    return stripped


def _validate_workflow_name(name: str) -> str:
    """Validate a workflow name - stricter than generic identifier.

    Only allows alphanumeric characters, hyphens, underscores, and dots.
    """
    validated = _validate_identifier(name, "workflow_name")
    if not _SAFE_ID_RE.match(validated):
        raise ValueError(
            "workflow_name must contain only alphanumeric characters, "
            "hyphens, underscores, and dots"
        )
    return validated


def _validate_limit(limit: int) -> int:
    """Validate a pagination limit parameter."""
    if not isinstance(limit, int):
        raise ValueError(f"limit must be an integer, got {type(limit).__name__}")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > _MAX_LIMIT:
        raise ValueError(f"limit must not exceed {_MAX_LIMIT}")
    return limit


def _validate_status(status: str) -> str | None:
    """Validate a status filter string. Returns None for empty/unset."""
    if not status:
        return None
    status_lower = status.strip().lower()
    if not status_lower:
        return None
    if status_lower not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. "
            f"Must be one of: {', '.join(sorted(_VALID_STATUSES))}"
        )
    return status_lower


def _safe_parse_json(raw: str, param_name: str = "input_data") -> dict:
    """Parse a JSON string into a dict with validation.

    Returns {} for empty/whitespace-only strings.
    Raises ValueError for invalid JSON or non-dict results.
    """
    if not raw or not raw.strip():
        return {}
    if len(raw) > _MAX_PARAM_LENGTH:
        raise ValueError(
            f"{param_name} exceeds maximum length of {_MAX_PARAM_LENGTH} characters"
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{param_name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{param_name} must be a JSON object, got {type(parsed).__name__}")
    return parsed


def _validate_yaml_content(content: str) -> str:
    """Validate YAML content length and basic sanity."""
    if not isinstance(content, str):
        raise ValueError(f"yaml_content must be a string, got {type(content).__name__}")
    stripped = content.strip()
    if not stripped:
        raise ValueError("yaml_content must not be empty")
    if len(stripped) > _MAX_YAML_LENGTH:
        raise ValueError(
            f"yaml_content exceeds maximum length of {_MAX_YAML_LENGTH} characters"
        )
    return stripped


def _validate_cron(cron: str) -> str:
    """Basic validation for a cron expression."""
    if not isinstance(cron, str):
        raise ValueError(f"cron must be a string, got {type(cron).__name__}")
    stripped = cron.strip()
    if not stripped:
        raise ValueError("cron expression must not be empty")
    # A cron expression should have 5 or 6 space-separated fields
    parts = stripped.split()
    if len(parts) < 5 or len(parts) > 6:
        raise ValueError(
            f"cron expression must have 5 or 6 fields, got {len(parts)}: '{stripped}'"
        )
    return stripped


# ---------------------------------------------------------------------------
# Serialization helpers
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
        try:
            return obj.model_dump()
        except Exception:
            pass
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
    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        raise ValueError(
            f"SANDCASTLE_URL must start with http:// or https://, got: {url[:100]}"
        )
    return SandcastleClient(base_url=url, api_key=api_key)


def _json_result(data: Any) -> str:
    """Serialize result to JSON string for MCP response."""
    return json.dumps(data, indent=2, default=str)


def _error_result(error: Exception) -> str:
    """Format an error into a JSON error response for MCP."""
    from sandcastle.sdk import SandcastleError

    if isinstance(error, SandcastleError):
        return _json_result({
            "error": True,
            "code": error.code,
            "message": error.message,
            "status_code": error.status_code,
        })
    if isinstance(error, ValueError):
        return _json_result({
            "error": True,
            "code": "VALIDATION_ERROR",
            "message": str(error),
        })
    return _json_result({
        "error": True,
        "code": "INTERNAL_ERROR",
        "message": str(error),
    })


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
        try:
            validated_name = _validate_workflow_name(workflow_name)
            parsed_input = _safe_parse_json(input_data)
        except ValueError as exc:
            return _error_result(exc)

        client = _get_client()
        try:
            run = client.run(validated_name, input=parsed_input, wait=wait)
            return _json_result(_to_dict(run))
        except Exception as exc:
            return _error_result(exc)
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
        try:
            validated_yaml = _validate_yaml_content(yaml_content)
            parsed_input = _safe_parse_json(input_data)
        except ValueError as exc:
            return _error_result(exc)

        client = _get_client()
        try:
            run = client.run_yaml(validated_yaml, input=parsed_input, wait=wait)
            return _json_result(_to_dict(run))
        except Exception as exc:
            return _error_result(exc)
        finally:
            client.close()

    @mcp.tool()
    def get_run_status(run_id: str) -> str:
        """Get detailed status of a workflow run including all steps.

        Args:
            run_id: The UUID of the run to check.
        """
        try:
            validated_id = _validate_identifier(run_id, "run_id")
        except ValueError as exc:
            return _error_result(exc)

        client = _get_client()
        try:
            run = client.get_run(validated_id)
            return _json_result(_to_dict(run))
        except Exception as exc:
            return _error_result(exc)
        finally:
            client.close()

    @mcp.tool()
    def cancel_run(run_id: str) -> str:
        """Cancel a queued or running workflow.

        Args:
            run_id: The UUID of the run to cancel.
        """
        try:
            validated_id = _validate_identifier(run_id, "run_id")
        except ValueError as exc:
            return _error_result(exc)

        client = _get_client()
        try:
            result = client.cancel_run(validated_id)
            return _json_result(result)
        except Exception as exc:
            return _error_result(exc)
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
            limit: Maximum number of runs to return (1-200, default 20).
        """
        try:
            validated_status = _validate_status(status)
            validated_limit = _validate_limit(limit)
            # Validate workflow name only if provided
            validated_workflow: str | None = None
            if workflow and workflow.strip():
                validated_workflow = _validate_workflow_name(workflow)
        except ValueError as exc:
            return _error_result(exc)

        client = _get_client()
        try:
            result = client.list_runs(
                status=validated_status,
                workflow=validated_workflow,
                limit=validated_limit,
            )
            return _json_result(_to_dicts(result))
        except Exception as exc:
            return _error_result(exc)
        finally:
            client.close()

    @mcp.tool()
    def save_workflow(name: str, yaml_content: str) -> str:
        """Save a workflow YAML definition to the server.

        Args:
            name: Workflow name (without .yaml extension).
            yaml_content: Complete YAML workflow definition.
        """
        try:
            validated_name = _validate_workflow_name(name)
            validated_yaml = _validate_yaml_content(yaml_content)
        except ValueError as exc:
            return _error_result(exc)

        client = _get_client()
        try:
            wf = client.save_workflow(validated_name, validated_yaml)
            return _json_result(_to_dict(wf))
        except Exception as exc:
            return _error_result(exc)
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
        try:
            validated_name = _validate_workflow_name(workflow_name)
            validated_cron = _validate_cron(cron)
            parsed_input = _safe_parse_json(input_data)
        except ValueError as exc:
            return _error_result(exc)

        client = _get_client()
        try:
            schedule = client.create_schedule(
                validated_name, validated_cron, input=parsed_input or None,
            )
            return _json_result(_to_dict(schedule))
        except Exception as exc:
            return _error_result(exc)
        finally:
            client.close()

    @mcp.tool()
    def delete_schedule(schedule_id: str) -> str:
        """Delete a workflow schedule.

        Args:
            schedule_id: The UUID of the schedule to delete.
        """
        try:
            validated_id = _validate_identifier(schedule_id, "schedule_id")
        except ValueError as exc:
            return _error_result(exc)

        client = _get_client()
        try:
            result = client.delete_schedule(validated_id)
            return _json_result(result)
        except Exception as exc:
            return _error_result(exc)
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
        except Exception as exc:
            return _error_result(exc)
        finally:
            client.close()

    @mcp.resource("sandcastle://schedules")
    def resource_schedules() -> str:
        """List all active workflow schedules."""
        client = _get_client()
        try:
            schedules = client.list_schedules()
            return _json_result(_to_dicts(schedules))
        except Exception as exc:
            return _error_result(exc)
        finally:
            client.close()

    @mcp.resource("sandcastle://health")
    def resource_health() -> str:
        """Check Sandcastle server health status."""
        client = _get_client()
        try:
            health = client.health()
            return _json_result(_to_dict(health))
        except Exception as exc:
            return _error_result(exc)
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
