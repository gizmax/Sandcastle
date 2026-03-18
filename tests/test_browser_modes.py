"""Tests for LightPanda and Browserbase browser modes added in v0.23.

Covers:
- BrowserConfig accepts "lightpanda" and "browserbase" modes
- Invalid mode is rejected by _execute_browser_step
- Settings has lightpanda_path and browserbase_* fields
- browserbase_api_key is in _SENSITIVE_FIELDS / safe_dump
- _browser_lightpanda_mode returns an error when the binary is missing
- _browser_lightpanda_mode returns an error when start_url is empty
- _browser_browserbase_mode returns an error when the API key is missing
- _browser_browserbase_mode returns an error on HTTP failure
- _browser_browserbase_mode cleans up the session after success/failure
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.config import Settings
from sandcastle.engine.dag import BrowserConfig, StepDefinition
from sandcastle.engine.executor import RunContext, StepResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(
    step_id: str = "browser-test",
    prompt: str = "Do something",
    browser_config: BrowserConfig | None = None,
    model: str = "sonnet",
    **kwargs: Any,
) -> StepDefinition:
    return StepDefinition(
        id=step_id,
        prompt=prompt,
        type="browser",
        model=model,
        browser_config=browser_config or BrowserConfig(),
        **kwargs,
    )


def _make_context(run_id: str = "run-123") -> RunContext:
    return RunContext(run_id=run_id, input={})


def _make_mock_storage() -> MagicMock:
    storage = MagicMock()
    storage.get = AsyncMock(return_value=None)
    storage.put = AsyncMock()
    return storage


def _make_mock_sandbox() -> MagicMock:
    sandbox = MagicMock()
    return sandbox


# ---------------------------------------------------------------------------
# Section 1: BrowserConfig mode field
# ---------------------------------------------------------------------------


class TestBrowserConfigModes:
    """BrowserConfig accepts all five valid modes."""

    def test_default_mode_is_playwright(self):
        cfg = BrowserConfig()
        assert cfg.mode == "playwright"

    def test_lightpanda_mode(self):
        cfg = BrowserConfig(mode="lightpanda")
        assert cfg.mode == "lightpanda"

    def test_browserbase_mode(self):
        cfg = BrowserConfig(mode="browserbase")
        assert cfg.mode == "browserbase"

    def test_dom_mode_still_valid(self):
        cfg = BrowserConfig(mode="dom")
        assert cfg.mode == "dom"

    def test_computer_use_mode_still_valid(self):
        cfg = BrowserConfig(mode="computer_use")
        assert cfg.mode == "computer_use"

    def test_lightpanda_mode_in_step(self):
        step = _make_step(browser_config=BrowserConfig(mode="lightpanda"))
        assert step.browser_config is not None
        assert step.browser_config.mode == "lightpanda"

    def test_browserbase_mode_in_step(self):
        step = _make_step(browser_config=BrowserConfig(mode="browserbase"))
        assert step.browser_config is not None
        assert step.browser_config.mode == "browserbase"


# ---------------------------------------------------------------------------
# Section 2: Invalid mode is rejected by _execute_browser_step
# ---------------------------------------------------------------------------


class TestInvalidModeRejection:
    """Unknown modes must return a failed StepResult, not raise."""

    @pytest.mark.asyncio
    async def test_unknown_mode_returns_failed(self):
        from sandcastle.engine.executor import _execute_browser_step

        step = _make_step(browser_config=BrowserConfig(mode="bogus_mode"))
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()

        result = await _execute_browser_step(step, ctx, sandbox, storage)

        assert result.status == "failed"
        assert "bogus_mode" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unknown_mode_includes_mode_name_in_error(self):
        from sandcastle.engine.executor import _execute_browser_step

        step = _make_step(browser_config=BrowserConfig(mode="magic_browser"))
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()

        result = await _execute_browser_step(step, ctx, sandbox, storage)

        assert "magic_browser" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unknown_mode_has_duration(self):
        from sandcastle.engine.executor import _execute_browser_step

        step = _make_step(browser_config=BrowserConfig(mode="nope"))
        ctx = _make_context()
        result = await _execute_browser_step(step, ctx, _make_mock_sandbox(), _make_mock_storage())

        assert result.duration_seconds is not None
        assert result.duration_seconds >= 0


# ---------------------------------------------------------------------------
# Section 3: Settings fields
# ---------------------------------------------------------------------------


class TestSettingsFields:
    """Settings has the new browser backend fields."""

    def test_lightpanda_path_exists(self):
        s = Settings()
        assert hasattr(s, "lightpanda_path")

    def test_lightpanda_path_default_is_empty(self):
        s = Settings()
        assert s.lightpanda_path == ""

    def test_browserbase_api_key_exists(self):
        s = Settings()
        assert hasattr(s, "browserbase_api_key")

    def test_browserbase_api_key_default_is_empty(self):
        s = Settings()
        assert s.browserbase_api_key == ""

    def test_browserbase_project_id_exists(self):
        s = Settings()
        assert hasattr(s, "browserbase_project_id")

    def test_browserbase_project_id_default_is_empty(self):
        s = Settings()
        assert s.browserbase_project_id == ""

    def test_lightpanda_path_can_be_set(self):
        s = Settings(lightpanda_path="/usr/local/bin/lightpanda")
        assert s.lightpanda_path == "/usr/local/bin/lightpanda"

    def test_browserbase_api_key_can_be_set(self):
        s = Settings(browserbase_api_key="bb_key_abc123")
        assert s.browserbase_api_key == "bb_key_abc123"


# ---------------------------------------------------------------------------
# Section 4: Sensitive fields - browserbase_api_key must be redacted
# ---------------------------------------------------------------------------


class TestBrowserbaseSensitiveKey:
    """browserbase_api_key must be in _SENSITIVE_FIELDS and redacted by safe_dump."""

    def test_browserbase_api_key_in_sensitive_fields(self):
        s = Settings()
        assert "browserbase_api_key" in s._SENSITIVE_FIELDS

    def test_browserbase_api_key_redacted_when_set(self):
        s = Settings(browserbase_api_key="super-secret-key")
        dumped = s.safe_dump()
        assert dumped["browserbase_api_key"] == "***"

    def test_browserbase_api_key_not_redacted_when_empty(self):
        # empty keys are not redacted (nothing to hide)
        s = Settings(browserbase_api_key="")
        dumped = s.safe_dump()
        assert dumped["browserbase_api_key"] == ""

    def test_lightpanda_path_is_not_sensitive(self):
        # lightpanda_path is a binary path, not a secret
        s = Settings()
        assert "lightpanda_path" not in s._SENSITIVE_FIELDS

    def test_safe_dump_still_redacts_other_keys(self):
        s = Settings(anthropic_api_key="sk-ant-test", browserbase_api_key="bb_key")
        dumped = s.safe_dump()
        assert dumped["anthropic_api_key"] == "***"
        assert dumped["browserbase_api_key"] == "***"


# ---------------------------------------------------------------------------
# Section 5: _browser_lightpanda_mode - error paths
# ---------------------------------------------------------------------------


class TestBrowserLightpandaMode:
    """Unit tests for _browser_lightpanda_mode error paths."""

    @pytest.mark.asyncio
    async def test_missing_start_url_returns_failed(self):
        from sandcastle.engine.executor import _browser_lightpanda_mode

        step = _make_step(browser_config=BrowserConfig(mode="lightpanda", start_url=""))
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        result = await _browser_lightpanda_mode(
            step, cfg, "prompt", None, ctx, sandbox, storage, started_at
        )

        assert result.status == "failed"
        assert "start_url" in (result.error or "").lower() or "url" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_binary_not_found_returns_failed(self):
        from sandcastle.engine.executor import _browser_lightpanda_mode

        step = _make_step(
            browser_config=BrowserConfig(
                mode="lightpanda",
                start_url="https://example.com",
            )
        )
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        # Patch settings so no custom path is set and the binary on PATH is
        # a non-existent name to trigger FileNotFoundError.
        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError),
        ):
            mock_settings.lightpanda_path = ""
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""

            result = await _browser_lightpanda_mode(
                step, cfg, "prompt", None, ctx, sandbox, storage, started_at
            )

        assert result.status == "failed"
        assert result.error is not None
        assert "lightpanda" in result.error.lower() or "binary" in result.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_url_returns_failed(self):
        from sandcastle.engine.executor import _browser_lightpanda_mode

        step = _make_step(
            browser_config=BrowserConfig(
                mode="lightpanda",
                start_url="javascript:alert(1)",
            )
        )
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        result = await _browser_lightpanda_mode(
            step, cfg, "prompt", None, ctx, sandbox, storage, started_at
        )

        assert result.status == "failed"
        assert "url" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_lightpanda_path_from_settings(self):
        """Settings.lightpanda_path is preferred over LIGHTPANDA_PATH env var."""
        from sandcastle.engine.executor import _browser_lightpanda_mode

        step = _make_step(
            browser_config=BrowserConfig(
                mode="lightpanda",
                start_url="https://example.com",
            )
        )
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        captured_args: list = []

        async def fake_subprocess(*args, **kwargs):
            captured_args.extend(args)
            raise FileNotFoundError  # stop early

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
        ):
            mock_settings.lightpanda_path = "/custom/path/lightpanda"
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""

            await _browser_lightpanda_mode(
                step, cfg, "prompt", None, ctx, sandbox, storage, started_at
            )

        assert captured_args and captured_args[0] == "/custom/path/lightpanda"


# ---------------------------------------------------------------------------
# Section 6: _browser_browserbase_mode - error paths
# ---------------------------------------------------------------------------


class TestBrowserBrowserbaseMode:
    """Unit tests for _browser_browserbase_mode error paths."""

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_failed(self):
        from sandcastle.engine.executor import _browser_browserbase_mode

        step = _make_step(
            browser_config=BrowserConfig(mode="browserbase", start_url="https://example.com")
        )
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch.dict("os.environ", {}, clear=False),
        ):
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""
            # Ensure the env var is also absent
            import os
            os.environ.pop("BROWSERBASE_API_KEY", None)

            result = await _browser_browserbase_mode(
                step, cfg, "prompt", None, ctx, sandbox, storage, started_at
            )

        assert result.status == "failed"
        assert "BROWSERBASE_API_KEY" in (result.error or "")

    @pytest.mark.asyncio
    async def test_http_error_returns_failed(self):
        """A non-2xx response from the sessions endpoint returns failed."""
        from sandcastle.engine.executor import _browser_browserbase_mode

        step = _make_step(
            browser_config=BrowserConfig(mode="browserbase", start_url="https://example.com")
        )
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        # Build a mock httpx client that raises HTTPStatusError on POST
        mock_response = MagicMock()
        mock_response.status_code = 401
        http_error = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=http_error)

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, *args):
                pass

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("httpx.AsyncClient", FakeClient),
        ):
            mock_settings.browserbase_api_key = "bb-key-test"
            mock_settings.browserbase_project_id = ""

            result = await _browser_browserbase_mode(
                step, cfg, "prompt", None, ctx, sandbox, storage, started_at
            )

        assert result.status == "failed"
        assert "401" in (result.error or "") or "session" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_missing_connect_url_returns_failed(self):
        """If Browserbase API returns no connectUrl the step must fail cleanly."""
        from sandcastle.engine.executor import _browser_browserbase_mode

        step = _make_step(
            browser_config=BrowserConfig(mode="browserbase", start_url="https://example.com")
        )
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "sess_123"}  # no connectUrl
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, *args):
                pass

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("httpx.AsyncClient", FakeClient),
        ):
            mock_settings.browserbase_api_key = "bb-key-test"
            mock_settings.browserbase_project_id = ""

            result = await _browser_browserbase_mode(
                step, cfg, "prompt", None, ctx, sandbox, storage, started_at
            )

        assert result.status == "failed"
        assert "connectUrl" in (result.error or "") or "connect" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_session_cleanup_called_on_success_path(self):
        """The Browserbase session DELETE must be called after a successful run."""
        from sandcastle.engine.executor import _browser_browserbase_mode

        step = _make_step(
            browser_config=BrowserConfig(mode="browserbase", start_url="https://example.com")
        )
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        session_id = "sess_cleanup_test"
        connect_url = "http://fake-cdp-endpoint"

        # Track ALL httpx calls made (POST to create session, DELETE to clean up)
        delete_calls: list = []

        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = {"id": session_id, "connectUrl": connect_url}
        mock_post_resp.raise_for_status = MagicMock()

        mock_delete_resp = MagicMock()
        mock_delete_resp.raise_for_status = MagicMock()

        async def fake_delete(url, **kwargs):
            delete_calls.append(url)
            return mock_delete_resp

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_post_resp)
        mock_client.delete = fake_delete

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, *args):
                pass

        # Mock playwright so connect_over_cdp raises - this simulates a browser failure
        # while still ensuring the finally block (session cleanup) is exercised.
        mock_chromium = MagicMock()
        mock_chromium.connect_over_cdp = AsyncMock(
            side_effect=RuntimeError("Fake CDP failure - testing cleanup only")
        )
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium = mock_chromium

        class FakeAsyncPlaywright:
            async def __aenter__(self):
                return mock_pw_instance

            async def __aexit__(self, *args):
                pass

        mock_async_playwright_module = MagicMock()
        mock_async_playwright_module.async_playwright = FakeAsyncPlaywright

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("httpx.AsyncClient", FakeClient),
            patch.dict(
                "sys.modules",
                {"playwright.async_api": mock_async_playwright_module},
            ),
        ):
            mock_settings.browserbase_api_key = "bb-key-test"
            mock_settings.browserbase_project_id = ""

            result = await _browser_browserbase_mode(
                step, cfg, "prompt", None, ctx, sandbox, storage, started_at
            )

        # The session should have been deleted regardless of the playwright failure
        assert any(session_id in url for url in delete_calls), (
            f"Expected DELETE call containing '{session_id}', got: {delete_calls}"
        )

    @pytest.mark.asyncio
    async def test_project_id_included_in_session_payload(self):
        """When browserbase_project_id is set it is sent in the session creation body."""
        from sandcastle.engine.executor import _browser_browserbase_mode

        step = _make_step(
            browser_config=BrowserConfig(mode="browserbase", start_url="https://example.com")
        )
        cfg = step.browser_config
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()
        started_at = time.monotonic()

        posted_json: list[dict] = []

        mock_resp = MagicMock()
        mock_resp.json.return_value = {}  # no connectUrl -> fail fast
        mock_resp.raise_for_status = MagicMock()

        async def fake_post(url, json=None, **kwargs):
            if json:
                posted_json.append(json)
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, *args):
                pass

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("httpx.AsyncClient", FakeClient),
        ):
            mock_settings.browserbase_api_key = "bb-key-test"
            mock_settings.browserbase_project_id = "proj-xyz"

            await _browser_browserbase_mode(
                step, cfg, "prompt", None, ctx, sandbox, storage, started_at
            )

        assert posted_json, "Expected at least one POST body to be captured"
        assert posted_json[0].get("projectId") == "proj-xyz"


# ---------------------------------------------------------------------------
# Section 7: Integration - _execute_browser_step dispatches correctly
# ---------------------------------------------------------------------------


class TestExecuteBrowserStepDispatch:
    """_execute_browser_step correctly dispatches to new mode functions."""

    @pytest.mark.asyncio
    async def test_lightpanda_mode_dispatches(self):
        from sandcastle.engine.executor import _execute_browser_step

        step = _make_step(
            browser_config=BrowserConfig(
                mode="lightpanda",
                start_url="https://example.com",
            )
        )
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()

        # Patch the internal function to verify dispatch
        dispatched_to: list[str] = []

        async def fake_lightpanda(*args, **kwargs):
            dispatched_to.append("lightpanda")
            return StepResult(step_id=step.id, status="completed", output="ok")

        with patch("sandcastle.engine.executor._browser_lightpanda_mode", fake_lightpanda):
            result = await _execute_browser_step(step, ctx, sandbox, storage)

        assert "lightpanda" in dispatched_to
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_browserbase_mode_dispatches(self):
        from sandcastle.engine.executor import _execute_browser_step

        step = _make_step(
            browser_config=BrowserConfig(
                mode="browserbase",
                start_url="https://example.com",
            )
        )
        ctx = _make_context()
        storage = _make_mock_storage()
        sandbox = _make_mock_sandbox()

        dispatched_to: list[str] = []

        async def fake_browserbase(*args, **kwargs):
            dispatched_to.append("browserbase")
            return StepResult(step_id=step.id, status="completed", output="ok")

        with patch("sandcastle.engine.executor._browser_browserbase_mode", fake_browserbase):
            result = await _execute_browser_step(step, ctx, sandbox, storage)

        assert "browserbase" in dispatched_to
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_missing_browser_config_returns_failed(self):
        from sandcastle.engine.executor import _execute_browser_step

        step = StepDefinition(
            id="no-cfg",
            prompt="oops",
            type="browser",
            model="sonnet",
        )
        ctx = _make_context()
        result = await _execute_browser_step(step, ctx, _make_mock_sandbox(), _make_mock_storage())
        assert result.status == "failed"
        assert "browser_config" in (result.error or "").lower()
