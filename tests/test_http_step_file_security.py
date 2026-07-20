"""Security coverage for HTTP-step file references and authentication."""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.config import settings
from sandcastle.engine.dag import ApprovalConfig, HttpConfig, StepDefinition
from sandcastle.engine.executor import (
    RunContext,
    WorkflowPaused,
    _execute_approval_step,
    _execute_http_step,
)


def _step(*, body: str | None = None, auth: str | None = None) -> StepDefinition:
    return StepDefinition(
        id="http-file-security",
        type="http",
        timeout=30,
        http_config=HttpConfig(
            url="https://api.example.test/upload",
            method="POST",
            body=body,
            auth=auth,
        ),
    )


def _context(*, admin_trusted: bool = False) -> RunContext:
    return RunContext(
        run_id="http-file-security", input={}, step_outputs={}, admin_trusted=admin_trusted
    )


class TestHttpStepFileSecurity:

    @pytest.mark.asyncio
    async def test_file_reference_outside_data_dir_fails(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("do not exfiltrate")
        monkeypatch.setattr(settings, "data_dir", str(data_dir))

        result = await _execute_http_step(
            _step(body=f"@file:{secret}"), _context(admin_trusted=True)
        )

        assert result.status == "failed"
        assert "outside data directory" in (result.error or "")

    @pytest.mark.asyncio
    async def test_file_reference_inside_data_dir_is_sent(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        payload = data_dir / "payload.txt"
        payload.write_text("approved content")
        monkeypatch.setattr(settings, "data_dir", str(data_dir))

        response = MagicMock()
        response.status_code = 200
        response.text = '{"ok": true}'
        response.json.return_value = {"ok": True}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.request.return_value = response

        with patch("httpx.AsyncClient", return_value=client):
            result = await _execute_http_step(
                _step(body=f"@file:{payload}"), _context(admin_trusted=True)
            )

        assert result.status == "completed"
        assert client.request.call_args.kwargs["content"] == "approved content"

    @pytest.mark.asyncio
    async def test_untrusted_file_reference_inside_data_dir_fails(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        payload = data_dir / "payload.txt"
        payload.write_text("private content")
        monkeypatch.setattr(settings, "data_dir", str(data_dir))

        result = await _execute_http_step(_step(body=f"@file:{payload}"), _context())

        assert result.status == "failed"
        assert result.error == "HTTP step: @file: references require an admin-trusted workflow"

    @pytest.mark.asyncio
    async def test_trusted_relative_file_reference_resolves_under_data_dir(
        self, tmp_path, monkeypatch
    ):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "payload.txt").write_text("relative content")
        monkeypatch.setattr(settings, "data_dir", str(data_dir))
        monkeypatch.chdir(tmp_path)

        response = MagicMock()
        response.status_code = 200
        response.text = '{"ok": true}'
        response.json.return_value = {"ok": True}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.request.return_value = response

        with patch("httpx.AsyncClient", return_value=client):
            result = await _execute_http_step(
                _step(body="@file:payload.txt"), _context(admin_trusted=True)
            )

        assert result.status == "completed"
        assert client.request.call_args.kwargs["content"] == "relative content"

    @pytest.mark.asyncio
    async def test_approval_artifacts_use_custom_data_dir(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "custom-data"
        run_id = str(uuid.uuid4())
        image_bytes = b"\x89PNG\r\n\x1a\n"
        image_data = base64.b64encode(image_bytes).decode()
        monkeypatch.setattr(settings, "data_dir", str(data_dir))

        step = StepDefinition(
            id="approval-images",
            type="approval",
            approval_config=ApprovalConfig(message="Review", show_images=["steps.image.output"]),
        )
        context = RunContext(
            run_id=run_id,
            input={},
            step_outputs={
                "image": {
                    "predictions": [{"bytesBase64Encoded": image_data, "mimeType": "image/png"}]
                }
            },
        )
        approval_id = uuid.uuid4()
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.get = AsyncMock(return_value=MagicMock(callback_url=None))

        async def set_approval_id(approval):
            approval.id = approval_id

        mock_session.refresh.side_effect = set_approval_id

        with (
            patch("sandcastle.models.db.async_session") as mock_session_ctx,
            patch("sandcastle.engine.executor._save_checkpoint", new_callable=AsyncMock),
            patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
        ):
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(WorkflowPaused):
                await _execute_approval_step(step, context, stage_index=0)

        assert (data_dir / "artifacts" / run_id / "image_0.png").read_bytes() == image_bytes

    @pytest.mark.asyncio
    async def test_missing_auth_environment_variable_fails(self, monkeypatch):
        env_name = "SANDCASTLE_TEST_MISSING_HTTP_TOKEN"
        monkeypatch.delenv(env_name, raising=False)

        result = await _execute_http_step(_step(auth=env_name), _context())

        assert result.status == "failed"
        assert result.error == f"HTTP step: auth env var {env_name} is not set"
