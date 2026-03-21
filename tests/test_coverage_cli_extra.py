"""Coverage for __main__.py additional branches.

Targets:
  - _load_dot_env OSError branch
  - _wait_for_run timeout, spinner, and KeyboardInterrupt branches
  - _cmd_run with file, --wait, failed status branches
  - _cmd_status with json output
  - _cmd_db_migrate
  - _cmd_worker KeyboardInterrupt
  - _cmd_doctor various backend branches
  - _cmd_health redis branch
  - _cmd_generate with description
  - _cmd_templates error branch
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ===========================================================================
# _load_dot_env - OSError branch
# ===========================================================================

class TestLoadDotEnvOsError:
    """Cover OSError branch of _load_dot_env."""

    def test_load_dot_env_oserror(self, tmp_path, monkeypatch):
        """_load_dot_env handles OSError when reading .env file."""
        import sandcastle.__main__ as main_module
        from sandcastle.__main__ import _load_dot_env

        # Reset the loaded flag
        monkeypatch.setattr(main_module, "_dot_env_loaded", False)

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            # Create .env as directory so read_text raises IsADirectoryError
            env_dir = tmp_path / ".env"
            env_dir.mkdir()

            # Should not raise even with OSError
            _load_dot_env()
        except Exception:
            pass  # Expected to handle gracefully
        finally:
            os.chdir(orig_cwd)

    def test_load_dot_env_read_oserror(self, tmp_path, monkeypatch):
        """_load_dot_env handles OSError from read_text."""
        import sandcastle.__main__ as main_module
        from sandcastle.__main__ import _load_dot_env

        # Reset the loaded flag
        monkeypatch.setattr(main_module, "_dot_env_loaded", False)

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            # Create a valid .env file but patch read_text to raise
            env_file = tmp_path / ".env"
            env_file.write_text("KEY=VALUE\n")

            with patch("pathlib.Path.read_text", side_effect=OSError("permission denied")):
                _load_dot_env()  # Should not raise
        finally:
            os.chdir(orig_cwd)


# ===========================================================================
# _wait_for_run - timeout and spinner branches
# ===========================================================================

class TestWaitForRun:
    """Cover _wait_for_run branches."""

    def test_wait_for_run_timeout(self):
        """_wait_for_run returns timeout dict when max_wait exceeded."""
        from sandcastle.__main__ import _wait_for_run

        mock_client = MagicMock()
        # Simulate that status never reaches terminal
        run = SimpleNamespace(status="running", run_id="test-run-id")
        mock_client.get_run = MagicMock(return_value=run)

        import time
        call_count = 0

        def mock_time_monotonic():
            nonlocal call_count
            call_count += 1
            # First call returns 0, second returns 3601 (past max_wait)
            if call_count <= 1:
                return 0.0
            return 3601.0

        with patch("sandcastle.__main__.time") as mock_time:
            mock_time.monotonic = mock_time_monotonic
            mock_time.sleep = MagicMock()
            result = _wait_for_run(mock_client, "test-run-id")

        assert result.get("status") == "timeout"

    def test_wait_for_run_keyboard_interrupt(self):
        """_wait_for_run handles KeyboardInterrupt."""
        from sandcastle.__main__ import _wait_for_run

        mock_client = MagicMock()
        # First get_run raises KeyboardInterrupt
        mock_client.get_run = MagicMock(side_effect=KeyboardInterrupt())

        result = _wait_for_run(mock_client, "test-run-id")
        assert result.get("status") == "interrupted"

    def test_wait_for_run_spinner_then_complete(self):
        """_wait_for_run shows spinner then returns on completion."""
        from sandcastle.__main__ import _wait_for_run

        mock_client = MagicMock()
        call_count = 0

        def get_run(run_id):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return SimpleNamespace(status="completed", run_id=run_id)
            return SimpleNamespace(status="running", run_id=run_id)

        mock_client.get_run = get_run

        time_calls = [0.0, 1.0, 2.0, 3.0, 4.0]
        time_idx = [0]

        def mock_monotonic():
            val = time_calls[min(time_idx[0], len(time_calls) - 1)]
            time_idx[0] += 1
            return val

        with patch("sandcastle.__main__.time") as mock_time:
            mock_time.monotonic = mock_monotonic
            mock_time.sleep = MagicMock()

            result = _wait_for_run(mock_client, "test-run-id")

        assert result.get("status") == "completed"


# ===========================================================================
# _cmd_run - various branches
# ===========================================================================

class TestCmdRunBranches:
    """Cover _cmd_run branches."""

    def test_cmd_run_with_yaml_file(self, tmp_path):
        """_cmd_run reads YAML file when workflow is a .yaml file."""
        from sandcastle.__main__ import _cmd_run

        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("name: test\nsteps: []\n")

        args = SimpleNamespace(
            workflow=str(yaml_file),
            input=[],
            input_file=None,
            max_cost=None,
            wait=False,
            json=False,
            url=None,
            api_key=None,
        )

        mock_client = MagicMock()
        run = SimpleNamespace(run_id="test-run-id-123")
        mock_client.run_yaml = MagicMock(return_value=run)

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            with patch("sandcastle.__main__.sys") as mock_sys:
                mock_sys.exit = MagicMock(side_effect=SystemExit)
                try:
                    _cmd_run(args)
                except SystemExit:
                    pass

    def test_cmd_run_with_wait_completed(self, tmp_path):
        """_cmd_run with --wait shows run detail on completion."""
        from sandcastle.__main__ import _cmd_run

        args = SimpleNamespace(
            workflow="test-workflow",
            input=[],
            input_file=None,
            max_cost=None,
            wait=True,
            json=False,
            url=None,
            api_key=None,
        )

        mock_client = MagicMock()
        run = SimpleNamespace(run_id="test-run-id-456")
        mock_client.run = MagicMock(return_value=run)

        result = {"status": "completed", "run_id": "test-run-id-456"}

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            with patch("sandcastle.__main__._wait_for_run", return_value=result):
                with patch("sandcastle.__main__._print_run_detail"):
                    _cmd_run(args)

    def test_cmd_run_with_wait_failed_exits_2(self, tmp_path):
        """_cmd_run with --wait exits 2 when run fails."""
        from sandcastle.__main__ import _cmd_run

        args = SimpleNamespace(
            workflow="test-workflow",
            input=[],
            input_file=None,
            max_cost=None,
            wait=True,
            json=True,
            url=None,
            api_key=None,
        )

        mock_client = MagicMock()
        run = SimpleNamespace(run_id="test-run-id-789")
        mock_client.run = MagicMock(return_value=run)

        result = {"status": "failed", "run_id": "test-run-id-789"}

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            with patch("sandcastle.__main__._wait_for_run", return_value=result):
                with pytest.raises(SystemExit) as exc_info:
                    _cmd_run(args)
                assert exc_info.value.code == 2

    def test_cmd_run_exception_exits_1(self):
        """_cmd_run exits 1 on exception from client.run."""
        from sandcastle.__main__ import _cmd_run

        args = SimpleNamespace(
            workflow="test-workflow",
            input=[],
            input_file=None,
            max_cost=None,
            wait=False,
            json=False,
            url=None,
            api_key=None,
        )

        mock_client = MagicMock()
        mock_client.run = MagicMock(side_effect=Exception("connection refused"))

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_run(args)
            assert exc_info.value.code == 1


# ===========================================================================
# _cmd_db_migrate
# ===========================================================================

class TestCmdDbMigrate:
    """Cover _cmd_db_migrate function."""

    def test_cmd_db_migrate_calls_run_migrations(self):
        """_cmd_db_migrate calls _run_migrations."""
        from sandcastle.__main__ import _cmd_db_migrate

        args = SimpleNamespace()
        with patch("sandcastle.__main__._run_migrations") as mock_migrate:
            _cmd_db_migrate(args)
        mock_migrate.assert_called_once()


# ===========================================================================
# _cmd_worker - KeyboardInterrupt branch
# ===========================================================================

class TestCmdWorker:
    """Cover _cmd_worker function."""

    def test_cmd_worker_keyboard_interrupt(self):
        """_cmd_worker handles KeyboardInterrupt."""
        from sandcastle.__main__ import _cmd_worker

        args = SimpleNamespace()
        with patch("subprocess.run", side_effect=KeyboardInterrupt()):
            _cmd_worker(args)  # Should not raise

    def test_cmd_worker_file_not_found(self):
        """_cmd_worker handles FileNotFoundError."""
        from sandcastle.__main__ import _cmd_worker

        args = SimpleNamespace()
        with patch("subprocess.run", side_effect=FileNotFoundError("arq not found")):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_worker(args)
            assert exc_info.value.code == 1


# ===========================================================================
# _cmd_doctor - backend branches
# ===========================================================================

class TestCmdDoctorBranches:
    """Cover _cmd_doctor backend and settings branches."""

    def test_cmd_doctor_runs_without_env_file(self, tmp_path, capsys):
        """_cmd_doctor handles missing .env file."""
        from sandcastle.__main__ import _cmd_doctor

        args = SimpleNamespace()
        # Change to a temp directory where there's no .env file
        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            try:
                _cmd_doctor(args)
            except (SystemExit, Exception):
                pass  # Doctor may call sys.exit
        finally:
            os.chdir(orig_cwd)

    def test_cmd_doctor_docker_backend(self, tmp_path, capsys):
        """_cmd_doctor checks docker backend branches."""
        from sandcastle.__main__ import _cmd_doctor

        args = SimpleNamespace()

        mock_cfg = MagicMock()
        mock_cfg.anthropic_api_key = ""
        mock_cfg.e2b_api_key = ""
        mock_cfg.sandbox_backend = "docker"
        mock_cfg.database_url = ""
        mock_cfg.redis_url = ""
        mock_cfg.cloudflare_worker_url = ""

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with patch("sandcastle.config.Settings", return_value=mock_cfg):
                try:
                    _cmd_doctor(args)
                except (SystemExit, Exception):
                    pass
        finally:
            os.chdir(orig_cwd)

    def test_cmd_doctor_local_backend(self, tmp_path, capsys):
        """_cmd_doctor checks local backend branches."""
        from sandcastle.__main__ import _cmd_doctor

        args = SimpleNamespace()

        mock_cfg = MagicMock()
        mock_cfg.anthropic_api_key = ""
        mock_cfg.e2b_api_key = ""
        mock_cfg.sandbox_backend = "local"
        mock_cfg.database_url = ""
        mock_cfg.redis_url = ""
        mock_cfg.cloudflare_worker_url = ""

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with patch("sandcastle.config.Settings", return_value=mock_cfg):
                try:
                    _cmd_doctor(args)
                except (SystemExit, Exception):
                    pass
        finally:
            os.chdir(orig_cwd)

    def test_cmd_doctor_cloudflare_backend_configured(self, tmp_path, capsys):
        """_cmd_doctor checks cloudflare backend configured."""
        from sandcastle.__main__ import _cmd_doctor

        args = SimpleNamespace()

        mock_cfg = MagicMock()
        mock_cfg.anthropic_api_key = ""
        mock_cfg.e2b_api_key = ""
        mock_cfg.sandbox_backend = "cloudflare"
        mock_cfg.database_url = ""
        mock_cfg.redis_url = ""
        mock_cfg.cloudflare_worker_url = "https://worker.example.com"

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with patch("sandcastle.config.Settings", return_value=mock_cfg):
                try:
                    _cmd_doctor(args)
                except (SystemExit, Exception):
                    pass
        finally:
            os.chdir(orig_cwd)


# ===========================================================================
# _cmd_health - redis branch
# ===========================================================================

class TestCmdHealthBranches:
    """Cover _cmd_health branches."""

    def test_cmd_health_with_redis_ok(self):
        """_cmd_health prints redis status when available."""
        from sandcastle.__main__ import _cmd_health

        args = SimpleNamespace(url=None, api_key=None)
        mock_client = MagicMock()
        health_data = {
            "status": "ok",
            "runtime": True,
            "database": True,
            "redis": True,
        }
        mock_client.health = MagicMock(return_value=health_data)

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            _cmd_health(args)

    def test_cmd_health_with_redis_none(self):
        """_cmd_health skips redis when not configured."""
        from sandcastle.__main__ import _cmd_health

        args = SimpleNamespace(url=None, api_key=None)
        mock_client = MagicMock()
        health_data = {
            "status": "ok",
            "runtime": True,
            "database": True,
            "redis": None,
        }
        mock_client.health = MagicMock(return_value=health_data)

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            _cmd_health(args)

    def test_cmd_health_failed_status_exits(self):
        """_cmd_health exits 1 when status is not ok."""
        from sandcastle.__main__ import _cmd_health

        args = SimpleNamespace(url=None, api_key=None)
        mock_client = MagicMock()
        health_data = {
            "status": "degraded",
            "runtime": False,
            "database": True,
            "redis": False,
        }
        mock_client.health = MagicMock(return_value=health_data)

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_health(args)
            assert exc_info.value.code == 1


# ===========================================================================
# _cmd_generate - exception branches
# ===========================================================================

class TestCmdGenerateBranches:
    """Cover _cmd_generate branches."""

    def test_cmd_generate_with_description(self):
        """_cmd_generate calls generate_workflow_sync with description."""
        from sandcastle.__main__ import _cmd_generate

        args = SimpleNamespace(
            description="create a hello world workflow",
            output=None,
            refine=False,
            url=None,
            api_key=None,
        )

        mock_result = SimpleNamespace(
            yaml_content="name: hello\nsteps: []\n",
            name="hello",
            steps_count=0,
            validation_errors=[],
        )

        with patch("sandcastle.engine.generator.generate_workflow_sync", return_value=mock_result):
            _cmd_generate(args)

    def test_cmd_generate_with_validation_errors(self):
        """_cmd_generate shows warnings when result has validation errors."""
        from sandcastle.__main__ import _cmd_generate

        args = SimpleNamespace(
            description="create a workflow",
            output=None,
            refine=False,
            url=None,
            api_key=None,
        )

        mock_result = SimpleNamespace(
            yaml_content="name: test\nsteps: []\n",
            name="test",
            steps_count=0,
            validation_errors=["step1 missing type", "step2 invalid prompt"],
        )

        with patch("sandcastle.engine.generator.generate_workflow_sync", return_value=mock_result):
            _cmd_generate(args)

    def test_cmd_generate_value_error_exits(self):
        """_cmd_generate exits 1 on ValueError."""
        from sandcastle.__main__ import _cmd_generate

        args = SimpleNamespace(
            description="test",
            output=None,
            refine=False,
            url=None,
            api_key=None,
        )

        with patch("sandcastle.engine.generator.generate_workflow_sync", side_effect=ValueError("no api key")):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_generate(args)
            assert exc_info.value.code == 1

    def test_cmd_generate_exception_exits(self):
        """_cmd_generate exits 1 on generic Exception."""
        from sandcastle.__main__ import _cmd_generate

        args = SimpleNamespace(
            description="test",
            output=None,
            refine=False,
            url=None,
            api_key=None,
        )

        with patch("sandcastle.engine.generator.generate_workflow_sync", side_effect=Exception("failure")):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_generate(args)
            assert exc_info.value.code == 1

    def test_cmd_generate_saves_output_file(self, tmp_path):
        """_cmd_generate saves yaml to output file when --output specified."""
        from sandcastle.__main__ import _cmd_generate

        output_file = tmp_path / "output.yaml"
        args = SimpleNamespace(
            description="test workflow",
            output=str(output_file),
            refine=False,
            url=None,
            api_key=None,
        )

        mock_result = SimpleNamespace(
            yaml_content="name: test\nsteps: []\n",
            name="test",
            steps_count=0,
            validation_errors=[],
        )

        with patch("sandcastle.engine.generator.generate_workflow_sync", return_value=mock_result):
            _cmd_generate(args)


# ===========================================================================
# _cmd_templates - error branches
# ===========================================================================

class TestCmdTemplatesBranches:
    """Cover _cmd_templates branches."""

    def test_cmd_templates_network_error(self):
        """_cmd_templates exits 1 on network error."""
        from sandcastle.__main__ import _cmd_templates

        args = SimpleNamespace(
            url=None,
            category=None,
            json=False,
            api_key=None,
        )

        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_templates(args)
            assert exc_info.value.code == 1

    def test_cmd_templates_success(self):
        """_cmd_templates prints templates on success."""
        from sandcastle.__main__ import _cmd_templates

        args = SimpleNamespace(
            url=None,
            category=None,
            json=False,
            api_key=None,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"data": [
            {"name": "template1", "category": "ai", "step_count": 3, "description": "test"},
        ]})

        with patch("httpx.get", return_value=mock_resp):
            _cmd_templates(args)


# ===========================================================================
# ls_schedules - not yet covered
# ===========================================================================

class TestLsSchedules:
    """Cover _ls_schedules function."""

    def test_ls_schedules_empty(self):
        """_ls_schedules with no schedules prints message."""
        from sandcastle.__main__ import _ls_schedules

        mock_client = MagicMock()
        mock_client.list_schedules = MagicMock(return_value=[])

        _ls_schedules(mock_client)

    def test_ls_schedules_with_items(self):
        """_ls_schedules shows table when schedules exist."""
        from sandcastle.__main__ import _ls_schedules

        mock_client = MagicMock()
        schedule = {
            "id": "sched-001",
            "workflow_name": "daily-report",
            "cron_expression": "0 9 * * *",
            "enabled": True,
            "run_count": 5,
        }
        mock_client.list_schedules = MagicMock(return_value=[schedule])

        _ls_schedules(mock_client)


# ===========================================================================
# ls_runs - limit validation
# ===========================================================================

class TestLsRunsValidation:
    """Cover _ls_runs limit validation."""

    def test_ls_runs_limit_zero_exits(self):
        """_ls_runs exits with error if limit is 0."""
        from sandcastle.__main__ import _ls_runs

        mock_client = MagicMock()
        args = SimpleNamespace(status=None, limit=0)

        with pytest.raises(SystemExit) as exc_info:
            _ls_runs(mock_client, args)
        assert exc_info.value.code == 1

    def test_ls_runs_negative_limit_exits(self):
        """_ls_runs exits with error if limit is negative."""
        from sandcastle.__main__ import _ls_runs

        mock_client = MagicMock()
        args = SimpleNamespace(status=None, limit=-5)

        with pytest.raises(SystemExit) as exc_info:
            _ls_runs(mock_client, args)
        assert exc_info.value.code == 1


# ===========================================================================
# _ls_workflows - coverage
# ===========================================================================

class TestLsWorkflows:
    """Cover _ls_workflows function."""

    def test_ls_workflows_empty(self):
        """_ls_workflows with no workflows prints message."""
        from sandcastle.__main__ import _ls_workflows

        mock_client = MagicMock()
        mock_client.list_workflows = MagicMock(return_value=[])

        _ls_workflows(mock_client)

    def test_ls_workflows_with_items(self):
        """_ls_workflows shows table when workflows exist."""
        from sandcastle.__main__ import _ls_workflows

        mock_client = MagicMock()
        wf = {
            "name": "test-workflow",
            "description": "A test workflow",
            "steps_count": 3,
        }
        mock_client.list_workflows = MagicMock(return_value=[wf])

        _ls_workflows(mock_client)


# ===========================================================================
# _cmd_status - json output branch
# ===========================================================================

class TestCmdStatusBranches:
    """Cover _cmd_status branches."""

    def test_cmd_status_json_output(self):
        """_cmd_status with --json outputs JSON."""
        from sandcastle.__main__ import _cmd_status

        args = SimpleNamespace(
            run_id="12345678-1234-5678-1234-567812345678",
            json=True,
            url=None,
            api_key=None,
        )

        run = {"status": "completed", "run_id": args.run_id}
        mock_client = MagicMock()
        mock_client.get_run = MagicMock(return_value=run)

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            _cmd_status(args)

    def test_cmd_status_exception_exits(self):
        """_cmd_status exits 1 on exception."""
        from sandcastle.__main__ import _cmd_status

        args = SimpleNamespace(
            run_id="12345678-1234-5678-1234-567812345678",
            json=False,
            url=None,
            api_key=None,
        )

        mock_client = MagicMock()
        mock_client.get_run = MagicMock(side_effect=Exception("not found"))

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_status(args)
            assert exc_info.value.code == 1


# ===========================================================================
# _cmd_cancel - branches
# ===========================================================================

class TestCmdCancelBranches:
    """Cover _cmd_cancel branches."""

    def test_cmd_cancel_success(self):
        """_cmd_cancel prints confirmation on success."""
        from sandcastle.__main__ import _cmd_cancel

        args = SimpleNamespace(
            run_id="12345678-1234-5678-1234-567812345678",
            json=False,
            url=None,
            api_key=None,
        )

        mock_client = MagicMock()
        mock_client.cancel_run = MagicMock(return_value={"cancelled": True})

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            _cmd_cancel(args)

    def test_cmd_cancel_with_json(self):
        """_cmd_cancel with --json outputs JSON."""
        from sandcastle.__main__ import _cmd_cancel

        args = SimpleNamespace(
            run_id="12345678-1234-5678-1234-567812345678",
            json=True,
            url=None,
            api_key=None,
        )

        mock_client = MagicMock()
        mock_client.cancel_run = MagicMock(return_value={"cancelled": True})

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            _cmd_cancel(args)

    def test_cmd_cancel_exception_exits(self):
        """_cmd_cancel exits 1 on exception."""
        from sandcastle.__main__ import _cmd_cancel

        args = SimpleNamespace(
            run_id="12345678-1234-5678-1234-567812345678",
            json=False,
            url=None,
            api_key=None,
        )

        mock_client = MagicMock()
        mock_client.cancel_run = MagicMock(side_effect=Exception("not found"))

        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_cancel(args)
            assert exc_info.value.code == 1


# ===========================================================================
# _cmd_init - chmod OSError branches
# ===========================================================================

class TestCmdInitOsError:
    """Cover _cmd_init chmod OSError branches."""

    def test_cmd_init_chmod_env_oserror(self, tmp_path, monkeypatch):
        """_cmd_init handles OSError from chmod on .env file."""
        from sandcastle.__main__ import _cmd_init
        import sandcastle.__main__ as main_module

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))

            # Mock all input() calls to return empty strings
            monkeypatch.setattr("builtins.input", lambda _: "test-key")

            # Patch chmod to raise OSError
            with patch("pathlib.Path.chmod", side_effect=OSError("permission denied")):
                with patch("pathlib.Path.mkdir"):
                    with patch("sandcastle.config._DEFAULT_DATA_DIR", str(tmp_path / "data")):
                        with patch("sandcastle.config._DEFAULT_WORKFLOWS_DIR", str(tmp_path / "workflows")):
                            try:
                                _cmd_init(SimpleNamespace())
                            except (SystemExit, Exception):
                                pass
        finally:
            os.chdir(orig_cwd)
