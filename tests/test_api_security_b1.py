"""Regression tests for API security fixes in wave B1."""

from __future__ import annotations

import asyncio
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sandcastle.api import routes
from sandcastle.api.schemas import UpdateRequest


def _request() -> MagicMock:
    request = MagicMock()
    request.client = SimpleNamespace(host="127.0.0.1")
    return request


def _configure_update(monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "update_blackout_start", "")
    monkeypatch.setattr(routes.settings, "update_blackout_end", "")
    monkeypatch.setattr(routes.settings, "update_channel", "stable")
    monkeypatch.setattr(routes, "_require_admin", lambda request: None)


def _process(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    process = MagicMock()
    process.returncode = returncode
    process.communicate = AsyncMock(return_value=(stdout, stderr))
    return process


def test_pre_update_env_backup_is_private_and_under_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes, "_SANDCASTLE_HOME", tmp_path / "state")
    monkeypatch.setattr(routes.settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(routes.settings, "database_url", "")
    Path(".env").write_text("API_KEY=super-secret\n")

    backup_path = asyncio.run(routes._pre_update_backup("1.0.0"))

    assert backup_path is not None
    assert backup_path.parent == tmp_path / "data" / "backups"
    assert backup_path.name.startswith(".env.pre-update-")
    assert backup_path.read_text() == "API_KEY=super-secret\n"
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    assert not Path(".env.pre-update").exists()


def test_successful_update_removes_environment_backup(monkeypatch, tmp_path: Path):
    _configure_update(monkeypatch)
    backup_path = tmp_path / "data" / "backups" / ".env.pre-update-test"
    backup_path.parent.mkdir(parents=True)
    backup_path.write_text("API_KEY=super-secret\n")
    install = _process(0)
    verify = _process(0, b"99.0.0\n")

    with (
        patch("sandcastle.api.routes._pre_update_backup", new=AsyncMock(return_value=backup_path)),
        patch("sandcastle.api.routes._emit_update_audit", new=AsyncMock()),
        patch(
            "sandcastle.api.routes.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=[install, verify]),
        ) as mock_exec,
    ):
        response = asyncio.run(routes.trigger_update(_request(), UpdateRequest(target_version="99.0.0")))

    assert response.data.status == "success"
    assert not backup_path.exists()
    assert mock_exec.await_args_list[0].args[:4] == (sys.executable, "-m", "pip", "install")
    assert mock_exec.await_args_list[1].args[:2] == (sys.executable, "-c")


def test_failed_update_keeps_environment_backup(monkeypatch, tmp_path: Path):
    _configure_update(monkeypatch)
    backup_path = tmp_path / "data" / "backups" / ".env.pre-update-test"
    backup_path.parent.mkdir(parents=True)
    backup_path.write_text("API_KEY=super-secret\n")

    with (
        patch("sandcastle.api.routes._pre_update_backup", new=AsyncMock(return_value=backup_path)),
        patch("sandcastle.api.routes._emit_update_audit", new=AsyncMock()),
        patch(
            "sandcastle.api.routes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_process(1, stderr=b"pip failed")),
        ),
    ):
        response = asyncio.run(routes.trigger_update(_request(), UpdateRequest(target_version="99.0.0")))

    assert response.data.status == "failed"
    assert backup_path.exists()


def test_version_verification_failure_is_reported(monkeypatch, tmp_path: Path):
    _configure_update(monkeypatch)
    backup_path = tmp_path / "data" / "backups" / ".env.pre-update-test"
    backup_path.parent.mkdir(parents=True)
    backup_path.write_text("API_KEY=super-secret\n")
    install = _process(0)
    verify = _process(1, stderr=b"cannot import sandcastle")

    with (
        patch("sandcastle.api.routes._pre_update_backup", new=AsyncMock(return_value=backup_path)),
        patch("sandcastle.api.routes._emit_update_audit", new=AsyncMock()),
        patch(
            "sandcastle.api.routes.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=[install, verify]),
        ),
    ):
        response = asyncio.run(routes.trigger_update(_request(), UpdateRequest(target_version="99.0.0")))

    assert response.data.status == "failed"
    assert "Version verification failed" in response.data.error
    assert backup_path.exists()


def test_rollback_uses_running_interpreter_for_pip(monkeypatch, tmp_path: Path):
    _configure_update(monkeypatch)
    monkeypatch.setattr(routes, "_SANDCASTLE_HOME", tmp_path / "state")
    routes._SANDCASTLE_HOME.mkdir()
    (routes._SANDCASTLE_HOME / "previous_version").write_text("98.0.0")

    with (
        patch("sandcastle.api.routes._emit_update_audit", new=AsyncMock()),
        patch("sandcastle.api.routes._restore_pre_update_backup", new=AsyncMock()),
        patch(
            "sandcastle.api.routes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_process(0)),
        ) as mock_exec,
    ):
        response = asyncio.run(routes.trigger_rollback(_request()))

    assert response.data.status == "success"
    assert mock_exec.await_args.args[:4] == (sys.executable, "-m", "pip", "install")


def test_rollback_restores_environment_backup_from_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes.settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(routes.settings, "database_url", "")
    backup_dir = tmp_path / "data" / "backups"
    backup_dir.mkdir(parents=True)
    backup_path = backup_dir / ".env.pre-update-20260720T120000000000Z"
    backup_path.write_text("API_KEY=pre-update-secret\n")
    Path(".env").write_text("API_KEY=current-secret\n")

    asyncio.run(routes._restore_pre_update_backup())

    assert Path(".env").read_text() == "API_KEY=pre-update-secret\n"
