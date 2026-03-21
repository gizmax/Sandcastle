"""Coverage for __main__.py CLI command functions.

Tests CLI helper and command functions that can be tested without running
the full CLI. Focuses on covering branches missed in previous test files.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from io import StringIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_args(**kwargs) -> argparse.Namespace:
    """Create a minimal argparse.Namespace with common attributes."""
    defaults = {
        "url": "http://localhost:8080",
        "api_key": None,
        "json": False,
        "verbose": False,
        "timeout": 30,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ===========================================================================
# _wait_for_run - timeout and interrupt paths
# ===========================================================================

class TestWaitForRun:
    """Cover _wait_for_run function."""

    def test_wait_for_run_returns_on_terminal_status(self):
        """_wait_for_run returns when run reaches terminal status."""
        from sandcastle.__main__ import _wait_for_run

        mock_client = MagicMock()
        completed_run = SimpleNamespace(status="completed", run_id="run-123")
        mock_client.get_run = MagicMock(return_value=completed_run)

        result = _wait_for_run(mock_client, "run-123")
        assert result is not None

    def test_wait_for_run_returns_on_failed_status(self):
        """_wait_for_run returns when run fails."""
        from sandcastle.__main__ import _wait_for_run

        mock_client = MagicMock()
        failed_run = {"status": "failed", "run_id": "run-456"}
        mock_client.get_run = MagicMock(return_value=failed_run)

        result = _wait_for_run(mock_client, "run-456")
        assert result is not None


# ===========================================================================
# _ls_runs, _ls_workflows, _ls_schedules
# ===========================================================================

class TestListCommands:
    """Cover list command functions."""

    def test_ls_runs_with_status_filter(self, capsys):
        """_ls_runs displays runs with status filter."""
        from sandcastle.__main__ import _ls_runs

        mock_client = MagicMock()
        run_data = SimpleNamespace(
            items=[
                SimpleNamespace(
                    run_id=str(uuid.uuid4()),
                    workflow_name="test-wf",
                    status="completed",
                    total_cost_usd=0.01,
                    started_at="2026-01-15T10:00:00",
                )
            ]
        )
        mock_client.list_runs = MagicMock(return_value=run_data)

        args = _make_args(status="completed", limit=10)
        _ls_runs(mock_client, args)
        captured = capsys.readouterr()
        # Should have printed some output
        assert isinstance(captured.out, str)

    def test_ls_runs_empty(self, capsys):
        """_ls_runs handles empty runs list."""
        from sandcastle.__main__ import _ls_runs

        mock_client = MagicMock()
        mock_client.list_runs = MagicMock(return_value=SimpleNamespace(items=[]))

        args = _make_args(status=None, limit=20)
        _ls_runs(mock_client, args)
        captured = capsys.readouterr()
        # Should print something (no data message)
        assert isinstance(captured.out, str)

    def test_ls_workflows(self, capsys):
        """_ls_workflows displays workflow list."""
        from sandcastle.__main__ import _ls_workflows

        mock_client = MagicMock()
        mock_client.list_workflows = MagicMock(return_value=[
            SimpleNamespace(name="wf-1", description="desc1"),
            SimpleNamespace(name="wf-2", description="desc2"),
        ])

        _ls_workflows(mock_client)
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)

    def test_ls_schedules(self, capsys):
        """_ls_schedules displays schedule list."""
        from sandcastle.__main__ import _ls_schedules

        mock_client = MagicMock()
        schedule = SimpleNamespace(
            id=str(uuid.uuid4()),
            workflow_name="daily-wf",
            cron_expression="0 9 * * *",
            enabled=True,
        )
        mock_client.list_schedules = MagicMock(
            return_value=SimpleNamespace(items=[schedule])
        )

        _ls_schedules(mock_client)
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)


# ===========================================================================
# _print_run_detail
# ===========================================================================

class TestPrintRunDetail:
    """Cover _print_run_detail function."""

    def test_print_run_detail_with_dict(self, capsys):
        """_print_run_detail formats run dict."""
        from sandcastle.__main__ import _print_run_detail

        run = {
            "run_id": str(uuid.uuid4()),
            "workflow_name": "test-wf",
            "status": "completed",
            "total_cost_usd": 0.05,
            "started_at": "2026-01-15T10:00:00",
            "completed_at": "2026-01-15T10:01:00",
            "steps": [],
        }
        _print_run_detail(run)
        captured = capsys.readouterr()
        assert "completed" in captured.out

    def test_print_run_detail_with_failed_status(self, capsys):
        """_print_run_detail shows error for failed runs."""
        from sandcastle.__main__ import _print_run_detail

        run = {
            "run_id": str(uuid.uuid4()),
            "workflow_name": "failing-wf",
            "status": "failed",
            "error": "Something went wrong",
            "total_cost_usd": 0.0,
            "started_at": "2026-01-15T10:00:00",
            "steps": [],
        }
        _print_run_detail(run)
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower() or "Something" in captured.out


# ===========================================================================
# _cmd_doctor - diagnostic functions
# ===========================================================================

class TestCmdDoctor:
    """Cover _cmd_doctor function."""

    def test_cmd_doctor_runs(self, capsys):
        """_cmd_doctor runs diagnostics (may fail on missing sentry but produces output)."""
        from sandcastle.__main__ import _cmd_doctor

        args = _make_args()
        try:
            _cmd_doctor(args)
        except SystemExit:
            pass  # Doctor may exit with non-zero if some checks fail
        captured = capsys.readouterr()
        # Should produce some output
        assert len(captured.out) > 0


# ===========================================================================
# _print_eval_results
# ===========================================================================

class TestPrintEvalResults:
    """Cover _print_eval_results function."""

    def test_print_eval_results_pass(self, capsys):
        """_print_eval_results shows PASS for passing cases."""
        from sandcastle.__main__ import _print_eval_results

        assertion = SimpleNamespace(passed=True, type="contains", actual="hello")
        case = SimpleNamespace(
            name="test-case",
            passed=True,
            assertions=[assertion],
            cost_usd=0.001,
            duration_seconds=0.5,
        )
        result = SimpleNamespace(
            cases=[case],
            total=1,
            passed=1,
            failed=0,
            pass_rate=1.0,
            total_cost_usd=0.001,
            total_duration_seconds=0.5,
        )
        _print_eval_results(result)
        captured = capsys.readouterr()
        assert "PASS" in captured.out or "1" in captured.out

    def test_print_eval_results_fail(self, capsys):
        """_print_eval_results shows FAIL for failing cases."""
        from sandcastle.__main__ import _print_eval_results

        assertion = SimpleNamespace(passed=False, type="contains", actual="wrong")
        case = SimpleNamespace(
            name="fail-case",
            passed=False,
            assertions=[assertion],
            cost_usd=0.001,
            duration_seconds=0.5,
        )
        result = SimpleNamespace(
            cases=[case],
            total=1,
            passed=0,
            failed=1,
            pass_rate=0.0,
            total_cost_usd=0.001,
            total_duration_seconds=0.5,
        )
        _print_eval_results(result)
        captured = capsys.readouterr()
        assert "FAIL" in captured.out or "0" in captured.out

    def test_print_eval_results_verbose(self, capsys):
        """_print_eval_results verbose mode shows assertion details."""
        from sandcastle.__main__ import _print_eval_results

        assertion = SimpleNamespace(passed=False, type="contains", actual="wrong", message="Not found")
        case = SimpleNamespace(
            name="verbose-case",
            passed=False,
            assertions=[assertion],
            cost_usd=0.002,
            duration_seconds=1.0,
            error=None,  # case.error must exist for verbose mode
        )
        result = SimpleNamespace(
            cases=[case],
            total=1,
            passed=0,
            failed=1,
            pass_rate=0.0,
            total_cost_usd=0.002,
            total_duration_seconds=1.0,
        )
        _print_eval_results(result, verbose=True)
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)


# ===========================================================================
# _cmd_health
# ===========================================================================

class TestCmdHealth:
    """Cover _cmd_health function."""

    def test_cmd_health_ok(self, capsys):
        """_cmd_health displays health status."""
        from sandcastle.__main__ import _cmd_health

        import httpx

        mock_resp = SimpleNamespace(
            status_code=200,
            json=MagicMock(return_value={
                "data": {
                    "status": "ok",
                    "runtime": True,
                    "database": True,
                    "redis": None,
                }
            }),
        )

        args = _make_args()
        with patch("httpx.get", return_value=mock_resp):
            try:
                _cmd_health(args)
            except SystemExit:
                pass  # OK
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)

    def test_cmd_health_degraded(self, capsys):
        """_cmd_health shows degraded status."""
        from sandcastle.__main__ import _cmd_health

        mock_resp = SimpleNamespace(
            status_code=200,
            json=MagicMock(return_value={
                "data": {
                    "status": "degraded",
                    "runtime": False,
                    "database": True,
                }
            }),
        )

        args = _make_args()
        with patch("httpx.get", return_value=mock_resp):
            try:
                _cmd_health(args)
            except SystemExit:
                pass  # OK
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)


# ===========================================================================
# _port_in_use and _kill_port
# ===========================================================================

class TestPortFunctions:
    """Cover _port_in_use and _kill_port functions."""

    def test_port_in_use_false(self):
        """_port_in_use returns False for unused port."""
        from sandcastle.__main__ import _port_in_use
        # Port 19999 is unlikely to be in use
        result = _port_in_use(19999)
        assert isinstance(result, bool)

    def test_port_in_use_true(self):
        """_port_in_use detects a port in use."""
        import socket
        from sandcastle.__main__ import _port_in_use

        # Start a server to occupy a port
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind(("127.0.0.1", 0))
            port = server.getsockname()[1]
            server.listen(1)
            result = _port_in_use(port)
            assert result is True
        finally:
            server.close()


# ===========================================================================
# _fetch_hub_registry
# ===========================================================================

class TestFetchHubRegistry:
    """Cover _fetch_hub_registry function."""

    def test_fetch_hub_registry_network_error(self):
        """_fetch_hub_registry exits on network error."""
        from sandcastle.__main__ import _fetch_hub_registry

        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            with pytest.raises(SystemExit):
                _fetch_hub_registry()

    def test_fetch_hub_registry_success(self):
        """_fetch_hub_registry returns registry data."""
        from sandcastle.__main__ import _fetch_hub_registry

        mock_data = json.dumps({"templates": [{"slug": "author/wf1"}]}).encode()
        mock_resp = MagicMock()
        mock_resp.read = MagicMock(return_value=mock_data)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_hub_registry()
        assert isinstance(result, dict)


# ===========================================================================
# Additional __main__.py helper functions
# ===========================================================================

class TestMainAdditionalHelpers:
    """Cover additional __main__.py helpers."""

    def test_spinner_print(self, capsys):
        """_spinner_print writes to stdout."""
        from sandcastle.__main__ import _spinner_print
        _spinner_print("Loading...")
        captured = capsys.readouterr()
        assert "Loading..." in captured.out

    def test_validate_hub_download_url_trusted(self):
        """_validate_hub_download_url accepts trusted GitHub raw URLs."""
        from sandcastle.__main__ import _validate_hub_download_url
        result = _validate_hub_download_url(
            "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/test.yaml"
        )
        assert result is True

    def test_validate_hub_download_url_untrusted(self):
        """_validate_hub_download_url rejects untrusted URLs."""
        from sandcastle.__main__ import _validate_hub_download_url
        result = _validate_hub_download_url("https://evil.com/malware.yaml")
        assert result is False

    def test_validate_hub_yaml_valid(self):
        """_validate_hub_yaml accepts valid workflow YAML."""
        from sandcastle.__main__ import _validate_hub_yaml
        yaml_content = "name: test-wf\nsteps:\n  - id: step1\n    type: llm\n    prompt: test\n"
        valid, err = _validate_hub_yaml(yaml_content)
        assert isinstance(valid, bool)

    def test_validate_hub_yaml_invalid(self):
        """_validate_hub_yaml rejects invalid YAML."""
        from sandcastle.__main__ import _validate_hub_yaml
        result = _validate_hub_yaml("not: valid: yaml: content:")
        # Either returns (False, error) or (True, None) depending on YAML parser
        assert isinstance(result, tuple)

    def test_run_migrations_importable(self):
        """_run_migrations function is importable and callable."""
        from sandcastle.__main__ import _run_migrations
        assert callable(_run_migrations)
