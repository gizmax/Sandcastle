"""Deep tests for browser automation modes in sandcastle/engine/executor.py.

Covers:
1. LightPanda fallback chain (settings.lightpanda_path -> LIGHTPANDA_PATH env -> "lightpanda" default)
2. Browserbase session lifecycle (POST creates session, DELETE cleans up even on error)
3. Missing credentials (browserbase mode without API key returns failed immediately)
4. URL validation (javascript: URLs rejected, http/https accepted)
5. Timeout handling (browser step with 0 timeout fails gracefully)
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx
import pytest

from sandcastle.config import Settings
from sandcastle.engine.dag import BrowserConfig, StepDefinition
from sandcastle.engine.executor import RunContext, StepResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(
    step_id: str = "browser-deep",
    prompt: str = "Do something",
    browser_config: BrowserConfig | None = None,
    model: str = "sonnet",
    timeout: int = 30,
    **kwargs: Any,
) -> StepDefinition:
    return StepDefinition(
        id=step_id,
        prompt=prompt,
        type="browser",
        model=model,
        timeout=timeout,
        browser_config=browser_config or BrowserConfig(),
        **kwargs,
    )


def _make_context(run_id: str = "run-deep-123") -> RunContext:
    return RunContext(run_id=run_id, input={})


def _make_mock_storage() -> MagicMock:
    storage = MagicMock()
    storage.get = AsyncMock(return_value=None)
    storage.put = AsyncMock()
    return storage


def _make_mock_sandbox() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# Part 1: LightPanda fallback chain
# ---------------------------------------------------------------------------


class TestLightPandaFallbackChain:
    """Verify the binary path resolution: settings -> env var -> 'lightpanda' default."""

    @pytest.mark.asyncio
    async def test_uses_settings_lightpanda_path_when_set(self):
        """When settings.lightpanda_path is set, that path is used for the binary."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="lightpanda", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        launched_with = []

        async def fake_subprocess(*args, **kwargs):
            launched_with.extend(args)
            proc = MagicMock()
            proc.terminate = MagicMock()
            proc.wait = AsyncMock()
            # Simulate FileNotFoundError so we short-circuit quickly
            raise FileNotFoundError("not found")

        with patch("sandcastle.config.settings") as mock_settings, \
             patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            mock_settings.lightpanda_path = "/custom/path/lightpanda"
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_lightpanda_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert result.status == "failed"
        # The binary path should be the custom one
        assert launched_with[0] == "/custom/path/lightpanda"

    @pytest.mark.asyncio
    async def test_uses_env_var_when_settings_path_empty(self):
        """When settings.lightpanda_path is empty, LIGHTPANDA_PATH env var is used."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="lightpanda", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        launched_with = []

        async def fake_subprocess(*args, **kwargs):
            launched_with.extend(args)
            raise FileNotFoundError("not found")

        with patch("sandcastle.config.settings") as mock_settings, \
             patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess), \
             patch.dict(os.environ, {"LIGHTPANDA_PATH": "/env/lightpanda"}):
            mock_settings.lightpanda_path = ""
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_lightpanda_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert result.status == "failed"
        assert launched_with[0] == "/env/lightpanda"

    @pytest.mark.asyncio
    async def test_defaults_to_lightpanda_when_no_config_or_env(self):
        """When neither settings nor env var is set, 'lightpanda' is the default binary."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="lightpanda", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        launched_with = []

        async def fake_subprocess(*args, **kwargs):
            launched_with.extend(args)
            raise FileNotFoundError("not found")

        env_without_lp = {k: v for k, v in os.environ.items() if k != "LIGHTPANDA_PATH"}
        with patch("sandcastle.config.settings") as mock_settings, \
             patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess), \
             patch.dict(os.environ, env_without_lp, clear=True):
            mock_settings.lightpanda_path = ""
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_lightpanda_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert result.status == "failed"
        assert launched_with[0] == "lightpanda"

    @pytest.mark.asyncio
    async def test_missing_binary_returns_helpful_error(self):
        """FileNotFoundError produces an informative error message."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="lightpanda", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        async def fake_subprocess(*args, **kwargs):
            raise FileNotFoundError("not found")

        env_without_lp = {k: v for k, v in os.environ.items() if k != "LIGHTPANDA_PATH"}
        with patch("sandcastle.config.settings") as mock_settings, \
             patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess), \
             patch.dict(os.environ, env_without_lp, clear=True):
            mock_settings.lightpanda_path = ""

            result = await exec_mod._browser_lightpanda_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert result.status == "failed"
        assert "lightpanda" in (result.error or "").lower()
        # Should mention how to fix it
        assert "LIGHTPANDA_PATH" in (result.error or "") or "PATH" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_start_url_returns_failed(self):
        """LightPanda mode with no start_url fails immediately."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="lightpanda", start_url="")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        result = await exec_mod._browser_lightpanda_mode(
            step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
            _make_mock_storage(), time.monotonic()
        )

        assert result.status == "failed"
        assert result.error is not None
        assert "start_url" in result.error.lower() or "url" in result.error.lower()


# ---------------------------------------------------------------------------
# Part 2: Browserbase session lifecycle
# ---------------------------------------------------------------------------


class TestBrowserbaseSessionLifecycle:
    """Verify POST creates session, DELETE always runs cleanup."""

    def _make_httpx_response(
        self,
        status_code: int = 200,
        json_data: dict | None = None,
    ) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        if status_code >= 400:
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=resp
            )
        else:
            resp.raise_for_status.return_value = None
        return resp

    @pytest.mark.asyncio
    async def test_session_created_with_post(self):
        """Browserbase mode POSTs to /v1/sessions to create a session."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="browserbase", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        post_calls = []

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        post_resp = self._make_httpx_response(
            200, {"id": "sess-abc123", "connectUrl": "ws://bb.local/cdp"}
        )
        mock_client.post = AsyncMock(return_value=post_resp)
        mock_client.delete = AsyncMock(return_value=MagicMock(status_code=200))

        # Playwright mock that raises to short-circuit the browser part
        mock_pw_module = MagicMock()
        mock_pw_module.chromium.connect_over_cdp = AsyncMock(
            side_effect=Exception("cdp connect failed - expected in test")
        )
        mock_pw_ctx = AsyncMock()
        mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw_module)
        mock_pw_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_playwright_module = MagicMock()
        mock_playwright_module.async_playwright = MagicMock(return_value=mock_pw_ctx)

        with patch("sandcastle.config.settings") as mock_settings, \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch.dict("sys.modules", {
                 "playwright": mock_playwright_module,
                 "playwright.async_api": mock_playwright_module,
             }):
            mock_settings.browserbase_api_key = "test-key-123"
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_browserbase_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        # POST was called (at least once - the session creation call)
        mock_client.post.assert_called()
        post_url = mock_client.post.call_args[0][0]
        assert "sessions" in post_url

    @pytest.mark.asyncio
    async def test_delete_called_on_success(self):
        """Session DELETE is always called via the finally block, even on playwright error."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="browserbase", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        # Use a shared list to track ALL delete calls across all client instances
        delete_called = []

        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        # POST returns a valid session
        post_resp = MagicMock(spec=httpx.Response)
        post_resp.status_code = 200
        post_resp.raise_for_status.return_value = None
        post_resp.json.return_value = {"id": "sess-xyz", "connectUrl": "ws://bb/cdp"}
        mock_async_client.post = AsyncMock(return_value=post_resp)

        # DELETE records the call
        async def track_delete(url, **kwargs):
            delete_called.append(url)
            return MagicMock(status_code=200)

        mock_async_client.delete = track_delete

        # Playwright fails to short-circuit (but that's OK - finally still runs)
        mock_pw_module = MagicMock()
        mock_pw_module.chromium.connect_over_cdp = AsyncMock(
            side_effect=Exception("cdp connect failed - expected")
        )
        mock_pw_ctx = AsyncMock()
        mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw_module)
        mock_pw_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_playwright_module = MagicMock()
        mock_playwright_module.async_playwright = MagicMock(return_value=mock_pw_ctx)

        with patch("sandcastle.config.settings") as mock_settings, \
             patch("httpx.AsyncClient", return_value=mock_async_client), \
             patch.dict("sys.modules", {
                 "playwright": mock_playwright_module,
                 "playwright.async_api": mock_playwright_module,
             }):
            mock_settings.browserbase_api_key = "test-key"
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_browserbase_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        # Session DELETE must have been called with the session ID
        assert len(delete_called) >= 1
        assert any("sess-xyz" in url for url in delete_called)

    @pytest.mark.asyncio
    async def test_delete_called_even_on_playwright_error(self):
        """Session is cleaned up even when Playwright connection fails."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="browserbase", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        delete_called = []

        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        post_resp = MagicMock(spec=httpx.Response)
        post_resp.status_code = 200
        post_resp.raise_for_status.return_value = None
        post_resp.json.return_value = {"id": "sess-cleanup-test", "connectUrl": "ws://bb/cdp"}
        mock_async_client.post = AsyncMock(return_value=post_resp)

        async def track_delete(url, **kwargs):
            delete_called.append(url)
            return MagicMock(status_code=200)

        mock_async_client.delete = track_delete

        # Playwright raises RuntimeError to simulate failure
        mock_pw_module = MagicMock()
        mock_pw_module.chromium.connect_over_cdp = AsyncMock(
            side_effect=RuntimeError("playwright failed")
        )
        mock_pw_ctx = AsyncMock()
        mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw_module)
        mock_pw_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_playwright_module = MagicMock()
        mock_playwright_module.async_playwright = MagicMock(return_value=mock_pw_ctx)

        with patch("sandcastle.config.settings") as mock_settings, \
             patch("httpx.AsyncClient", return_value=mock_async_client), \
             patch.dict("sys.modules", {
                 "playwright": mock_playwright_module,
                 "playwright.async_api": mock_playwright_module,
             }):
            mock_settings.browserbase_api_key = "bb-key"
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_browserbase_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        # Even on playwright failure, DELETE should be called
        assert result.status == "failed"
        assert any("sess-cleanup-test" in url for url in delete_called)

    @pytest.mark.asyncio
    async def test_http_error_creating_session_returns_failed(self):
        """HTTP error from Browserbase API returns a failed StepResult."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="browserbase", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        resp_mock = MagicMock(spec=httpx.Response)
        resp_mock.status_code = 401
        resp_mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=resp_mock
        )
        mock_async_client.post = AsyncMock(return_value=resp_mock)
        mock_async_client.delete = AsyncMock(return_value=MagicMock(status_code=200))

        with patch("sandcastle.config.settings") as mock_settings, \
             patch("httpx.AsyncClient", return_value=mock_async_client):
            mock_settings.browserbase_api_key = "bad-key"
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_browserbase_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert result.status == "failed"
        assert result.error is not None
        assert "401" in result.error or "session" in result.error.lower()


# ---------------------------------------------------------------------------
# Part 3: Missing credentials
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    """Browserbase mode without API key fails immediately without making HTTP calls."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_failed_immediately(self):
        """Missing BROWSERBASE_API_KEY causes immediate failure."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="browserbase", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        http_called = []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                http_called.append(url)
                return MagicMock()

        env_no_key = {k: v for k, v in os.environ.items() if k != "BROWSERBASE_API_KEY"}
        with patch("sandcastle.config.settings") as mock_settings, \
             patch("httpx.AsyncClient", FakeClient), \
             patch.dict(os.environ, env_no_key, clear=True):
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_browserbase_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert result.status == "failed"
        assert "BROWSERBASE_API_KEY" in (result.error or "")
        # No HTTP calls should have been made
        assert http_called == []

    @pytest.mark.asyncio
    async def test_no_api_key_error_message_mentions_env_var(self):
        """Error message when API key is missing mentions BROWSERBASE_API_KEY."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="browserbase", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        env_no_key = {k: v for k, v in os.environ.items() if k != "BROWSERBASE_API_KEY"}
        with patch("sandcastle.config.settings") as mock_settings, \
             patch.dict(os.environ, env_no_key, clear=True):
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_browserbase_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert "BROWSERBASE_API_KEY" in (result.error or "")

    @pytest.mark.asyncio
    async def test_api_key_from_env_var_is_accepted(self):
        """If settings has no key but env var is set, key is picked up."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="browserbase", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        # The function should proceed past the key check and attempt HTTP
        post_called = []

        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        async def track_post(url, **kwargs):
            post_called.append(url)
            # Raise to short-circuit
            raise httpx.ConnectError("connection refused", request=MagicMock())

        mock_async_client.post = track_post
        mock_async_client.delete = AsyncMock(return_value=MagicMock(status_code=200))

        with patch("sandcastle.config.settings") as mock_settings, \
             patch("httpx.AsyncClient", return_value=mock_async_client), \
             patch.dict(os.environ, {"BROWSERBASE_API_KEY": "env-key-abc"}):
            mock_settings.browserbase_api_key = ""
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_browserbase_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        # The function proceeded past key check and attempted HTTP (even though it failed)
        assert len(post_called) > 0
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# Part 4: URL validation
# ---------------------------------------------------------------------------


class TestUrlValidation:
    """_validate_browser_url rejects dangerous schemes and accepts http/https."""

    def test_javascript_url_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="[Dd]angerous"):
            _validate_browser_url("javascript:alert(1)")

    def test_javascript_url_with_leading_spaces_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("  javascript:void(0)")

    def test_javascript_url_uppercase_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("JAVASCRIPT:alert('xss')")

    def test_data_url_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("data:text/html,<script>alert(1)</script>")

    def test_file_url_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("file:///etc/passwd")

    def test_vbscript_url_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("vbscript:msgbox(1)")

    def test_blob_url_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("blob:https://example.com/some-id")

    def test_http_url_accepted(self):
        from sandcastle.engine.executor import _validate_browser_url
        url = _validate_browser_url("http://example.com")
        assert url == "http://example.com"

    def test_https_url_accepted(self):
        from sandcastle.engine.executor import _validate_browser_url
        url = _validate_browser_url("https://example.com/path?q=1")
        assert url == "https://example.com/path?q=1"

    def test_no_scheme_prepends_https(self):
        from sandcastle.engine.executor import _validate_browser_url
        url = _validate_browser_url("example.com/page")
        assert url.startswith("https://")

    def test_empty_url_raises(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("")

    def test_whitespace_only_url_raises(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("   ")

    def test_ftp_url_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="[Uu]nsupported"):
            _validate_browser_url("ftp://files.example.com")


# ---------------------------------------------------------------------------
# Part 5: Timeout handling
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    """Browser steps with zero or very low timeouts must fail gracefully."""

    @pytest.mark.asyncio
    async def test_lightpanda_zero_timeout_fails_gracefully(self):
        """LightPanda mode with timeout=0 does not crash the server."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(
            mode="lightpanda",
            start_url="https://example.com",
            timeout_seconds=0,
        )
        step = _make_step(browser_config=cfg, timeout=0)
        ctx = _make_context()

        async def fake_subprocess(*args, **kwargs):
            raise FileNotFoundError("lightpanda not found")

        env_clean = {k: v for k, v in os.environ.items() if k != "LIGHTPANDA_PATH"}
        with patch("sandcastle.config.settings") as mock_settings, \
             patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess), \
             patch.dict(os.environ, env_clean, clear=True):
            mock_settings.lightpanda_path = ""

            # Must not raise
            result = await exec_mod._browser_lightpanda_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert result.status == "failed"
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_execute_browser_step_no_browser_config_fails(self):
        """Browser step missing browser_config entirely returns a failed result."""
        from sandcastle.engine.executor import _execute_browser_step

        # Create a step with no browser_config
        step = StepDefinition(
            id="no-cfg",
            prompt="do it",
            type="browser",
            model="sonnet",
        )
        ctx = _make_context()

        result = await _execute_browser_step(
            step, ctx, _make_mock_sandbox(), _make_mock_storage()
        )

        assert result.status == "failed"
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_browserbase_network_timeout_returns_failed(self):
        """Network timeout during Browserbase session creation returns failed."""
        from sandcastle.engine import executor as exec_mod

        cfg = BrowserConfig(mode="browserbase", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        ctx = _make_context()

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                raise httpx.TimeoutException("Connection timed out", request=MagicMock())

            async def delete(self, url, **kwargs):
                return MagicMock()

        with patch("sandcastle.config.settings") as mock_settings, \
             patch("httpx.AsyncClient", FakeClient):
            mock_settings.browserbase_api_key = "valid-key"
            mock_settings.browserbase_project_id = ""

            result = await exec_mod._browser_browserbase_mode(
                step, cfg, step.prompt, None, ctx, _make_mock_sandbox(),
                _make_mock_storage(), time.monotonic()
            )

        assert result.status == "failed"
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_all_browser_modes_have_duration(self):
        """Every browser mode failure includes a duration_seconds value >= 0."""
        from sandcastle.engine.executor import _execute_browser_step

        modes = ["lightpanda", "browserbase", "dom", "playwright", "computer_use"]

        async def fake_subprocess(*args, **kwargs):
            raise FileNotFoundError("not found")

        class FakeHttpxClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                raise httpx.ConnectError("connection refused", request=MagicMock())

            async def delete(self, url, **kwargs):
                return MagicMock()

        env_clean = {k: v for k, v in os.environ.items()
                     if k not in ("LIGHTPANDA_PATH", "BROWSERBASE_API_KEY")}

        for mode in modes:
            cfg = BrowserConfig(mode=mode, start_url="https://example.com")
            step = _make_step(step_id=f"test-{mode}", browser_config=cfg)
            ctx = _make_context()

            with patch("sandcastle.config.settings") as mock_settings, \
                 patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess), \
                 patch("httpx.AsyncClient", FakeHttpxClient), \
                 patch.dict(os.environ, env_clean, clear=True):
                mock_settings.lightpanda_path = ""
                mock_settings.browserbase_api_key = ""
                mock_settings.browserbase_project_id = ""
                # LLM steps for dom/playwright/computer_use would call execute_step
                # Patch it to avoid real API calls
                with patch("sandcastle.engine.executor.execute_step_with_retry",
                           AsyncMock(return_value=StepResult(step_id=step.id, status="failed",
                                                             error="mocked"))):
                    result = await _execute_browser_step(step, ctx, _make_mock_sandbox(),
                                                         _make_mock_storage())

            assert result.duration_seconds is not None, f"mode={mode} missing duration"
            assert result.duration_seconds >= 0, f"mode={mode} negative duration"
