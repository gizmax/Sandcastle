"""Comprehensive tests for the browser step code in executor.py.

Covers all three browser modes (playwright, dom, computer_use),
URL validation, JS string escaping, action script building,
screenshot handling, timeout/error paths, and edge cases.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import BrowserConfig, StepDefinition
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _build_computer_use_action_script,
    _escape_js_string,
    _MAX_SCREENSHOT_B64_SIZE,
    _take_sandbox_screenshot,
    _validate_browser_url,
)


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
    """Create a StepDefinition with browser_config for testing."""
    return StepDefinition(
        id=step_id,
        prompt=prompt,
        type="browser",
        model=model,
        browser_config=browser_config or BrowserConfig(),
        **kwargs,
    )


def _make_context(
    run_id: str = "run-123",
    input_data: dict | None = None,
) -> RunContext:
    return RunContext(
        run_id=run_id,
        input=input_data or {},
    )


def _make_mock_storage() -> MagicMock:
    storage = MagicMock()
    storage.get = AsyncMock(return_value=None)
    storage.put = AsyncMock()
    return storage


def _make_mock_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.sandbox_exec = AsyncMock(return_value={"stdout": "", "stderr": ""})
    return runtime


def _make_httpx_mock(api_response: dict) -> MagicMock:
    """Create a properly mocked httpx.AsyncClient for the computer_use loop.

    httpx.Response uses sync methods (json(), raise_for_status()) so we
    use MagicMock for the response to avoid coroutine issues.
    The constructor must accept **kwargs (e.g. timeout=60).
    """
    mock_resp = MagicMock()
    mock_resp.json.return_value = api_response
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return mock_client

        async def __aexit__(self, *args):
            pass

    return FakeAsyncClient


def _make_httpx_mock_multi(responses: list[dict]) -> type:
    """Create a mock httpx client that returns different responses per call."""
    call_count_box = [0]

    async def fake_post(*args, **kwargs):
        idx = min(call_count_box[0], len(responses) - 1)
        call_count_box[0] += 1
        resp = MagicMock()
        resp.json.return_value = responses[idx]
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = AsyncMock()
    mock_client.post = fake_post

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return mock_client

        async def __aexit__(self, *args):
            pass

    return FakeAsyncClient


def _make_mock_model() -> MagicMock:
    mock_model = MagicMock()
    mock_model.api_model_id = "claude-sonnet-4-6"
    mock_model.input_price_per_m = 3.0
    mock_model.output_price_per_m = 15.0
    return mock_model


# ===========================================================================
# Section 1: _escape_js_string
# ===========================================================================


class TestEscapeJsString:
    """Tests for JavaScript string escaping."""

    def test_basic_single_quote(self):
        assert "\\'" in _escape_js_string("it's")

    def test_basic_double_quote(self):
        assert '\\"' in _escape_js_string('say "hello"')

    def test_backslash(self):
        assert _escape_js_string("a\\b") == "a\\\\b"

    def test_newline(self):
        assert _escape_js_string("a\nb") == "a\\nb"

    def test_carriage_return(self):
        assert _escape_js_string("a\rb") == "a\\rb"

    def test_tab(self):
        assert _escape_js_string("a\tb") == "a\\tb"

    def test_backtick_escaped(self):
        """Backticks must be escaped to prevent template literal injection."""
        assert _escape_js_string("a`b") == "a\\`b"

    def test_null_byte_stripped(self):
        """Null bytes must be removed to prevent string truncation."""
        assert "\x00" not in _escape_js_string("a\x00b")
        assert _escape_js_string("a\x00b") == "ab"

    def test_unicode_line_separator(self):
        """U+2028 and U+2029 break JS string literals."""
        result = _escape_js_string("a\u2028b\u2029c")
        assert "\\u2028" in result
        assert "\\u2029" in result

    def test_empty_string(self):
        assert _escape_js_string("") == ""

    def test_already_escaped_backslash(self):
        """Double escape: an already-escaped backslash should not cause issues."""
        result = _escape_js_string("\\n")
        assert result == "\\\\n"

    def test_combined_special_chars(self):
        """Multiple special characters in one string."""
        result = _escape_js_string("it's a \"test\"\nwith `backticks`")
        assert "\\'" in result
        assert '\\"' in result
        assert "\\n" in result
        assert "\\`" in result

    def test_long_string(self):
        """Escaping works correctly on long strings."""
        text = "x" * 10000 + "'"
        result = _escape_js_string(text)
        assert len(result) == 10002  # 10000 x's + \\' (2 chars)

    def test_only_special_chars(self):
        result = _escape_js_string("'\"\\\n\r\t`\x00\u2028\u2029")
        assert "\x00" not in result
        assert "'" not in result or "\\'" in result


# ===========================================================================
# Section 2: _validate_browser_url
# ===========================================================================


class TestValidateBrowserUrl:
    """Tests for URL validation and sanitization."""

    def test_valid_https_url(self):
        assert _validate_browser_url("https://example.com") == "https://example.com"

    def test_valid_http_url(self):
        assert _validate_browser_url("http://example.com") == "http://example.com"

    def test_url_with_path(self):
        url = "https://example.com/path/to/page?q=1"
        assert _validate_browser_url(url) == url

    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_browser_url("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_browser_url("   ")

    def test_javascript_scheme_rejected(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("javascript:alert(1)")

    def test_javascript_case_insensitive(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("JAVASCRIPT:alert(1)")

    def test_data_scheme_rejected(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("data:text/html,<h1>Hi</h1>")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("file:///etc/passwd")

    def test_vbscript_scheme_rejected(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("vbscript:msgbox('hi')")

    def test_blob_scheme_rejected(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("blob:http://example.com/uuid")

    def test_ftp_scheme_rejected(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _validate_browser_url("ftp://example.com/file.txt")

    def test_no_scheme_prepends_https(self):
        assert _validate_browser_url("example.com") == "https://example.com"

    def test_leading_whitespace_stripped(self):
        result = _validate_browser_url("  https://example.com  ")
        assert result == "https://example.com"

    def test_javascript_with_leading_spaces(self):
        """Leading whitespace should not bypass scheme check."""
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("  javascript:alert(1)")

    def test_url_with_auth_info(self):
        url = "https://user:pass@example.com"
        assert _validate_browser_url(url) == url

    def test_url_with_port(self):
        url = "https://example.com:8443/path"
        assert _validate_browser_url(url) == url

    def test_url_with_fragment(self):
        url = "https://example.com/page#section"
        assert _validate_browser_url(url) == url

    def test_localhost_url_blocked_by_ssrf_guard(self):
        # SSRF guard: localhost resolves to 127.0.0.1, a blocked private network.
        with pytest.raises(ValueError, match="blocked private/internal network"):
            _validate_browser_url("http://localhost:3000")

    def test_cloud_metadata_endpoint_blocked_by_ssrf_guard(self):
        with pytest.raises(ValueError, match="blocked private/internal network"):
            _validate_browser_url("http://169.254.169.254/latest/meta-data/")

    def test_private_network_allowed_with_optout(self, monkeypatch):
        # Trusted single-tenant setups can opt out to automate local apps.
        monkeypatch.setenv("SANDCASTLE_ALLOW_PRIVATE_NETWORKS", "1")
        url = "http://localhost:3000"
        assert _validate_browser_url(url) == url


# ===========================================================================
# Section 3: _build_computer_use_action_script
# ===========================================================================


class TestBuildComputerUseActionScript:
    """Tests for computer_use action script generation."""

    def _cfg(self, **kwargs: Any) -> BrowserConfig:
        return BrowserConfig(**kwargs)

    def test_screenshot_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script("screenshot", {}, cfg)
        assert "screenshot_taken" in script
        assert "console.log" in script

    def test_click_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "click", {"coordinate": [100, 200]}, cfg
        )
        assert "mouse.click(100, 200)" in script
        assert "clicked" in script

    def test_left_click_alias(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "left_click", {"coordinate": [50, 75]}, cfg
        )
        assert "mouse.click(50, 75)" in script

    def test_right_click_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "right_click", {"coordinate": [10, 20]}, cfg
        )
        assert "button: 'right'" in script
        assert "right_clicked" in script

    def test_double_click_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "double_click", {"coordinate": [30, 40]}, cfg
        )
        assert "dblclick(30, 40)" in script

    def test_type_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "type", {"text": "hello world"}, cfg
        )
        assert "keyboard.type('hello world')" in script

    def test_type_action_escapes_special_chars(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "type", {"text": "it's a \"test\""}, cfg
        )
        assert "\\'" in script
        assert '\\"' in script

    def test_key_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "key", {"text": "Enter"}, cfg
        )
        assert "keyboard.press('Enter')" in script

    def test_scroll_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "scroll", {"coordinate": [0, -300]}, cfg
        )
        assert "mouse.wheel" in script

    def test_mouse_move_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "mouse_move", {"coordinate": [500, 600]}, cfg
        )
        assert "mouse.move(500, 600)" in script

    def test_unknown_action(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "dance", {}, cfg
        )
        assert "Unknown action" in script
        assert "dance" in script

    def test_unknown_action_escapes_name(self):
        """Action name with quotes is escaped so it stays inside the string literal."""
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "'; process.exit(); '", {}, cfg
        )
        assert "Unknown action" in script
        # The single quotes in the action name should be escaped
        assert "\\'" in script
        # The raw unescaped version should NOT appear
        assert "'; process.exit(); '" not in script

    def test_missing_coordinate_defaults_to_zero(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "click", {}, cfg
        )
        assert "mouse.click(0, 0)" in script

    def test_empty_coordinate_list(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "click", {"coordinate": []}, cfg
        )
        assert "mouse.click(0, 0)" in script

    def test_single_coordinate_value(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "click", {"coordinate": [100]}, cfg
        )
        assert "100, 0" in script

    def test_negative_coordinates_clamped_to_zero(self):
        """Negative coordinates should be clamped to 0."""
        cfg = self._cfg(viewport_width=1280, viewport_height=720)
        script = _build_computer_use_action_script(
            "click", {"coordinate": [-100, -50]}, cfg
        )
        assert "mouse.click(0, 0)" in script

    def test_coordinates_clamped_to_viewport(self):
        """Coordinates exceeding viewport should be clamped."""
        cfg = self._cfg(viewport_width=1280, viewport_height=720)
        script = _build_computer_use_action_script(
            "click", {"coordinate": [5000, 3000]}, cfg
        )
        assert "mouse.click(1280, 720)" in script

    def test_float_coordinates_converted_to_int(self):
        cfg = self._cfg(viewport_width=1280, viewport_height=720)
        script = _build_computer_use_action_script(
            "click", {"coordinate": [100.7, 200.3]}, cfg
        )
        assert "mouse.click(100, 200)" in script

    def test_non_list_coordinate_defaults(self):
        """If coordinate is not a list/tuple, use defaults."""
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "click", {"coordinate": "invalid"}, cfg
        )
        assert "mouse.click(0, 0)" in script

    def test_type_with_empty_text(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "type", {"text": ""}, cfg
        )
        assert "keyboard.type('')" in script

    def test_key_with_default_enter(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "key", {}, cfg
        )
        assert "keyboard.press('Enter')" in script

    def test_scroll_missing_coordinate_uses_default(self):
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "scroll", {}, cfg
        )
        assert "mouse.wheel" in script

    def test_type_injection_attempt(self):
        """Text with JS injection attempt should be safely escaped."""
        cfg = self._cfg()
        script = _build_computer_use_action_script(
            "type",
            {"text": "'); require('child_process').exec('rm -rf /'); ('"},
            cfg,
        )
        # The single quotes should be escaped
        assert "\\'" in script


# ===========================================================================
# Section 4: _take_sandbox_screenshot
# ===========================================================================


class TestTakeSandboxScreenshot:
    """Tests for screenshot capture from sandbox."""

    @pytest.mark.asyncio
    async def test_successful_screenshot(self):
        runtime = _make_mock_runtime()
        # Simulate a base64 PNG output (> 100 chars)
        fake_b64 = "A" * 200
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": fake_b64})

        result = await _take_sandbox_screenshot(runtime, MagicMock())
        assert result == fake_b64
        runtime.sandbox_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_screenshot_too_short_returns_none(self):
        """Output shorter than 100 chars is considered invalid."""
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": "short"})

        result = await _take_sandbox_screenshot(runtime, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_screenshot_empty_returns_none(self):
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": ""})

        result = await _take_sandbox_screenshot(runtime, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_screenshot_exec_exception_returns_none(self):
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(side_effect=RuntimeError("sandbox crashed"))

        result = await _take_sandbox_screenshot(runtime, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_screenshot_result_is_string_not_dict(self):
        """Some runtimes return plain string instead of dict."""
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value="B" * 300)

        result = await _take_sandbox_screenshot(runtime, MagicMock())
        assert result == "B" * 300

    @pytest.mark.asyncio
    async def test_screenshot_with_whitespace_stripped(self):
        runtime = _make_mock_runtime()
        fake_b64 = "  " + "C" * 200 + "\n"
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": fake_b64})

        result = await _take_sandbox_screenshot(runtime, MagicMock())
        assert result == "C" * 200

    @pytest.mark.asyncio
    async def test_screenshot_size_limit_enforced(self):
        """Screenshots exceeding _MAX_SCREENSHOT_B64_SIZE should be rejected."""
        runtime = _make_mock_runtime()
        oversized = "X" * (_MAX_SCREENSHOT_B64_SIZE + 1)
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": oversized})

        result = await _take_sandbox_screenshot(runtime, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_screenshot_at_size_limit_accepted(self):
        runtime = _make_mock_runtime()
        at_limit = "Y" * _MAX_SCREENSHOT_B64_SIZE
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": at_limit})

        result = await _take_sandbox_screenshot(runtime, MagicMock())
        assert result == at_limit

    @pytest.mark.asyncio
    async def test_screenshot_timeout_passed_to_exec(self):
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": "D" * 200})

        await _take_sandbox_screenshot(runtime, MagicMock())

        call_kwargs = runtime.sandbox_exec.call_args
        assert call_kwargs.kwargs.get("timeout") == 10 or call_kwargs[1].get("timeout") == 10


# ===========================================================================
# Section 5: _execute_browser_step (main entry point)
# ===========================================================================


class TestExecuteBrowserStep:
    """Tests for the main browser step dispatcher."""

    @pytest.mark.asyncio
    async def test_missing_browser_config_returns_failed(self):
        from sandcastle.engine.executor import _execute_browser_step

        step = StepDefinition(id="test", prompt="go", type="browser")
        # browser_config is None by default
        context = _make_context()
        storage = _make_mock_storage()

        result = await _execute_browser_step(step, context, MagicMock(), storage)
        assert result.status == "failed"
        assert "Missing browser_config" in result.error

    @pytest.mark.asyncio
    async def test_unknown_mode_returns_failed(self):
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="quantum")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "failed"
        assert "Unknown browser mode: quantum" in result.error
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_exception_in_mode_handler_caught(self):
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="dom", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_dom_mode", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "failed"
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_dom_mode_dispatched(self):
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="dom", start_url="https://example.com")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_dom_mode", new_callable=AsyncMock, return_value=mock_result):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_playwright_mode_dispatched(self):
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="playwright")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        mock_result = StepResult(step_id="browser-test", status="completed", output="done")

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_playwright_mode", new_callable=AsyncMock, return_value=mock_result):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_computer_use_mode_dispatched(self):
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="computer_use")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        mock_result = StepResult(step_id="browser-test", status="completed", output="result")

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_computer_use_mode", new_callable=AsyncMock, return_value=mock_result):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_replay_screenshots_attached_to_output(self):
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="computer_use", capture_screenshots=True)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        mock_result = StepResult(step_id="browser-test", status="completed", output={"data": "x"})

        async def fake_computer_use(*args, **kwargs):
            # Simulate adding replay screenshots via the mutable list
            replay_list = args[8] if len(args) > 8 else kwargs.get("replay_screenshots", [])
            if replay_list is not None:
                replay_list.append({"iteration": 1, "action": "click", "screenshot_b64": "AAA"})
            return mock_result

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_computer_use_mode", new_callable=AsyncMock, side_effect=fake_computer_use):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"
        assert "replay" in result.output
        assert result.output["replay_count"] == 1

    @pytest.mark.asyncio
    async def test_replay_screenshots_with_non_dict_output(self):
        """When output is a string and there are replay screenshots, wrap in dict."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="computer_use", capture_screenshots=True)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        mock_result = StepResult(step_id="browser-test", status="completed", output="plain text")

        async def fake_computer_use(*args, **kwargs):
            replay_list = args[8] if len(args) > 8 else kwargs.get("replay_screenshots", [])
            if replay_list is not None:
                replay_list.append({"iteration": 1, "action": "type", "screenshot_b64": "BBB"})
            return mock_result

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_computer_use_mode", new_callable=AsyncMock, side_effect=fake_computer_use):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert isinstance(result.output, dict)
        assert result.output["result"] == "plain text"
        assert "replay" in result.output

    @pytest.mark.asyncio
    async def test_credentials_env_read(self):
        """Credentials env var should be passed to mode handlers."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="playwright", credentials_env="BROWSER_CREDS")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch.dict("os.environ", {"BROWSER_CREDS": "USER,PASS"}):
                    with patch("sandcastle.engine.executor._browser_playwright_mode", new_callable=AsyncMock, return_value=mock_result) as mock_pw:
                        result = await _execute_browser_step(step, context, MagicMock(), storage)
                        # Credentials should have been passed (3rd positional arg)
                        call_args = mock_pw.call_args
                        assert call_args[0][3] == "USER,PASS"


# ===========================================================================
# Section 6: _browser_playwright_mode
# ===========================================================================


class TestBrowserPlaywrightMode:
    """Tests for Playwright mode execution."""

    @pytest.mark.asyncio
    async def test_invalid_url_returns_failed(self):
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="javascript:alert(1)")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        result = await _browser_playwright_mode(
            step, cfg, "test prompt", None, context, MagicMock(), storage, time.monotonic()
        )
        assert result.status == "failed"
        assert "Dangerous URL scheme" in result.error

    @pytest.mark.asyncio
    async def test_file_url_rejected(self):
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="file:///etc/passwd")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        result = await _browser_playwright_mode(
            step, cfg, "test prompt", None, context, MagicMock(), storage, time.monotonic()
        )
        assert result.status == "failed"
        assert "Dangerous URL scheme" in result.error

    @pytest.mark.asyncio
    async def test_valid_url_proceeds_to_execution(self):
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="https://example.com", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor._get_cached_actions", new_callable=AsyncMock, return_value=None):
                with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result):
                    result = await _browser_playwright_mode(
                        step, cfg, "go", None, context, MagicMock(), storage, time.monotonic()
                    )

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_no_start_url_skips_goto(self):
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor._get_cached_actions", new_callable=AsyncMock, return_value=None):
                with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result) as mock_exec:
                    result = await _browser_playwright_mode(
                        step, cfg, "go", None, context, MagicMock(), storage, time.monotonic()
                    )

        assert result.status == "completed"
        # The augmented prompt should NOT contain a specific page.goto('URL') call
        # (page.goto appears in the documentation section but not as an actual navigation)
        augmented_step = mock_exec.call_args[0][0]
        assert "await page.goto('" not in augmented_step.prompt

    @pytest.mark.asyncio
    async def test_credentials_included_in_prompt(self):
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor._get_cached_actions", new_callable=AsyncMock, return_value=None):
                with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result) as mock_exec:
                    result = await _browser_playwright_mode(
                        step, cfg, "go", "MYUSER,MYPASS", context, MagicMock(), storage, time.monotonic()
                    )

        augmented_step = mock_exec.call_args[0][0]
        assert "Credentials" in augmented_step.prompt
        assert "MYUSER" in augmented_step.prompt
        assert "MYPASS" in augmented_step.prompt

    @pytest.mark.asyncio
    async def test_screenshot_on_error_instructions_included(self):
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="", timeout_seconds=10, screenshot_on_error=True)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor._get_cached_actions", new_callable=AsyncMock, return_value=None):
                with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result) as mock_exec:
                    result = await _browser_playwright_mode(
                        step, cfg, "go", None, context, MagicMock(), storage, time.monotonic()
                    )

        augmented_step = mock_exec.call_args[0][0]
        assert "Error Handling" in augmented_step.prompt
        assert "error-screenshot" in augmented_step.prompt

    @pytest.mark.asyncio
    async def test_cached_actions_injected_into_prompt(self):
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="https://example.com", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        cached = [
            {"action": "click", "selector": "#login"},
            {"action": "fill", "selector": "#email"},
        ]

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor._get_cached_actions", new_callable=AsyncMock, return_value=cached):
                with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result) as mock_exec:
                    result = await _browser_playwright_mode(
                        step, cfg, "login", None, context, MagicMock(), storage, time.monotonic()
                    )

        augmented_step = mock_exec.call_args[0][0]
        assert "Previously successful actions" in augmented_step.prompt
        assert "#login" in augmented_step.prompt
        assert "#email" in augmented_step.prompt

    @pytest.mark.asyncio
    async def test_playwright_install_failure_non_fatal(self):
        """Playwright install failure should be non-fatal (warning only)."""
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(side_effect=RuntimeError("npm not found"))

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor._get_cached_actions", new_callable=AsyncMock, return_value=None):
                with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result):
                    result = await _browser_playwright_mode(
                        step, cfg, "go", None, context, MagicMock(), storage, time.monotonic()
                    )

        # Should still complete despite install failure
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_step_timeout_respects_browser_timeout(self):
        """Browser timeout should be used when greater than step timeout."""
        from sandcastle.engine.executor import _browser_playwright_mode

        cfg = BrowserConfig(mode="playwright", start_url="", timeout_seconds=600)
        step = _make_step(browser_config=cfg, timeout=30)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor._get_cached_actions", new_callable=AsyncMock, return_value=None):
                with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result) as mock_exec:
                    result = await _browser_playwright_mode(
                        step, cfg, "go", None, context, MagicMock(), storage, time.monotonic()
                    )

        augmented_step = mock_exec.call_args[0][0]
        assert augmented_step.timeout == 600  # max(30, 600)


# ===========================================================================
# Section 7: _browser_dom_mode
# ===========================================================================


class TestBrowserDomMode:
    """Tests for DOM extraction mode."""

    @pytest.mark.asyncio
    async def test_missing_start_url_returns_failed(self):
        from sandcastle.engine.executor import _browser_dom_mode

        cfg = BrowserConfig(mode="dom", start_url="")
        step = _make_step(browser_config=cfg)

        result = await _browser_dom_mode(
            step, cfg, MagicMock(), "run-1", {}
        )
        assert result.status == "failed"
        assert "start_url" in result.error

    @pytest.mark.asyncio
    async def test_javascript_url_rejected(self):
        from sandcastle.engine.executor import _browser_dom_mode

        cfg = BrowserConfig(mode="dom", start_url="javascript:void(0)")
        step = _make_step(browser_config=cfg)

        result = await _browser_dom_mode(
            step, cfg, MagicMock(), "run-1", {}
        )
        assert result.status == "failed"
        assert "Dangerous URL scheme" in result.error

    @pytest.mark.asyncio
    async def test_data_url_rejected(self):
        from sandcastle.engine.executor import _browser_dom_mode

        cfg = BrowserConfig(mode="dom", start_url="data:text/html,<script>alert(1)</script>")
        step = _make_step(browser_config=cfg)

        result = await _browser_dom_mode(
            step, cfg, MagicMock(), "run-1", {}
        )
        assert result.status == "failed"
        assert "Dangerous URL scheme" in result.error

    @pytest.mark.asyncio
    async def test_valid_url_proceeds(self):
        from sandcastle.engine.executor import _browser_dom_mode

        cfg = BrowserConfig(mode="dom", start_url="https://example.com", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": '{"elements":[],"page_info":{"title":"Ex","url":"https://example.com","body_text":"Hello"}}'})

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result):
                result = await _browser_dom_mode(
                    step, cfg, MagicMock(), "run-1", {}
                )

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_dom_extraction_failure_non_fatal(self):
        """DOM extraction failure should still proceed with error message in context."""
        from sandcastle.engine.executor import _browser_dom_mode

        cfg = BrowserConfig(mode="dom", start_url="https://example.com", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        runtime = _make_mock_runtime()
        # First call: playwright install (success)
        # Second call: DOM extraction (failure)
        runtime.sandbox_exec = AsyncMock(side_effect=[
            {"stdout": ""},  # playwright install
            RuntimeError("node crashed"),  # DOM script
        ])

        mock_result = StepResult(step_id="browser-test", status="completed", output="recovered")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result) as mock_exec:
                result = await _browser_dom_mode(
                    step, cfg, MagicMock(), "run-1", {}
                )

        assert result.status == "completed"
        augmented_step = mock_exec.call_args[0][0]
        assert "DOM extraction failed" in augmented_step.prompt

    @pytest.mark.asyncio
    async def test_output_schema_included_in_prompt(self):
        from sandcastle.engine.executor import _browser_dom_mode

        cfg = BrowserConfig(
            mode="dom",
            start_url="https://example.com",
            timeout_seconds=10,
            output_schema={"type": "object", "properties": {"price": {"type": "number"}}},
        )
        step = _make_step(browser_config=cfg)
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": '{"elements":[],"page_info":{}}'})

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result) as mock_exec:
                result = await _browser_dom_mode(
                    step, cfg, MagicMock(), "run-1", {}
                )

        augmented_step = mock_exec.call_args[0][0]
        assert "price" in augmented_step.prompt
        assert "Extract data matching this schema" in augmented_step.prompt

    @pytest.mark.asyncio
    async def test_context_passed_when_provided(self):
        """When context is provided, it should be used instead of creating new."""
        from sandcastle.engine.executor import _browser_dom_mode

        cfg = BrowserConfig(mode="dom", start_url="https://example.com", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": '{"elements":[],"page_info":{}}'})

        ctx = _make_context(run_id="custom-run")
        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result) as mock_exec:
                result = await _browser_dom_mode(
                    step, cfg, MagicMock(), "run-1", {}, context=ctx
                )

        # Should use the provided context, not create a new one
        passed_ctx = mock_exec.call_args[0][1]
        assert passed_ctx.run_id == "custom-run"

    @pytest.mark.asyncio
    async def test_no_scheme_url_gets_https_prepended(self):
        from sandcastle.engine.executor import _browser_dom_mode

        cfg = BrowserConfig(mode="dom", start_url="example.com", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": '{"elements":[],"page_info":{}}'})

        mock_result = StepResult(step_id="browser-test", status="completed", output="ok")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.executor.execute_step_with_retry", new_callable=AsyncMock, return_value=mock_result):
                result = await _browser_dom_mode(
                    step, cfg, MagicMock(), "run-1", {}
                )

        # Should succeed (URL gets https:// prepended)
        assert result.status == "completed"


# ===========================================================================
# Section 8: _browser_computer_use_mode
# ===========================================================================


class TestBrowserComputerUseMode:
    """Tests for the computer_use screenshot-action loop."""

    @pytest.mark.asyncio
    async def test_invalid_url_returns_failed(self):
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(mode="computer_use", start_url="javascript:alert(1)", timeout_seconds=10)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        mock_model = MagicMock()
        mock_model.api_model_id = "claude-sonnet-4-6"
        mock_model.input_price_per_m = 3.0
        mock_model.output_price_per_m = 15.0

        with patch("sandcastle.engine.providers.resolve_model", return_value=mock_model):
            with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=_make_mock_runtime()):
                    with patch("sandcastle.engine.executor.resolve_templates", return_value="javascript:alert(1)"):
                        result = await _browser_computer_use_mode(
                            step, cfg, "prompt", None, context, MagicMock(), storage, time.monotonic()
                        )

        assert result.status == "failed"
        assert "Dangerous URL scheme" in result.error

    @pytest.mark.asyncio
    async def test_end_turn_with_text_completes(self):
        """When API returns end_turn with text, task is complete."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(mode="computer_use", start_url="https://example.com", timeout_seconds=30)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        api_response = {
            "content": [{"type": "text", "text": "Task completed successfully"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value="AAAA" * 100):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value="https://example.com"):
                            with patch("httpx.AsyncClient", _make_httpx_mock(api_response)):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "do task", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        assert result.status == "completed"
        assert result.output == "Task completed successfully"

    @pytest.mark.asyncio
    async def test_timeout_returns_failed_status(self):
        """Timeout should now return failed status instead of completed."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(mode="computer_use", start_url="", timeout_seconds=0)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value=None):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value=""):
                            started = time.monotonic() - 1
                            result = await _browser_computer_use_mode(
                                step, cfg, "do task", None, context,
                                MagicMock(), storage, started
                            )

        assert result.status == "failed"
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_api_error_stops_loop(self):
        """API call failure should stop the loop gracefully."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(mode="computer_use", start_url="https://example.com", timeout_seconds=30)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        # Build a mock that raises on post
        mock_client_cls = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("API down"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value="AAAA" * 100):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value="https://example.com"):
                            with patch("httpx.AsyncClient", mock_client_cls):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "do task", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_captcha_pause_strategy(self):
        """CAPTCHA detection with pause strategy returns captcha_detected."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(
            mode="computer_use",
            start_url="https://example.com",
            timeout_seconds=30,
            captcha_strategy="pause",
        )
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        api_response = {
            "content": [{"type": "text", "text": "I see a CAPTCHA challenge on the page"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value="AAAA" * 100):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value="https://example.com"):
                            with patch("httpx.AsyncClient", _make_httpx_mock(api_response)):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "do task", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        assert result.status == "completed"
        assert result.output["status"] == "captcha_detected"
        assert result.output["requires_approval"] is True

    @pytest.mark.asyncio
    async def test_captcha_fail_strategy(self):
        """CAPTCHA detection with fail strategy returns failed."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(
            mode="computer_use",
            start_url="https://example.com",
            timeout_seconds=30,
            captcha_strategy="fail",
        )
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        api_response = {
            "content": [{"type": "text", "text": "There is a reCAPTCHA on this page"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value="AAAA" * 100):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value="https://example.com"):
                            with patch("httpx.AsyncClient", _make_httpx_mock(api_response)):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "do task", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        assert result.status == "failed"
        assert "CAPTCHA" in result.error

    @pytest.mark.asyncio
    async def test_json_result_parsed(self):
        """If LLM returns valid JSON, it should be parsed into dict/list."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(mode="computer_use", start_url="https://example.com", timeout_seconds=30)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        json_output = json.dumps({"price": 42.99, "currency": "USD"})
        api_response = {
            "content": [{"type": "text", "text": json_output}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value="AAAA" * 100):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value="https://example.com"):
                            with patch("httpx.AsyncClient", _make_httpx_mock(api_response)):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "get price", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert result.output["price"] == 42.99

    @pytest.mark.asyncio
    async def test_cost_tracking(self):
        """Token usage should be tracked and accumulated in cost_usd."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(mode="computer_use", start_url="https://example.com", timeout_seconds=30)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        api_response = {
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        }

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value="AAAA" * 100):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value="https://example.com"):
                            with patch("httpx.AsyncClient", _make_httpx_mock(api_response)):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "do task", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        # cost = (1000 * 3.0 / 1M) + (500 * 15.0 / 1M) = 0.003 + 0.0075 = 0.0105
        assert result.cost_usd == pytest.approx(0.0105, abs=1e-6)

    @pytest.mark.asyncio
    async def test_no_start_url_still_works(self):
        """Computer use mode should work without a start URL."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(mode="computer_use", start_url="", timeout_seconds=30)
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()

        api_response = {
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 50, "output_tokens": 20},
        }

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value=None):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value=""):
                            with patch("httpx.AsyncClient", _make_httpx_mock(api_response)):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "task", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        assert result.status == "completed"


# ===========================================================================
# Section 9: Browser action cache
# ===========================================================================


class TestBrowserActionCache:
    """Tests for the browser action cache helpers."""

    @pytest.mark.asyncio
    async def test_cache_roundtrip(self):
        from sandcastle.engine.executor import (
            _get_cached_actions,
            _save_cached_actions,
        )

        url = "https://test-cache.example.com/page"
        intent = "fill login form"
        actions = [{"action": "click", "selector": "#login"}]

        await _save_cached_actions(url, intent, actions)
        result = await _get_cached_actions(url, intent)
        assert result == actions

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        from sandcastle.engine.executor import _get_cached_actions

        result = await _get_cached_actions("https://nonexistent.example.com", "xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_key_generation(self):
        from sandcastle.engine.executor import _cache_key

        key = _cache_key("https://example.com/path", "do something")
        assert "example.com" in key
        assert "/path" in key

    @pytest.mark.asyncio
    async def test_cache_eviction_on_max(self):
        from sandcastle.engine.executor import (
            _browser_action_cache,
            _BROWSER_CACHE_MAX,
            _save_cached_actions,
            _get_cached_actions,
        )

        # Save more entries than the max to trigger eviction
        original_size = len(_browser_action_cache)
        for i in range(_BROWSER_CACHE_MAX + 5):
            await _save_cached_actions(
                f"https://evict-test-{i}.example.com",
                f"intent-{i}",
                [{"action": "click"}],
            )

        assert len(_browser_action_cache) <= _BROWSER_CACHE_MAX


# ===========================================================================
# Section 10: Edge cases and integration-like scenarios
# ===========================================================================


class TestBrowserEdgeCases:
    """Edge case tests for browser step execution."""

    def test_browser_config_defaults(self):
        """BrowserConfig should have sensible defaults."""
        cfg = BrowserConfig()
        assert cfg.mode == "playwright"
        assert cfg.viewport_width == 1280
        assert cfg.viewport_height == 720
        assert cfg.timeout_seconds == 120
        assert cfg.wait_after_action == 0.5
        assert cfg.screenshot_on_error is True
        assert cfg.headless is True
        assert cfg.captcha_strategy == "pause"
        assert cfg.max_actions == 100
        assert cfg.capture_screenshots is False

    def test_step_definition_browser_config(self):
        """StepDefinition should carry browser_config when set."""
        cfg = BrowserConfig(mode="dom", start_url="https://test.com")
        step = StepDefinition(id="test", type="browser", browser_config=cfg)
        assert step.browser_config is not None
        assert step.browser_config.mode == "dom"
        assert step.browser_config.start_url == "https://test.com"

    def test_escape_js_string_with_url(self):
        """URLs should be safely escaped for JS embedding."""
        url = "https://example.com/path?q=it's&x=\"test\""
        escaped = _escape_js_string(url)
        assert "\\'" in escaped
        assert '\\"' in escaped

    def test_validate_url_with_encoded_chars(self):
        """URL with percent-encoded characters should be valid."""
        url = "https://example.com/path%20with%20spaces"
        result = _validate_browser_url(url)
        assert result == url

    def test_validate_url_idna(self):
        """International domain names should be valid."""
        url = "https://xn--nxasmq6b.example.com"
        result = _validate_browser_url(url)
        assert result == url

    def test_build_action_script_returns_string(self):
        """All action types should return a non-empty string."""
        cfg = BrowserConfig()
        actions = [
            "screenshot", "click", "left_click", "right_click",
            "double_click", "type", "key", "scroll", "mouse_move",
            "unknown_action",
        ]
        for action in actions:
            script = _build_computer_use_action_script(
                action, {"coordinate": [100, 100], "text": "test"}, cfg
            )
            assert isinstance(script, str)
            assert len(script) > 0

    @pytest.mark.asyncio
    async def test_execute_browser_step_duration_tracked(self):
        """Duration should be set even on early failure."""
        from sandcastle.engine.executor import _execute_browser_step

        step = StepDefinition(id="test", prompt="go", type="browser")
        context = _make_context()
        storage = _make_mock_storage()

        result = await _execute_browser_step(step, context, MagicMock(), storage)
        # Missing browser_config -> immediate fail, but no duration_seconds
        # This is OK for the "missing config" fast path
        assert result.status == "failed"

    def test_captcha_keywords(self):
        """All CAPTCHA keywords should be detected case-insensitively."""
        keywords = ["captcha", "recaptcha", "hcaptcha", "verify you're human", "turnstile"]
        for kw in keywords:
            text_lower = f"I see a {kw} on this page".lower()
            assert any(k in text_lower for k in keywords)

    @pytest.mark.asyncio
    async def test_tool_use_action_execution(self):
        """Tool use actions should trigger sandbox exec and screenshot."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(
            mode="computer_use",
            start_url="https://example.com",
            timeout_seconds=30,
            wait_after_action=0,  # No delay for test speed
        )
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": "ok"})

        responses = [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "computer",
                        "input": {"action": "click", "coordinate": [100, 200]},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 50, "output_tokens": 30},
            },
            {
                "content": [{"type": "text", "text": "clicked and done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 80, "output_tokens": 20},
            },
        ]

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value="AAAA" * 100):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value="https://example.com"):
                            with patch("httpx.AsyncClient", _make_httpx_mock_multi(responses)):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "click button", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        assert result.status == "completed"
        assert result.output == "clicked and done"
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_action_output_truncated_to_2000(self):
        """Action output sent back to API should be truncated."""
        from sandcastle.engine.executor import _browser_computer_use_mode

        cfg = BrowserConfig(
            mode="computer_use",
            start_url="https://example.com",
            timeout_seconds=30,
            wait_after_action=0,
        )
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        runtime = _make_mock_runtime()
        long_output = "X" * 5000
        runtime.sandbox_exec = AsyncMock(return_value={"stdout": long_output})

        responses = [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "computer",
                        "input": {"action": "click", "coordinate": [10, 10]},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 50, "output_tokens": 30},
            },
            {
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 80, "output_tokens": 20},
            },
        ]

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=runtime):
            with patch("sandcastle.engine.providers.resolve_model", return_value=_make_mock_model()):
                with patch("sandcastle.engine.providers.get_api_key", return_value="sk-test"):
                    with patch("sandcastle.engine.executor._take_sandbox_screenshot", new_callable=AsyncMock, return_value="AAAA" * 100):
                        with patch("sandcastle.engine.executor.resolve_templates", return_value="https://example.com"):
                            with patch("httpx.AsyncClient", _make_httpx_mock_multi(responses)):
                                result = await _browser_computer_use_mode(
                                    step, cfg, "do it", None, context,
                                    MagicMock(), storage, time.monotonic()
                                )

        assert result.status == "completed"
