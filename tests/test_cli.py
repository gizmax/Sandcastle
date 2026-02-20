"""Tests for the Sandcastle CLI tool (argument parsing and command handlers)."""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.__main__ import (
    _build_parser,
    _cmd_health,
    _cmd_serve,
    _parse_input_pairs,
)

# ---------------------------------------------------------------------------
# Tests: Argument parsing
# ---------------------------------------------------------------------------


class TestArgParsing:
    def test_serve_defaults(self):
        """'serve' command should have correct default host, port, and reload."""
        parser = _build_parser()
        args = parser.parse_args(["serve"])

        assert args.command == "serve"
        assert args.host == "0.0.0.0"
        assert args.port == 8080
        assert args.reload is True

    def test_serve_custom_port(self):
        """'serve --port 9090' should set port to 9090."""
        parser = _build_parser()
        args = parser.parse_args(["serve", "--port", "9090"])

        assert args.port == 9090

    def test_serve_no_reload(self):
        """'serve --no-reload' should set reload to False."""
        parser = _build_parser()
        args = parser.parse_args(["serve", "--no-reload"])

        assert args.reload is False

    def test_run_command_parses_workflow(self):
        """'run my-workflow' should set the workflow argument."""
        parser = _build_parser()
        args = parser.parse_args(["run", "my-workflow"])

        assert args.command == "run"
        assert args.workflow == "my-workflow"

    def test_run_command_with_input(self):
        """'run wf -i key=value' should capture input pairs."""
        parser = _build_parser()
        args = parser.parse_args(["run", "wf", "-i", "name=test", "-i", "count=5"])

        assert args.input == ["name=test", "count=5"]

    def test_run_command_with_wait(self):
        """'run wf --wait' should set wait to True."""
        parser = _build_parser()
        args = parser.parse_args(["run", "wf", "--wait"])

        assert args.wait is True

    def test_run_command_with_max_cost(self):
        """'run wf --max-cost 5.0' should set max_cost."""
        parser = _build_parser()
        args = parser.parse_args(["run", "wf", "--max-cost", "5.0"])

        assert args.max_cost == 5.0

    def test_status_command(self):
        """'status <run_id>' should parse the run_id argument."""
        parser = _build_parser()
        args = parser.parse_args(["status", "abc-123"])

        assert args.command == "status"
        assert args.run_id == "abc-123"

    def test_cancel_command(self):
        """'cancel <run_id>' should parse the run_id argument."""
        parser = _build_parser()
        args = parser.parse_args(["cancel", "run-456"])

        assert args.command == "cancel"
        assert args.run_id == "run-456"

    def test_health_command(self):
        """'health' command should parse correctly."""
        parser = _build_parser()
        args = parser.parse_args(["health"])

        assert args.command == "health"

    def test_ls_runs_command(self):
        """'ls runs' should parse the resource as 'runs'."""
        parser = _build_parser()
        args = parser.parse_args(["ls", "runs"])

        assert args.command == "ls"
        assert args.resource == "runs"

    def test_ls_runs_with_status_filter(self):
        """'ls runs --status completed' should set the status filter."""
        parser = _build_parser()
        args = parser.parse_args(["ls", "runs", "--status", "completed"])

        assert args.status == "completed"

    def test_ls_workflows_command(self):
        """'ls workflows' should parse the resource as 'workflows'."""
        parser = _build_parser()
        args = parser.parse_args(["ls", "workflows"])

        assert args.resource == "workflows"

    def test_ls_schedules_command(self):
        """'ls schedules' should parse the resource as 'schedules'."""
        parser = _build_parser()
        args = parser.parse_args(["ls", "schedules"])

        assert args.resource == "schedules"

    def test_schedule_create_command(self):
        """'schedule create wf cron' should parse workflow and cron."""
        parser = _build_parser()
        args = parser.parse_args(["schedule", "create", "daily-wf", "0 9 * * *"])

        assert args.command == "schedule"
        assert args.schedule_action == "create"
        assert args.workflow == "daily-wf"
        assert args.cron == "0 9 * * *"

    def test_schedule_delete_command(self):
        """'schedule delete <id>' should parse the schedule ID."""
        parser = _build_parser()
        args = parser.parse_args(["schedule", "delete", "sched-789"])

        assert args.command == "schedule"
        assert args.schedule_action == "delete"
        assert args.id == "sched-789"

    def test_connection_args_on_health(self):
        """'health --url http://x --api-key sk-y' should set url and api_key."""
        parser = _build_parser()
        args = parser.parse_args(["health", "--url", "http://remote:9090", "--api-key", "sk-test"])

        assert args.url == "http://remote:9090"
        assert args.api_key == "sk-test"

    def test_no_command_sets_none(self):
        """No command argument should result in args.command being None."""
        parser = _build_parser()
        args = parser.parse_args([])

        assert args.command is None

    def test_db_migrate_command(self):
        """'db migrate' should parse correctly."""
        parser = _build_parser()
        args = parser.parse_args(["db", "migrate"])

        assert args.command == "db"
        assert args.db_action == "migrate"


# ---------------------------------------------------------------------------
# Tests: Input parsing
# ---------------------------------------------------------------------------


class TestInputParsing:
    def test_parse_key_value_pairs(self):
        """_parse_input_pairs should convert KEY=VALUE strings to a dict."""
        result = _parse_input_pairs(["name=Alice", "age=30"])
        assert result["name"] == "Alice"
        assert result["age"] == 30  # Should be parsed as JSON int

    def test_parse_json_values(self):
        """Values that look like JSON should be parsed as JSON."""
        result = _parse_input_pairs(['items=["a","b","c"]', "flag=true"])
        assert result["items"] == ["a", "b", "c"]
        assert result["flag"] is True

    def test_parse_none_input(self):
        """None input should return empty dict."""
        result = _parse_input_pairs(None)
        assert result == {}

    def test_parse_empty_list(self):
        """Empty list should return empty dict."""
        result = _parse_input_pairs([])
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: Health command
# ---------------------------------------------------------------------------


class _FakeHealth:
    """Simple health object that works with _to_dict (has __dict__)."""

    def __init__(self, status, runtime, database, redis=None):
        self.status = status
        self.runtime = runtime
        self.database = database
        self.redis = redis


class TestHealthCommand:
    def test_health_command_prints_status(self):
        """health command should print status information from the SDK."""
        mock_health = _FakeHealth(status="ok", runtime=True, database=True, redis=None)

        mock_client = MagicMock()
        mock_client.health.return_value = mock_health

        captured = StringIO()
        parser = _build_parser()
        args = parser.parse_args(["health"])

        with (
            patch("sandcastle.__main__._get_client", return_value=mock_client),
            patch("sys.stdout", captured),
        ):
            _cmd_health(args)

        output = captured.getvalue()
        assert "ok" in output or "Status" in output
        mock_client.health.assert_called_once()

    def test_health_command_exits_on_error(self):
        """health command should exit with code 1 when the API is unreachable."""
        mock_client = MagicMock()
        mock_client.health.side_effect = Exception("Connection refused")

        parser = _build_parser()
        args = parser.parse_args(["health"])

        with (
            patch("sandcastle.__main__._get_client", return_value=mock_client),
            pytest.raises(SystemExit) as exc_info,
        ):
            _cmd_health(args)

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Tests: Serve command
# ---------------------------------------------------------------------------


class TestServeCommand:
    def test_serve_calls_uvicorn_with_correct_args(self):
        """serve command should call uvicorn.run with host, port, and reload."""
        parser = _build_parser()
        args = parser.parse_args(["serve", "--host", "127.0.0.1", "--port", "9000", "--no-reload"])

        with patch("uvicorn.run") as mock_run:
            _cmd_serve(args)

        mock_run.assert_called_once_with(
            "sandcastle.main:app",
            host="127.0.0.1",
            port=9000,
            reload=False,
        )

    def test_serve_default_args(self):
        """serve command with defaults should call uvicorn with 0.0.0.0:8080 and reload=True."""
        parser = _build_parser()
        args = parser.parse_args(["serve"])

        with patch("uvicorn.run") as mock_run:
            _cmd_serve(args)

        mock_run.assert_called_once_with(
            "sandcastle.main:app",
            host="0.0.0.0",
            port=8080,
            reload=True,
        )
