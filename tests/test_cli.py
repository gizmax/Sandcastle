"""Tests for the Sandcastle CLI tool (argument parsing and command handlers)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.__main__ import (
    _build_parser,
    _cmd_approve,
    _cmd_fork,
    _cmd_health,
    _cmd_reject,
    _cmd_replay,
    _cmd_runs,
    _cmd_serve,
    _cmd_templates,
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


# ---------------------------------------------------------------------------
# Tests: New CLI commands - argument parsing
# ---------------------------------------------------------------------------


class TestNewCommandParsing:
    """Verify that all new subcommands are registered and parse correctly."""

    def test_templates_command(self):
        """'templates' command should parse correctly."""
        parser = _build_parser()
        args = parser.parse_args(["templates"])

        assert args.command == "templates"

    def test_templates_with_category(self):
        """'templates --category data' should set the category filter."""
        parser = _build_parser()
        args = parser.parse_args(["templates", "--category", "data"])

        assert args.command == "templates"
        assert args.category == "data"

    def test_replay_command(self):
        """'replay <run_id> --from-step <step>' should parse correctly."""
        parser = _build_parser()
        args = parser.parse_args(["replay", "run-abc", "--from-step", "step-2"])

        assert args.command == "replay"
        assert args.run_id == "run-abc"
        assert args.from_step == "step-2"

    def test_fork_command(self):
        """'fork <run_id> --from-step <step>' should parse correctly."""
        parser = _build_parser()
        args = parser.parse_args(["fork", "run-abc", "--from-step", "step-1"])

        assert args.command == "fork"
        assert args.run_id == "run-abc"
        assert args.from_step == "step-1"

    def test_fork_with_changes(self):
        """'fork <run_id> --from-step s --change k=v' should capture changes."""
        parser = _build_parser()
        args = parser.parse_args([
            "fork", "run-abc", "--from-step", "s",
            "--change", "prompt=new text",
            "--change", "model=haiku",
        ])

        assert args.change == ["prompt=new text", "model=haiku"]

    def test_approve_command(self):
        """'approve <run_id>' should parse correctly."""
        parser = _build_parser()
        args = parser.parse_args(["approve", "run-xyz"])

        assert args.command == "approve"
        assert args.run_id == "run-xyz"

    def test_approve_with_data(self):
        """'approve <run_id> --data ...' should set the data argument."""
        parser = _build_parser()
        args = parser.parse_args(["approve", "run-xyz", "--data", '{"ok": true}'])

        assert args.data == '{"ok": true}'

    def test_reject_command(self):
        """'reject <run_id>' should parse correctly."""
        parser = _build_parser()
        args = parser.parse_args(["reject", "run-xyz"])

        assert args.command == "reject"
        assert args.run_id == "run-xyz"

    def test_reject_with_reason(self):
        """'reject <run_id> --reason ...' should set the reason argument."""
        parser = _build_parser()
        args = parser.parse_args(["reject", "run-xyz", "--reason", "bad output"])

        assert args.reason == "bad output"

    def test_runs_command(self):
        """'runs' command should parse correctly with defaults."""
        parser = _build_parser()
        args = parser.parse_args(["runs"])

        assert args.command == "runs"
        assert args.limit == 20
        assert args.status is None

    def test_runs_with_filters(self):
        """'runs --status completed --limit 5' should set filters."""
        parser = _build_parser()
        args = parser.parse_args(["runs", "--status", "completed", "--limit", "5"])

        assert args.status == "completed"
        assert args.limit == 5

    def test_global_json_flag(self):
        """'--json templates' should make the json flag available."""
        parser = _build_parser()
        args = parser.parse_args(["--json", "templates"])

        assert args.json is True
        assert args.command == "templates"

    def test_global_json_flag_default_false(self):
        """By default --json should be False."""
        parser = _build_parser()
        args = parser.parse_args(["templates"])

        assert args.json is False


# ---------------------------------------------------------------------------
# Tests: New CLI commands - handler execution
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Mock httpx response object."""

    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class TestTemplatesCommand:
    def test_templates_json_output(self):
        """templates --json should print raw JSON."""
        mock_data = {
            "data": [
                {"name": "wf1", "category": "data", "step_count": 3, "description": "Desc"},
            ]
        }
        parser = _build_parser()
        args = parser.parse_args(["--json", "templates"])

        captured = StringIO()
        with (
            patch("httpx.get", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_templates(args)

        output = json.loads(captured.getvalue())
        assert len(output) == 1
        assert output[0]["name"] == "wf1"

    def test_templates_table_output(self):
        """templates without --json should print a formatted table."""
        mock_data = {
            "data": [
                {"name": "wf1", "category": "data", "step_count": 3, "description": "A workflow"},
            ]
        }
        parser = _build_parser()
        args = parser.parse_args(["templates"])

        captured = StringIO()
        with (
            patch("httpx.get", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_templates(args)

        output = captured.getvalue()
        assert "wf1" in output
        assert "data" in output

    def test_templates_category_filter(self):
        """templates --category should filter results."""
        mock_data = {
            "data": [
                {"name": "wf1", "category": "data", "step_count": 3, "description": "D"},
                {"name": "wf2", "category": "ai", "step_count": 2, "description": "A"},
            ]
        }
        parser = _build_parser()
        args = parser.parse_args(["--json", "templates", "--category", "data"])

        captured = StringIO()
        with (
            patch("httpx.get", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_templates(args)

        output = json.loads(captured.getvalue())
        assert len(output) == 1
        assert output[0]["name"] == "wf1"


class TestReplayCommand:
    def test_replay_json_output(self):
        """replay --json should print raw JSON."""
        mock_data = {
            "data": {
                "new_run_id": "new-run-123",
                "parent_run_id": "run-abc",
                "replay_from_step": "step-2",
                "status": "queued",
            }
        }
        parser = _build_parser()
        args = parser.parse_args(["--json", "replay", "run-abc", "--from-step", "step-2"])

        captured = StringIO()
        with (
            patch("httpx.post", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_replay(args)

        output = json.loads(captured.getvalue())
        assert output["new_run_id"] == "new-run-123"

    def test_replay_table_output(self):
        """replay should print formatted output by default."""
        mock_data = {
            "data": {
                "new_run_id": "new-run-123",
                "parent_run_id": "run-abc",
                "replay_from_step": "step-2",
                "status": "queued",
            }
        }
        parser = _build_parser()
        args = parser.parse_args(["replay", "run-abc", "--from-step", "step-2"])

        captured = StringIO()
        with (
            patch("httpx.post", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_replay(args)

        output = captured.getvalue()
        assert "new-run-123" in output
        assert "run-abc" in output


class TestForkCommand:
    def test_fork_json_output(self):
        """fork --json should print raw JSON."""
        mock_data = {
            "data": {
                "new_run_id": "fork-456",
                "parent_run_id": "run-abc",
                "fork_from_step": "step-1",
                "changes": {"prompt": "new prompt"},
                "status": "queued",
            }
        }
        parser = _build_parser()
        args = parser.parse_args([
            "--json", "fork", "run-abc", "--from-step", "step-1",
            "--change", "prompt=new prompt",
        ])

        captured = StringIO()
        with (
            patch("httpx.post", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_fork(args)

        output = json.loads(captured.getvalue())
        assert output["new_run_id"] == "fork-456"


class TestApproveCommand:
    def test_approve_prints_confirmation(self):
        """approve should print a confirmation message."""
        # Mock the approval lookup and the approve call
        approval_resp = _FakeResponse({
            "data": [
                {"id": "appr-111", "run_id": "run-xyz", "status": "pending"},
            ]
        })
        approve_resp = _FakeResponse({
            "data": {"approved": True, "approval_id": "appr-111", "run_id": "run-xyz"}
        })

        parser = _build_parser()
        args = parser.parse_args(["approve", "run-xyz"])

        captured = StringIO()
        with (
            patch("httpx.get", return_value=approval_resp),
            patch("httpx.post", return_value=approve_resp),
            patch("sys.stdout", captured),
        ):
            _cmd_approve(args)

        output = captured.getvalue()
        assert "Approved" in output
        assert "run-xyz" in output


class TestRejectCommand:
    def test_reject_prints_confirmation(self):
        """reject should print a confirmation message."""
        approval_resp = _FakeResponse({
            "data": [
                {"id": "appr-222", "run_id": "run-xyz", "status": "pending"},
            ]
        })
        reject_resp = _FakeResponse({
            "data": {"rejected": True, "approval_id": "appr-222", "run_id": "run-xyz"}
        })

        parser = _build_parser()
        args = parser.parse_args(["reject", "run-xyz", "--reason", "bad quality"])

        captured = StringIO()
        with (
            patch("httpx.get", return_value=approval_resp),
            patch("httpx.post", return_value=reject_resp),
            patch("sys.stdout", captured),
        ):
            _cmd_reject(args)

        output = captured.getvalue()
        assert "Rejected" in output
        assert "run-xyz" in output
        assert "bad quality" in output


class TestRunsCommand:
    def test_runs_json_output(self):
        """runs --json should print raw JSON."""
        mock_data = {
            "data": [
                {
                    "run_id": "abc-123-def-456",
                    "workflow_name": "test-wf",
                    "status": "completed",
                    "total_cost_usd": 0.0123,
                    "started_at": "2026-02-25T10:00:00",
                },
            ],
            "meta": {"total": 1, "limit": 20, "offset": 0},
        }
        parser = _build_parser()
        args = parser.parse_args(["--json", "runs"])

        captured = StringIO()
        with (
            patch("httpx.get", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_runs(args)

        output = json.loads(captured.getvalue())
        assert output["data"][0]["workflow_name"] == "test-wf"

    def test_runs_table_output(self):
        """runs should print a formatted table by default."""
        mock_data = {
            "data": [
                {
                    "run_id": "abc-123-def-456",
                    "workflow_name": "test-wf",
                    "status": "completed",
                    "total_cost_usd": 0.0123,
                    "started_at": "2026-02-25T10:00:00",
                },
            ],
            "meta": {"total": 1, "limit": 20, "offset": 0},
        }
        parser = _build_parser()
        args = parser.parse_args(["runs"])

        captured = StringIO()
        with (
            patch("httpx.get", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_runs(args)

        output = captured.getvalue()
        assert "test-wf" in output
        assert "abc-123-def" in output

    def test_runs_no_results(self):
        """runs with empty results should print a message."""
        mock_data = {"data": [], "meta": {"total": 0, "limit": 20, "offset": 0}}
        parser = _build_parser()
        args = parser.parse_args(["runs"])

        captured = StringIO()
        with (
            patch("httpx.get", return_value=_FakeResponse(mock_data)),
            patch("sys.stdout", captured),
        ):
            _cmd_runs(args)

        output = captured.getvalue()
        assert "No runs found" in output
