"""Security coverage for HTTP-step file references and authentication."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.config import settings
from sandcastle.engine.dag import HttpConfig, StepDefinition
from sandcastle.engine.executor import RunContext, _execute_http_step


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


def _context() -> RunContext:
    return RunContext(run_id="http-file-security", input={}, step_outputs={})


class TestHttpStepFileSecurity:

    @pytest.mark.asyncio
    async def test_file_reference_outside_data_dir_fails(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("do not exfiltrate")
        monkeypatch.setattr(settings, "data_dir", str(data_dir))

        result = await _execute_http_step(_step(body=f"@file:{secret}"), _context())

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
            result = await _execute_http_step(_step(body=f"@file:{payload}"), _context())

        assert result.status == "completed"
        assert client.request.call_args.kwargs["content"] == "approved content"

    @pytest.mark.asyncio
    async def test_missing_auth_environment_variable_fails(self, monkeypatch):
        env_name = "SANDCASTLE_TEST_MISSING_HTTP_TOKEN"
        monkeypatch.delenv(env_name, raising=False)

        result = await _execute_http_step(_step(auth=env_name), _context())

        assert result.status == "failed"
        assert result.error == f"HTTP step: auth env var {env_name} is not set"
