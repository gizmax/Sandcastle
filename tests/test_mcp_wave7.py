"""Wave 7 deep audit tests for MCP server - input validation, error handling,
security, and edge cases.

Tests the MCP server layer's validation, response formatting, and error handling.
All SDK/API calls are mocked - we focus on the MCP server's own behavior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.mcp_server import (
    _MAX_LIMIT,
    _MAX_PARAM_LENGTH,
    _MAX_YAML_LENGTH,
    _VALID_STATUSES,
    _error_result,
    _get_client,
    _json_result,
    _safe_parse_json,
    _to_dict,
    _to_dicts,
    _validate_cron,
    _validate_identifier,
    _validate_limit,
    _validate_status,
    _validate_workflow_name,
    _validate_yaml_content,
    create_mcp_server,
)
from sandcastle.sdk import (
    HealthStatus,
    PaginatedList,
    Run,
    RunListItem,
    SandcastleError,
    Schedule,
    Step,
    Workflow,
)

# ---------------------------------------------------------------------------
# Fixtures
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


@pytest.fixture()
def mcp_server():
    """Create a fresh MCP server instance for testing."""
    return create_mcp_server()


def _get_tool(server, name: str):
    """Look up a tool by name from the MCP server."""
    for tool in server._tool_manager.list_tools():
        if tool.name == name:
            return tool
    raise KeyError(f"Tool '{name}' not found")


def _call_tool(server, tool_name: str, **kwargs) -> str:
    """Call a registered MCP tool function directly by name."""
    tool = _get_tool(server, tool_name)
    return tool.fn(**kwargs)


def _parse_result(result_str: str) -> Any:
    """Parse a JSON result string from an MCP tool."""
    return json.loads(result_str)


# ===========================================================================
# Test: Validation helpers
# ===========================================================================


class TestValidateIdentifier:
    """Tests for _validate_identifier."""

    def test_valid_uuid(self):
        result = _validate_identifier("abc-123-def-456", "run_id")
        assert result == "abc-123-def-456"

    def test_valid_alphanumeric(self):
        result = _validate_identifier("run123", "run_id")
        assert result == "run123"

    def test_strips_whitespace(self):
        result = _validate_identifier("  abc-123  ", "run_id")
        assert result == "abc-123"

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_identifier("", "run_id")

    def test_rejects_whitespace_only(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_identifier("   ", "run_id")

    def test_rejects_forward_slash(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_identifier("../../etc/passwd", "run_id")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_identifier("..\\windows\\system32", "run_id")

    def test_rejects_dot_dot(self):
        with pytest.raises(ValueError, match="must not contain '..'"):
            _validate_identifier("run..123", "run_id")

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="must not exceed 256"):
            _validate_identifier("a" * 300, "run_id")

    def test_rejects_control_characters(self):
        with pytest.raises(ValueError, match="control characters"):
            _validate_identifier("run\x00id", "run_id")

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError, match="control characters"):
            _validate_identifier("abc\x00def", "run_id")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be a string"):
            _validate_identifier(123, "run_id")  # type: ignore[arg-type]

    def test_allows_dots(self):
        result = _validate_identifier("v1.2.3", "version")
        assert result == "v1.2.3"

    def test_allows_underscores(self):
        result = _validate_identifier("my_run_id", "run_id")
        assert result == "my_run_id"


class TestValidateWorkflowName:
    """Tests for _validate_workflow_name."""

    def test_valid_simple_name(self):
        assert _validate_workflow_name("my-workflow") == "my-workflow"

    def test_valid_with_dots(self):
        assert _validate_workflow_name("v1.workflow") == "v1.workflow"

    def test_valid_with_underscores(self):
        assert _validate_workflow_name("my_workflow_v2") == "my_workflow_v2"

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_workflow_name("")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_workflow_name("my workflow")

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_workflow_name("wf@name!")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_workflow_name("../../evil")

    def test_rejects_unicode(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_workflow_name("workflow_\u0159\u00e1d")

    def test_rejects_newline(self):
        with pytest.raises(ValueError, match="control characters"):
            _validate_workflow_name("name\ninjection")

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_workflow_name("name;drop table")


class TestValidateLimit:
    """Tests for _validate_limit."""

    def test_valid_default(self):
        assert _validate_limit(20) == 20

    def test_valid_min(self):
        assert _validate_limit(1) == 1

    def test_valid_max(self):
        assert _validate_limit(_MAX_LIMIT) == _MAX_LIMIT

    def test_rejects_zero(self):
        with pytest.raises(ValueError, match="at least 1"):
            _validate_limit(0)

    def test_rejects_negative(self):
        with pytest.raises(ValueError, match="at least 1"):
            _validate_limit(-5)

    def test_rejects_too_large(self):
        with pytest.raises(ValueError, match="must not exceed"):
            _validate_limit(_MAX_LIMIT + 1)

    def test_rejects_non_int(self):
        with pytest.raises(ValueError, match="must be an integer"):
            _validate_limit(3.14)  # type: ignore[arg-type]


class TestValidateStatus:
    """Tests for _validate_status."""

    def test_empty_string_returns_none(self):
        assert _validate_status("") is None

    def test_whitespace_returns_none(self):
        assert _validate_status("   ") is None

    def test_valid_completed(self):
        assert _validate_status("completed") == "completed"

    def test_case_insensitive(self):
        assert _validate_status("RUNNING") == "running"

    def test_strips_whitespace(self):
        assert _validate_status("  failed  ") == "failed"

    def test_all_valid_statuses(self):
        for s in _VALID_STATUSES:
            assert _validate_status(s) == s

    def test_rejects_invalid_status(self):
        with pytest.raises(ValueError, match="Invalid status"):
            _validate_status("invalid_status")

    def test_rejects_sql_injection_attempt(self):
        with pytest.raises(ValueError, match="Invalid status"):
            _validate_status("completed'; DROP TABLE runs;--")


class TestSafeParseJson:
    """Tests for _safe_parse_json."""

    def test_empty_string_returns_empty_dict(self):
        assert _safe_parse_json("") == {}

    def test_whitespace_returns_empty_dict(self):
        assert _safe_parse_json("   ") == {}

    def test_valid_json_object(self):
        result = _safe_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_empty_json_object(self):
        assert _safe_parse_json("{}") == {}

    def test_nested_json(self):
        result = _safe_parse_json('{"a": {"b": [1, 2]}}')
        assert result == {"a": {"b": [1, 2]}}

    def test_rejects_invalid_json(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _safe_parse_json("{invalid json}")

    def test_rejects_json_array(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            _safe_parse_json("[1, 2, 3]")

    def test_rejects_json_string(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            _safe_parse_json('"just a string"')

    def test_rejects_json_number(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            _safe_parse_json("42")

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="exceeds maximum length"):
            _safe_parse_json("x" * (_MAX_PARAM_LENGTH + 1))

    def test_preserves_unicode_in_values(self):
        result = _safe_parse_json('{"name": "caf\u00e9"}')
        assert result == {"name": "caf\u00e9"}


class TestValidateYamlContent:
    """Tests for _validate_yaml_content."""

    def test_valid_yaml(self):
        result = _validate_yaml_content("name: test\nsteps: []")
        assert result == "name: test\nsteps: []"

    def test_strips_whitespace(self):
        result = _validate_yaml_content("  name: test  ")
        assert result == "name: test"

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_yaml_content("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_yaml_content("   ")

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="exceeds maximum length"):
            _validate_yaml_content("x" * (_MAX_YAML_LENGTH + 1))

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be a string"):
            _validate_yaml_content(123)  # type: ignore[arg-type]


class TestValidateCron:
    """Tests for _validate_cron."""

    def test_valid_5_fields(self):
        assert _validate_cron("0 9 * * *") == "0 9 * * *"

    def test_valid_6_fields(self):
        assert _validate_cron("0 9 * * * 2026") == "0 9 * * * 2026"

    def test_strips_whitespace(self):
        assert _validate_cron("  0 9 * * *  ") == "0 9 * * *"

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_cron("")

    def test_rejects_too_few_fields(self):
        with pytest.raises(ValueError, match="5 or 6 fields"):
            _validate_cron("0 9 *")

    def test_rejects_too_many_fields(self):
        with pytest.raises(ValueError, match="5 or 6 fields"):
            _validate_cron("0 9 * * * * extra")

    def test_rejects_single_field(self):
        with pytest.raises(ValueError, match="5 or 6 fields"):
            _validate_cron("*")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be a string"):
            _validate_cron(123)  # type: ignore[arg-type]


# ===========================================================================
# Test: Serialization helpers
# ===========================================================================


class TestToDict:
    """Tests for _to_dict serialization."""

    def test_none(self):
        assert _to_dict(None) is None

    def test_primitives(self):
        assert _to_dict(42) == 42
        assert _to_dict("hello") == "hello"
        assert _to_dict(True) is True
        assert _to_dict(3.14) == 3.14

    def test_datetime(self):
        dt = datetime(2026, 2, 28, 10, 30, 0)
        assert _to_dict(dt) == "2026-02-28T10:30:00"

    def test_dict(self):
        assert _to_dict({"a": 1, "b": "c"}) == {"a": 1, "b": "c"}

    def test_nested_dict_with_datetime(self):
        result = _to_dict({"ts": datetime(2026, 1, 1), "val": 42})
        assert result["ts"] == "2026-01-01T00:00:00"
        assert result["val"] == 42

    def test_list(self):
        assert _to_dict([1, "a", None]) == [1, "a", None]

    def test_tuple(self):
        assert _to_dict((1, 2, 3)) == [1, 2, 3]

    def test_dataclass(self):
        @dataclass
        class Sample:
            name: str
            value: int

        result = _to_dict(Sample(name="test", value=42))
        assert result == {"name": "test", "value": 42}

    def test_nested_dataclass(self):
        @dataclass
        class Inner:
            x: int

        @dataclass
        class Outer:
            inner: Inner

        result = _to_dict(Outer(inner=Inner(x=5)))
        assert result == {"inner": {"x": 5}}

    def test_sdk_run_with_steps(self):
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

    def test_model_dump_object(self):
        """Objects with model_dump() method (Pydantic models)."""
        obj = MagicMock()
        obj.model_dump.return_value = {"field": "value"}
        # Need to ensure it's not caught by earlier checks
        result = _to_dict(obj)
        assert result == {"field": "value"}

    def test_model_dump_error_falls_back(self):
        """If model_dump() raises, fall back to __dict__."""
        obj = MagicMock(spec=[])
        obj.model_dump = MagicMock(side_effect=RuntimeError("broken"))
        obj.__dict__ = {"fallback": True}
        result = _to_dict(obj)
        # Should use __dict__ fallback
        assert isinstance(result, dict)

    def test_fallback_to_str(self):
        """Objects without __dict__ or other interfaces fall back to str()."""
        result = _to_dict(object())
        assert isinstance(result, str)
        assert "object" in result


class TestToDicts:
    """Tests for _to_dicts normalization."""

    def test_paginated_list(self):
        paginated = PaginatedList(
            items=[RunListItem(run_id="r-1", workflow_name="wf", status="done")],
            total=1,
        )
        result = _to_dicts(paginated)
        assert len(result) == 1
        assert result[0]["run_id"] == "r-1"

    def test_plain_list(self):
        result = _to_dicts([{"x": 1}, {"x": 2}])
        assert len(result) == 2

    def test_empty_paginated(self):
        result = _to_dicts(PaginatedList(items=[], total=0))
        assert result == []

    def test_unexpected_type_returns_empty(self):
        result = _to_dicts("not a list")
        assert result == []

    def test_none_returns_empty(self):
        result = _to_dicts(None)
        assert result == []


class TestJsonResult:
    """Tests for _json_result."""

    def test_dict(self):
        result = json.loads(_json_result({"key": "value"}))
        assert result == {"key": "value"}

    def test_list(self):
        result = json.loads(_json_result([1, 2, 3]))
        assert result == [1, 2, 3]

    def test_none(self):
        result = json.loads(_json_result(None))
        assert result is None

    def test_non_serializable_uses_default_str(self):
        """Non-serializable objects should use str() via default=str."""
        result = _json_result({"dt": datetime(2026, 1, 1)})
        parsed = json.loads(result)
        assert "2026" in parsed["dt"]


class TestErrorResult:
    """Tests for _error_result."""

    def test_sandcastle_error(self):
        err = SandcastleError(404, "NOT_FOUND", "Workflow not found")
        result = json.loads(_error_result(err))
        assert result["error"] is True
        assert result["code"] == "NOT_FOUND"
        assert result["message"] == "Workflow not found"
        assert result["status_code"] == 404

    def test_value_error(self):
        err = ValueError("bad input")
        result = json.loads(_error_result(err))
        assert result["error"] is True
        assert result["code"] == "VALIDATION_ERROR"
        assert result["message"] == "bad input"

    def test_generic_error(self):
        err = RuntimeError("something broke")
        result = json.loads(_error_result(err))
        assert result["error"] is True
        assert result["code"] == "INTERNAL_ERROR"
        assert result["message"] == "something broke"


# ===========================================================================
# Test: _get_client
# ===========================================================================


class TestGetClient:
    """Tests for _get_client factory."""

    def test_default_url(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch("sandcastle.sdk.SandcastleClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            _get_client()
            mock_cls.assert_called_once_with(
                base_url="http://localhost:8080", api_key=""
            )

    def test_custom_url(self):
        with patch.dict("os.environ", {"SANDCASTLE_URL": "https://api.example.com"}, clear=True), \
             patch("sandcastle.sdk.SandcastleClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            _get_client()
            mock_cls.assert_called_once_with(
                base_url="https://api.example.com", api_key=""
            )

    def test_custom_api_key(self):
        with patch.dict(
            "os.environ",
            {"SANDCASTLE_URL": "http://localhost:8080", "SANDCASTLE_API_KEY": "sk-test"},
            clear=True,
        ), patch("sandcastle.sdk.SandcastleClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            _get_client()
            mock_cls.assert_called_once_with(
                base_url="http://localhost:8080", api_key="sk-test"
            )

    def test_rejects_invalid_url_scheme(self):
        with patch.dict("os.environ", {"SANDCASTLE_URL": "ftp://evil.com"}, clear=True):
            with pytest.raises(ValueError, match="must start with http"):
                _get_client()

    def test_rejects_no_scheme(self):
        with patch.dict("os.environ", {"SANDCASTLE_URL": "localhost:8080"}, clear=True):
            with pytest.raises(ValueError, match="must start with http"):
                _get_client()

    def test_rejects_javascript_scheme(self):
        with patch.dict("os.environ", {"SANDCASTLE_URL": "javascript:alert(1)"}, clear=True):
            with pytest.raises(ValueError, match="must start with http"):
                _get_client()


# ===========================================================================
# Test: MCP tool registration
# ===========================================================================


class TestToolRegistration:
    """Verify all tools and resources are properly registered."""

    def test_all_tools_registered(self, mcp_server):
        # Core tools must always be present. v0.31 publishing adds one
        # extra tool per published workflow plus request_llm_completion
        # for sampling, so we check the core subset only.
        tool_names = {t.name for t in mcp_server._tool_manager.list_tools()}
        expected = {
            "run_workflow", "run_workflow_yaml", "get_run_status",
            "cancel_run", "list_runs", "save_workflow",
            "create_schedule", "delete_schedule",
        }
        assert expected.issubset(tool_names)

    def test_tool_count(self, mcp_server):
        # 8 core tools + request_llm_completion (sampling) = 9 baseline.
        # Per-workflow tools come on top when workflows exist on disk.
        tools = list(mcp_server._tool_manager.list_tools())
        assert len(tools) >= 9

    def test_all_resources_registered(self, mcp_server):
        resources = list(mcp_server._resource_manager.list_resources())
        uris = {str(r.uri) for r in resources}
        expected = {
            "sandcastle://workflows",
            "sandcastle://schedules",
            "sandcastle://health",
        }
        assert expected.issubset(uris)

    def test_resource_count(self, mcp_server):
        # Core 3 plus sandcastle://roots and sandcastle://manifest.
        resources = list(mcp_server._resource_manager.list_resources())
        assert len(resources) >= 5

    def test_tools_have_descriptions(self, mcp_server):
        """All tools should have non-empty descriptions."""
        for tool in mcp_server._tool_manager.list_tools():
            assert tool.description, f"Tool '{tool.name}' has no description"

    def test_tools_have_parameter_schemas(self, mcp_server):
        """All tools should have parameter schemas defined."""
        for tool in mcp_server._tool_manager.list_tools():
            assert tool.parameters is not None, (
                f"Tool '{tool.name}' has no parameter schema"
            )

    def test_run_workflow_parameters(self, mcp_server):
        """Check run_workflow has correct required/optional params."""
        tool = _get_tool(mcp_server, "run_workflow")
        params = tool.parameters
        assert "workflow_name" in params["required"]
        # input_data and wait are optional (have defaults)
        assert "input_data" in params["properties"]
        assert "wait" in params["properties"]

    def test_list_runs_parameters(self, mcp_server):
        """Check list_runs parameters are all optional."""
        tool = _get_tool(mcp_server, "list_runs")
        params = tool.parameters
        # All params have defaults, so required might be empty
        assert "status" in params["properties"]
        assert "workflow" in params["properties"]
        assert "limit" in params["properties"]


# ===========================================================================
# Test: Tool calls with valid inputs (mocked client)
# ===========================================================================


@pytest.mark.usefixtures("_patch_client")
class TestToolCallsValid:
    """Test each tool with valid inputs and mocked SDK client."""

    def test_run_workflow_success(self, mock_client, mcp_server):
        mock_client.run.return_value = Run(
            run_id="abc-123", status="queued", workflow_name="test-wf",
        )
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="test-wf",
            input_data='{"key": "value"}',
            wait=False,
        ))
        assert result["run_id"] == "abc-123"
        assert result["status"] == "queued"
        mock_client.run.assert_called_once_with(
            "test-wf", input={"key": "value"}, wait=False,
        )
        mock_client.close.assert_called_once()

    def test_run_workflow_empty_input(self, mock_client, mcp_server):
        mock_client.run.return_value = Run(
            run_id="r-1", status="queued", workflow_name="wf",
        )
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow", workflow_name="wf",
        ))
        assert result["run_id"] == "r-1"
        mock_client.run.assert_called_once_with("wf", input={}, wait=False)

    def test_run_workflow_yaml_success(self, mock_client, mcp_server):
        yaml = "name: test\nsteps:\n  - id: step1\n    model: sonnet"
        mock_client.run_yaml.return_value = Run(
            run_id="yaml-1", status="queued", workflow_name="inline",
        )
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow_yaml",
            yaml_content=yaml,
            input_data='{"prompt": "hello"}',
        ))
        assert result["run_id"] == "yaml-1"
        mock_client.run_yaml.assert_called_once()

    def test_get_run_status_success(self, mock_client, mcp_server):
        mock_client.get_run.return_value = Run(
            run_id="r-abc", status="completed",
            steps=[
                Step(step_id="s1", status="completed", cost_usd=0.05),
                Step(step_id="s2", status="completed", cost_usd=0.03),
            ],
        )
        result = _parse_result(_call_tool(
            mcp_server, "get_run_status", run_id="r-abc",
        ))
        assert result["status"] == "completed"
        assert len(result["steps"]) == 2
        mock_client.get_run.assert_called_once_with("r-abc")

    def test_cancel_run_success(self, mock_client, mcp_server):
        mock_client.cancel_run.return_value = {"cancelled": True, "run_id": "r-1"}
        result = _parse_result(_call_tool(
            mcp_server, "cancel_run", run_id="r-1",
        ))
        assert result["cancelled"] is True
        mock_client.cancel_run.assert_called_once_with("r-1")

    def test_list_runs_no_filters(self, mock_client, mcp_server):
        mock_client.list_runs.return_value = PaginatedList(
            items=[
                RunListItem(run_id="r-1", workflow_name="wf", status="completed"),
                RunListItem(run_id="r-2", workflow_name="wf", status="failed"),
            ],
            total=2,
        )
        result = _parse_result(_call_tool(mcp_server, "list_runs"))
        assert isinstance(result, list)
        assert len(result) == 2

    def test_list_runs_with_status_filter(self, mock_client, mcp_server):
        mock_client.list_runs.return_value = PaginatedList(items=[], total=0)
        _call_tool(mcp_server, "list_runs", status="completed")
        mock_client.list_runs.assert_called_once_with(
            status="completed", workflow=None, limit=20,
        )

    def test_list_runs_with_workflow_filter(self, mock_client, mcp_server):
        mock_client.list_runs.return_value = PaginatedList(items=[], total=0)
        _call_tool(mcp_server, "list_runs", workflow="my-wf")
        mock_client.list_runs.assert_called_once_with(
            status=None, workflow="my-wf", limit=20,
        )

    def test_list_runs_with_custom_limit(self, mock_client, mcp_server):
        mock_client.list_runs.return_value = PaginatedList(items=[], total=0)
        _call_tool(mcp_server, "list_runs", limit=50)
        mock_client.list_runs.assert_called_once_with(
            status=None, workflow=None, limit=50,
        )

    def test_save_workflow_success(self, mock_client, mcp_server):
        mock_client.save_workflow.return_value = Workflow(
            name="my-wf", description="A test", steps_count=3,
            file_name="my-wf.yaml",
        )
        result = _parse_result(_call_tool(
            mcp_server, "save_workflow",
            name="my-wf",
            yaml_content="name: my-wf\nsteps: []",
        ))
        assert result["name"] == "my-wf"
        assert result["steps_count"] == 3

    def test_create_schedule_success(self, mock_client, mcp_server):
        mock_client.create_schedule.return_value = Schedule(
            id="sched-1", workflow_name="daily-wf",
            cron_expression="0 9 * * *",
        )
        result = _parse_result(_call_tool(
            mcp_server, "create_schedule",
            workflow_name="daily-wf",
            cron="0 9 * * *",
            input_data='{"key": "val"}',
        ))
        assert result["id"] == "sched-1"
        assert result["cron_expression"] == "0 9 * * *"

    def test_create_schedule_empty_input(self, mock_client, mcp_server):
        mock_client.create_schedule.return_value = Schedule(
            id="sched-2", workflow_name="wf",
            cron_expression="*/5 * * * *",
        )
        result = _parse_result(_call_tool(
            mcp_server, "create_schedule",
            workflow_name="wf",
            cron="*/5 * * * *",
        ))
        assert result["id"] == "sched-2"

    def test_delete_schedule_success(self, mock_client, mcp_server):
        mock_client.delete_schedule.return_value = {"deleted": True, "id": "sched-1"}
        result = _parse_result(_call_tool(
            mcp_server, "delete_schedule", schedule_id="sched-1",
        ))
        assert result["deleted"] is True
        mock_client.delete_schedule.assert_called_once_with("sched-1")


# ===========================================================================
# Test: Tool calls with invalid inputs (validation layer)
# ===========================================================================


@pytest.mark.usefixtures("_patch_client")
class TestToolCallsInvalidInput:
    """Test each tool rejects invalid inputs with proper error responses."""

    def test_run_workflow_empty_name(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow", workflow_name="",
        ))
        assert result["error"] is True
        assert result["code"] == "VALIDATION_ERROR"
        # Should NOT call the client
        mock_client.run.assert_not_called()

    def test_run_workflow_path_traversal_name(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="../../etc/passwd",
        ))
        assert result["error"] is True
        mock_client.run.assert_not_called()

    def test_run_workflow_invalid_json_input(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="valid-wf",
            input_data="not json",
        ))
        assert result["error"] is True
        assert "not valid JSON" in result["message"]
        mock_client.run.assert_not_called()

    def test_run_workflow_json_array_input(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="valid-wf",
            input_data="[1, 2, 3]",
        ))
        assert result["error"] is True
        assert "JSON object" in result["message"]

    def test_run_workflow_name_with_spaces(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="my workflow name",
        ))
        assert result["error"] is True
        mock_client.run.assert_not_called()

    def test_run_workflow_yaml_empty_content(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow_yaml",
            yaml_content="",
        ))
        assert result["error"] is True
        assert "empty" in result["message"].lower()
        mock_client.run_yaml.assert_not_called()

    def test_run_workflow_yaml_oversized(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow_yaml",
            yaml_content="x" * (_MAX_YAML_LENGTH + 1),
        ))
        assert result["error"] is True
        assert "exceeds" in result["message"].lower()

    def test_get_run_status_empty_id(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "get_run_status", run_id="",
        ))
        assert result["error"] is True
        mock_client.get_run.assert_not_called()

    def test_get_run_status_path_traversal(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "get_run_status", run_id="../../secret",
        ))
        assert result["error"] is True
        mock_client.get_run.assert_not_called()

    def test_get_run_status_null_byte(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "get_run_status", run_id="run\x00id",
        ))
        assert result["error"] is True

    def test_cancel_run_empty_id(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "cancel_run", run_id="",
        ))
        assert result["error"] is True
        mock_client.cancel_run.assert_not_called()

    def test_cancel_run_dot_dot(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "cancel_run", run_id="run..id",
        ))
        assert result["error"] is True

    def test_list_runs_invalid_status(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "list_runs", status="invalid_status",
        ))
        assert result["error"] is True
        assert "Invalid status" in result["message"]
        mock_client.list_runs.assert_not_called()

    def test_list_runs_negative_limit(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "list_runs", limit=-1,
        ))
        assert result["error"] is True
        assert "at least 1" in result["message"]

    def test_list_runs_zero_limit(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "list_runs", limit=0,
        ))
        assert result["error"] is True

    def test_list_runs_excessive_limit(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "list_runs", limit=999999,
        ))
        assert result["error"] is True
        assert "exceed" in result["message"].lower()

    def test_list_runs_invalid_workflow_name(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "list_runs", workflow="bad name!@#",
        ))
        assert result["error"] is True

    def test_save_workflow_empty_name(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "save_workflow",
            name="",
            yaml_content="name: test\nsteps: []",
        ))
        assert result["error"] is True
        mock_client.save_workflow.assert_not_called()

    def test_save_workflow_empty_yaml(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "save_workflow",
            name="valid-wf",
            yaml_content="",
        ))
        assert result["error"] is True

    def test_save_workflow_path_traversal_name(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "save_workflow",
            name="../../../evil",
            yaml_content="name: evil\nsteps: []",
        ))
        assert result["error"] is True

    def test_create_schedule_empty_name(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "create_schedule",
            workflow_name="",
            cron="0 9 * * *",
        ))
        assert result["error"] is True
        mock_client.create_schedule.assert_not_called()

    def test_create_schedule_empty_cron(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "create_schedule",
            workflow_name="valid-wf",
            cron="",
        ))
        assert result["error"] is True

    def test_create_schedule_invalid_cron_fields(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "create_schedule",
            workflow_name="valid-wf",
            cron="* * *",
        ))
        assert result["error"] is True
        assert "5 or 6 fields" in result["message"]

    def test_create_schedule_invalid_json_input(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "create_schedule",
            workflow_name="valid-wf",
            cron="0 9 * * *",
            input_data="{broken}",
        ))
        assert result["error"] is True
        assert "JSON" in result["message"]

    def test_delete_schedule_empty_id(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "delete_schedule", schedule_id="",
        ))
        assert result["error"] is True
        mock_client.delete_schedule.assert_not_called()

    def test_delete_schedule_path_traversal(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "delete_schedule",
            schedule_id="../../secret",
        ))
        assert result["error"] is True


# ===========================================================================
# Test: Error handling (SDK/network errors caught)
# ===========================================================================


@pytest.mark.usefixtures("_patch_client")
class TestToolErrorHandling:
    """Test that SDK errors are caught and returned as error responses."""

    def test_run_workflow_api_error(self, mock_client, mcp_server):
        mock_client.run.side_effect = SandcastleError(
            404, "NOT_FOUND", "Workflow not found"
        )
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow", workflow_name="nonexistent",
        ))
        assert result["error"] is True
        assert result["code"] == "NOT_FOUND"
        assert result["status_code"] == 404
        # Client should still be closed
        mock_client.close.assert_called_once()

    def test_run_workflow_network_error(self, mock_client, mcp_server):
        mock_client.run.side_effect = ConnectionError("Connection refused")
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow", workflow_name="test-wf",
        ))
        assert result["error"] is True
        assert result["code"] == "INTERNAL_ERROR"
        assert "Connection refused" in result["message"]

    def test_get_run_status_api_error(self, mock_client, mcp_server):
        mock_client.get_run.side_effect = SandcastleError(
            500, "INTERNAL_ERROR", "Server error"
        )
        result = _parse_result(_call_tool(
            mcp_server, "get_run_status", run_id="valid-id",
        ))
        assert result["error"] is True
        assert result["status_code"] == 500

    def test_cancel_run_api_error(self, mock_client, mcp_server):
        mock_client.cancel_run.side_effect = SandcastleError(
            409, "CONFLICT", "Run already completed"
        )
        result = _parse_result(_call_tool(
            mcp_server, "cancel_run", run_id="valid-id",
        ))
        assert result["error"] is True
        assert result["code"] == "CONFLICT"

    def test_list_runs_api_error(self, mock_client, mcp_server):
        mock_client.list_runs.side_effect = SandcastleError(
            401, "UNAUTHORIZED", "Invalid API key"
        )
        result = _parse_result(_call_tool(
            mcp_server, "list_runs",
        ))
        assert result["error"] is True
        assert result["code"] == "UNAUTHORIZED"

    def test_save_workflow_api_error(self, mock_client, mcp_server):
        mock_client.save_workflow.side_effect = SandcastleError(
            400, "INVALID_YAML", "Invalid workflow definition"
        )
        result = _parse_result(_call_tool(
            mcp_server, "save_workflow",
            name="bad-wf",
            yaml_content="invalid: yaml: content",
        ))
        assert result["error"] is True
        assert result["code"] == "INVALID_YAML"

    def test_create_schedule_api_error(self, mock_client, mcp_server):
        mock_client.create_schedule.side_effect = SandcastleError(
            404, "NOT_FOUND", "Workflow not found"
        )
        result = _parse_result(_call_tool(
            mcp_server, "create_schedule",
            workflow_name="nonexistent",
            cron="0 9 * * *",
        ))
        assert result["error"] is True

    def test_delete_schedule_api_error(self, mock_client, mcp_server):
        mock_client.delete_schedule.side_effect = SandcastleError(
            404, "NOT_FOUND", "Schedule not found"
        )
        result = _parse_result(_call_tool(
            mcp_server, "delete_schedule", schedule_id="nonexistent",
        ))
        assert result["error"] is True

    def test_run_workflow_yaml_api_error(self, mock_client, mcp_server):
        mock_client.run_yaml.side_effect = SandcastleError(
            400, "INVALID_YAML", "Parse error"
        )
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow_yaml",
            yaml_content="name: test\nsteps: []",
        ))
        assert result["error"] is True
        assert result["code"] == "INVALID_YAML"

    def test_client_always_closed_on_error(self, mock_client, mcp_server):
        """Ensure the client is closed even when the API raises."""
        mock_client.run.side_effect = RuntimeError("unexpected")
        _call_tool(mcp_server, "run_workflow", workflow_name="test-wf")
        mock_client.close.assert_called_once()

    def test_timeout_error_handling(self, mock_client, mcp_server):
        mock_client.run.side_effect = TimeoutError("Request timed out")
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="slow-wf",
            wait=True,
        ))
        assert result["error"] is True
        assert "timed out" in result["message"].lower()


# ===========================================================================
# Test: Security edge cases
# ===========================================================================


@pytest.mark.usefixtures("_patch_client")
class TestSecurityEdgeCases:
    """Test security-relevant edge cases - injection, traversal, etc."""

    def test_workflow_name_shell_injection(self, mock_client, mcp_server):
        """Workflow names with shell metacharacters should be rejected."""
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="$(whoami)",
        ))
        assert result["error"] is True
        mock_client.run.assert_not_called()

    def test_workflow_name_command_injection(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="test; rm -rf /",
        ))
        assert result["error"] is True
        mock_client.run.assert_not_called()

    def test_run_id_with_null_bytes(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "get_run_status",
            run_id="valid\x00hidden",
        ))
        assert result["error"] is True

    def test_workflow_name_url_encoded_traversal(self, mock_client, mcp_server):
        """URL-encoded path traversal in workflow name."""
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="..%2F..%2Fetc%2Fpasswd",
        ))
        assert result["error"] is True

    def test_input_data_oversized(self, mock_client, mcp_server):
        """Oversized JSON input data should be rejected."""
        huge_input = json.dumps({"data": "x" * (_MAX_PARAM_LENGTH + 1)})
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="test-wf",
            input_data=huge_input,
        ))
        assert result["error"] is True
        assert "exceeds" in result["message"].lower()

    def test_yaml_content_oversized(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "save_workflow",
            name="oversized",
            yaml_content="x" * (_MAX_YAML_LENGTH + 1),
        ))
        assert result["error"] is True

    def test_cron_injection_attempt(self, mock_client, mcp_server):
        """Cron expression with injection attempt."""
        result = _parse_result(_call_tool(
            mcp_server, "create_schedule",
            workflow_name="test-wf",
            cron="* * * * * && rm -rf /",
        ))
        # Should be rejected because of 7 fields
        assert result["error"] is True

    def test_schedule_id_with_path_separator(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "delete_schedule",
            schedule_id="sched/../../../etc/passwd",
        ))
        assert result["error"] is True

    def test_list_runs_status_sql_injection(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "list_runs",
            status="'; DROP TABLE runs; --",
        ))
        assert result["error"] is True

    def test_workflow_name_backslash_traversal(self, mock_client, mcp_server):
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="..\\..\\windows\\system32",
        ))
        assert result["error"] is True


# ===========================================================================
# Test: Resource calls
# ===========================================================================


@pytest.mark.usefixtures("_patch_client")
class TestResourceCalls:
    """Test MCP resource functions with mocked client."""

    def test_resource_workflows_success(self, mock_client):
        from sandcastle.mcp_server import create_mcp_server

        mock_client.list_workflows.return_value = [
            Workflow(name="wf-1", description="First", steps_count=2, file_name="wf-1.yaml"),
        ]
        server = create_mcp_server()
        # Access resource function directly
        resource_fns = {}
        for r in server._resource_manager.list_resources():
            resource_fns[str(r.uri)] = r
        # Call the function through the server internals
        with patch("sandcastle.mcp_server._get_client", return_value=mock_client):
            # Get the function registered for this resource
            from sandcastle.mcp_server import _get_client, _json_result, _to_dict
            client = _get_client()
            workflows = client.list_workflows()
            result = json.loads(_json_result([_to_dict(w) for w in workflows]))
        assert len(result) == 1
        assert result[0]["name"] == "wf-1"

    def test_resource_workflows_error(self, mock_client):
        mock_client.list_workflows.side_effect = SandcastleError(
            500, "SERVER_ERROR", "DB down"
        )
        from sandcastle.mcp_server import create_mcp_server
        server = create_mcp_server()
        with patch("sandcastle.mcp_server._get_client", return_value=mock_client):
            from sandcastle.mcp_server import _error_result, _get_client
            client = _get_client()
            try:
                client.list_workflows()
                result_str = "[]"  # unreachable
            except Exception as exc:
                result_str = _error_result(exc)
            result = json.loads(result_str)
        assert result["error"] is True

    def test_resource_schedules_success(self, mock_client):
        mock_client.list_schedules.return_value = PaginatedList(
            items=[
                Schedule(id="s-1", workflow_name="wf", cron_expression="0 * * * *"),
            ],
            total=1,
        )
        with patch("sandcastle.mcp_server._get_client", return_value=mock_client):
            from sandcastle.mcp_server import _get_client, _json_result, _to_dicts
            client = _get_client()
            schedules = client.list_schedules()
            result = json.loads(_json_result(_to_dicts(schedules)))
        assert len(result) == 1
        assert result[0]["id"] == "s-1"

    def test_resource_health_success(self, mock_client):
        mock_client.health.return_value = HealthStatus(
            status="ok", runtime=True, database=True, redis=True,
        )
        with patch("sandcastle.mcp_server._get_client", return_value=mock_client):
            from sandcastle.mcp_server import _get_client, _json_result, _to_dict
            client = _get_client()
            health = client.health()
            result = json.loads(_json_result(_to_dict(health)))
        assert result["status"] == "ok"
        assert result["database"] is True


# ===========================================================================
# Test: CLI integration
# ===========================================================================


class TestMcpCliIntegration:
    """Test CLI parser and _cmd_mcp integration."""

    def test_parser_accepts_mcp(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["mcp"])
        assert args.command == "mcp"

    def test_parser_mcp_with_url(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["mcp", "--url", "http://example.com:9090"])
        assert args.url == "http://example.com:9090"

    def test_parser_mcp_with_api_key(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["mcp", "--api-key", "sk-test-123"])
        assert args.api_key == "sk-test-123"

    def test_parser_mcp_with_both_args(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args([
            "mcp", "--url", "https://remote:8080", "--api-key", "key123",
        ])
        assert args.url == "https://remote:8080"
        assert args.api_key == "key123"

    def test_cmd_mcp_sets_env_vars(self):
        """_cmd_mcp propagates --url and --api-key to env vars."""
        from sandcastle.__main__ import _cmd_mcp

        args = MagicMock()
        args.url = "http://test:1234"
        args.api_key = "test-key"

        import os
        with patch("sandcastle.mcp_server.main") as mock_main, \
             patch.dict("os.environ", {}, clear=False):
            _cmd_mcp(args)
            assert os.environ["SANDCASTLE_URL"] == "http://test:1234"
            assert os.environ["SANDCASTLE_API_KEY"] == "test-key"
            mock_main.assert_called_once()

    def test_cmd_mcp_no_url_does_not_set_env(self):
        """When --url is not given, SANDCASTLE_URL should not be set."""
        from sandcastle.__main__ import _cmd_mcp

        args = MagicMock()
        args.url = None
        args.api_key = None

        import os
        with patch("sandcastle.mcp_server.main") as mock_main, \
             patch.dict("os.environ", {}, clear=True):
            _cmd_mcp(args)
            assert "SANDCASTLE_URL" not in os.environ
            assert "SANDCASTLE_API_KEY" not in os.environ
            mock_main.assert_called_once()

    def test_cmd_mcp_import_error(self):
        """If mcp package is not installed, _cmd_mcp exits gracefully."""
        from sandcastle.__main__ import _cmd_mcp

        args = MagicMock()
        args.url = None
        args.api_key = None

        with patch.dict("sys.modules", {"sandcastle.mcp_server": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module mcp")):
                with pytest.raises(SystemExit):
                    _cmd_mcp(args)


# ===========================================================================
# Test: Edge cases and misc
# ===========================================================================


@pytest.mark.usefixtures("_patch_client")
class TestEdgeCases:
    """Miscellaneous edge cases."""

    def test_run_workflow_wait_true(self, mock_client, mcp_server):
        """wait=True should be passed through to SDK."""
        mock_client.run.return_value = Run(
            run_id="r-wait", status="completed", workflow_name="wf",
        )
        _call_tool(
            mcp_server, "run_workflow",
            workflow_name="test-wf",
            wait=True,
        )
        mock_client.run.assert_called_once_with(
            "test-wf", input={}, wait=True,
        )

    def test_run_workflow_yaml_wait_true(self, mock_client, mcp_server):
        mock_client.run_yaml.return_value = Run(
            run_id="r-wait", status="completed", workflow_name="inline",
        )
        _call_tool(
            mcp_server, "run_workflow_yaml",
            yaml_content="name: test\nsteps: []",
            wait=True,
        )
        mock_client.run_yaml.assert_called_once()
        _, kwargs = mock_client.run_yaml.call_args
        assert kwargs.get("wait") is True or mock_client.run_yaml.call_args[1].get("wait") is True

    def test_list_runs_empty_status_and_workflow(self, mock_client, mcp_server):
        """Empty strings for status and workflow should result in None filters."""
        mock_client.list_runs.return_value = PaginatedList(items=[], total=0)
        _call_tool(mcp_server, "list_runs", status="", workflow="")
        mock_client.list_runs.assert_called_once_with(
            status=None, workflow=None, limit=20,
        )

    def test_create_schedule_empty_input_passes_none(self, mock_client, mcp_server):
        """Empty input_data should pass None to create_schedule."""
        mock_client.create_schedule.return_value = Schedule(
            id="s-1", workflow_name="wf", cron_expression="0 9 * * *",
        )
        _call_tool(
            mcp_server, "create_schedule",
            workflow_name="wf",
            cron="0 9 * * *",
            input_data="",
        )
        # Empty dict is falsy, so input=None
        mock_client.create_schedule.assert_called_once_with(
            "wf", "0 9 * * *", input=None,
        )

    def test_multiple_server_instances_independent(self):
        """Creating multiple MCP servers should not interfere."""
        server1 = create_mcp_server()
        server2 = create_mcp_server()
        tools1 = {t.name for t in server1._tool_manager.list_tools()}
        tools2 = {t.name for t in server2._tool_manager.list_tools()}
        assert tools1 == tools2

    def test_main_function_exists(self):
        from sandcastle.mcp_server import main
        assert callable(main)

    def test_main_calls_run(self):
        from sandcastle.mcp_server import main

        with patch("sandcastle.mcp_server.create_mcp_server") as mock_create:
            mock_server = MagicMock()
            mock_create.return_value = mock_server
            main()
            mock_server.run.assert_called_once_with(transport="stdio")

    def test_json_result_formatting(self):
        """Verify JSON output is indented for readability."""
        result = _json_result({"a": 1, "b": 2})
        assert "\n" in result  # Indented output has newlines
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_run_workflow_with_complex_nested_input(self, mock_client, mcp_server):
        """Complex nested JSON input should be passed through correctly."""
        mock_client.run.return_value = Run(
            run_id="r-complex", status="queued", workflow_name="wf",
        )
        complex_input = json.dumps({
            "urls": ["https://a.com", "https://b.com"],
            "config": {"depth": 3, "timeout": 30},
            "tags": ["prod", "v2"],
        })
        result = _parse_result(_call_tool(
            mcp_server, "run_workflow",
            workflow_name="test-wf",
            input_data=complex_input,
        ))
        assert result["run_id"] == "r-complex"
        call_args = mock_client.run.call_args
        assert call_args[1]["input"]["urls"] == ["https://a.com", "https://b.com"]

    def test_to_dict_with_sdk_schedule(self):
        """Ensure Schedule dataclass serializes correctly."""
        sched = Schedule(
            id="s-1",
            workflow_name="daily",
            cron_expression="0 9 * * *",
            input_data={"key": "val"},
            enabled=True,
            created_at=datetime(2026, 2, 28, 10, 0),
        )
        result = _to_dict(sched)
        assert result["id"] == "s-1"
        assert result["cron_expression"] == "0 9 * * *"
        assert result["created_at"] == "2026-02-28T10:00:00"

    def test_to_dict_with_sdk_health_status(self):
        health = HealthStatus(status="ok", runtime=True, database=True, redis=None)
        result = _to_dict(health)
        assert result["status"] == "ok"
        assert result["redis"] is None

    def test_to_dict_with_sdk_workflow(self):
        wf = Workflow(name="wf", description="desc", steps_count=5, file_name="wf.yaml")
        result = _to_dict(wf)
        assert result["name"] == "wf"
        assert result["steps_count"] == 5

    def test_error_result_preserves_all_sandcastle_fields(self):
        """SandcastleError response should include status_code, code, message."""
        err = SandcastleError(429, "RATE_LIMITED", "Too many requests")
        result = json.loads(_error_result(err))
        assert result["error"] is True
        assert result["status_code"] == 429
        assert result["code"] == "RATE_LIMITED"
        assert result["message"] == "Too many requests"

    def test_validate_identifier_allows_uuid_format(self):
        """Standard UUID format should be valid."""
        uuid_val = "550e8400-e29b-41d4-a716-446655440000"
        assert _validate_identifier(uuid_val, "id") == uuid_val

    def test_validate_workflow_name_allows_version_suffix(self):
        """Workflow names like 'my-workflow-v2.1' should be valid."""
        assert _validate_workflow_name("my-workflow-v2.1") == "my-workflow-v2.1"

    def test_safe_parse_json_with_null_string(self):
        """The string 'null' is valid JSON but not a dict."""
        with pytest.raises(ValueError, match="must be a JSON object"):
            _safe_parse_json("null")

    def test_safe_parse_json_with_boolean(self):
        """The string 'true' is valid JSON but not a dict."""
        with pytest.raises(ValueError, match="must be a JSON object"):
            _safe_parse_json("true")
