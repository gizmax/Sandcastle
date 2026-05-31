"""Tests for browser automation step and sandbox backend abstraction (v0.27).

Covers:
- BrowserConfig parsing from YAML data
- Browser mode selection (lightpanda, browserbase, playwright default)
- URL validation (blocks file://, data://, javascript:, vbscript:, blob:)
- Screenshot, viewport, timeout, max_actions configuration
- Credential environment injection
- Empty start_url handling
- Browser step result formatting and replay data
- Computer use mode dispatch with vision model
- E2B backend request format and health checks
- Docker backend container config (seccomp, caps, limits)
- Local backend subprocess execution and environment filtering
- Cloudflare backend worker URL construction and HTTPS enforcement
- Backend factory selection and invalid backend rejection
- Sandbox timeout, cleanup, file validation, resource limits
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.backends import (
    CloudflareBackend,
    DockerBackend,
    E2BBackend,
    LocalBackend,
    SSEEvent,
    _validate_runner_file,
    _validate_tool_filename,
    create_backend,
)
from sandcastle.engine.dag import BrowserConfig, StepDefinition, _parse_browser_config
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _validate_browser_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(
    step_id: str = "browser-v27",
    prompt: str = "Navigate and extract data",
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
    run_id: str = "run-v27-001",
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


# ===========================================================================
# 1. BrowserConfig - parsing and validation (15+ tests)
# ===========================================================================


class TestBrowserConfigParsing:
    """Tests for BrowserConfig parsing from YAML dict data."""

    def test_parse_full_config(self):
        """All fields are correctly parsed from a complete YAML dict."""
        data = {
            "mode": "computer_use",
            "start_url": "https://example.com",
            "viewport_width": 1920,
            "viewport_height": 1080,
            "timeout_seconds": 60,
            "wait_after_action": 1.0,
            "screenshot_on_error": False,
            "headless": False,
            "credentials_env": "BROWSER_CREDS",
            "max_actions": 50,
            "capture_screenshots": True,
            "output_schema": {"type": "object"},
            "captcha_strategy": "fail",
        }
        cfg = _parse_browser_config(data)
        assert cfg is not None
        assert cfg.mode == "computer_use"
        assert cfg.start_url == "https://example.com"
        assert cfg.viewport_width == 1920
        assert cfg.viewport_height == 1080
        assert cfg.timeout_seconds == 60
        assert cfg.wait_after_action == 1.0
        assert cfg.screenshot_on_error is False
        assert cfg.headless is False
        assert cfg.credentials_env == "BROWSER_CREDS"
        assert cfg.max_actions == 50
        assert cfg.capture_screenshots is True
        assert cfg.output_schema == {"type": "object"}
        assert cfg.captcha_strategy == "fail"

    def test_parse_none_returns_none(self):
        """Passing None returns None (no browser config)."""
        assert _parse_browser_config(None) is None

    def test_parse_empty_dict_uses_defaults(self):
        """An empty dict yields all default values."""
        cfg = _parse_browser_config({})
        assert cfg is not None
        assert cfg.mode == "playwright"
        assert cfg.start_url == ""
        assert cfg.viewport_width == 1280
        assert cfg.viewport_height == 720
        assert cfg.timeout_seconds == 120
        assert cfg.wait_after_action == 0.5
        assert cfg.screenshot_on_error is True
        assert cfg.headless is True
        assert cfg.credentials_env == ""
        assert cfg.max_actions == 100
        assert cfg.capture_screenshots is False
        assert cfg.output_schema is None
        assert cfg.captcha_strategy == "pause"

    def test_lightpanda_mode_selection(self):
        """LightPanda mode is correctly parsed."""
        cfg = _parse_browser_config({"mode": "lightpanda"})
        assert cfg.mode == "lightpanda"

    def test_browserbase_mode_selection(self):
        """Browserbase mode is correctly parsed."""
        cfg = _parse_browser_config({"mode": "browserbase"})
        assert cfg.mode == "browserbase"

    def test_default_playwright_mode(self):
        """Default mode is 'playwright' when no mode is specified."""
        cfg = _parse_browser_config({})
        assert cfg.mode == "playwright"

    def test_dom_mode_selection(self):
        """DOM mode is correctly parsed."""
        cfg = _parse_browser_config({"mode": "dom"})
        assert cfg.mode == "dom"

    def test_computer_use_mode_selection(self):
        """Computer use mode is correctly parsed."""
        cfg = _parse_browser_config({"mode": "computer_use"})
        assert cfg.mode == "computer_use"

    def test_screenshot_option_enabled(self):
        """capture_screenshots=True is parsed correctly."""
        cfg = _parse_browser_config({"capture_screenshots": True})
        assert cfg.capture_screenshots is True

    def test_screenshot_on_error_disabled(self):
        """screenshot_on_error can be disabled."""
        cfg = _parse_browser_config({"screenshot_on_error": False})
        assert cfg.screenshot_on_error is False

    def test_viewport_configuration_custom(self):
        """Custom viewport dimensions are parsed."""
        cfg = _parse_browser_config({
            "viewport_width": 375,
            "viewport_height": 812,
        })
        assert cfg.viewport_width == 375
        assert cfg.viewport_height == 812

    def test_timeout_enforcement_value(self):
        """Custom timeout_seconds is parsed."""
        cfg = _parse_browser_config({"timeout_seconds": 30})
        assert cfg.timeout_seconds == 30

    def test_max_actions_limit(self):
        """Custom max_actions limit is parsed."""
        cfg = _parse_browser_config({"max_actions": 10})
        assert cfg.max_actions == 10

    def test_credentials_env_injection(self):
        """credentials_env is parsed and stored for later environment lookup."""
        cfg = _parse_browser_config({"credentials_env": "MY_SECRET_CREDS"})
        assert cfg.credentials_env == "MY_SECRET_CREDS"

    def test_output_schema_parsing(self):
        """output_schema dict is preserved through parsing."""
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        cfg = _parse_browser_config({"output_schema": schema})
        assert cfg.output_schema == schema

    def test_captcha_strategy_skip(self):
        """captcha_strategy 'skip' is parsed."""
        cfg = _parse_browser_config({"captcha_strategy": "skip"})
        assert cfg.captcha_strategy == "skip"


# ===========================================================================
# 2. URL validation
# ===========================================================================


class TestBrowserURLValidation:
    """Tests for _validate_browser_url blocking dangerous schemes."""

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("file:///etc/passwd")

    def test_rejects_data_scheme(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("data:text/html,<h1>XSS</h1>")

    def test_rejects_javascript_scheme(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("javascript:alert(1)")

    def test_rejects_vbscript_scheme(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("vbscript:MsgBox(1)")

    def test_rejects_blob_scheme(self):
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("blob:http://example.com/data")

    def test_allows_https(self):
        result = _validate_browser_url("https://example.com")
        assert result == "https://example.com"

    def test_allows_http(self):
        result = _validate_browser_url("http://example.com")
        assert result == "http://example.com"

    def test_prepends_https_when_no_scheme(self):
        result = _validate_browser_url("example.com")
        assert result == "https://example.com"

    def test_rejects_empty_url(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_browser_url("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_browser_url("   ")

    def test_strips_whitespace(self):
        result = _validate_browser_url("  https://example.com  ")
        assert result == "https://example.com"

    def test_rejects_unsupported_scheme(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _validate_browser_url("ftp://example.com/file")

    def test_rejects_case_insensitive_dangerous_scheme(self):
        """Dangerous schemes are rejected regardless of case."""
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("FILE:///etc/passwd")

    def test_rejects_leading_whitespace_dangerous_scheme(self):
        """Leading whitespace before a dangerous scheme does not bypass validation."""
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("  javascript:alert(1)")


# ===========================================================================
# 3. Browser step - empty start_url and result formatting
# ===========================================================================


class TestBrowserStepEdgeCases:
    """Tests for browser step entry point edge cases."""

    @pytest.mark.asyncio
    async def test_missing_browser_config_returns_failed(self):
        from sandcastle.engine.executor import _execute_browser_step

        step = StepDefinition(id="no-cfg", prompt="go", type="browser")
        context = _make_context()
        storage = _make_mock_storage()

        result = await _execute_browser_step(step, context, MagicMock(), storage)
        assert result.status == "failed"
        assert "Missing browser_config" in result.error

    @pytest.mark.asyncio
    async def test_unknown_mode_returns_failed(self):
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="nonexistent_mode")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "failed"
        assert "Unknown browser mode: nonexistent_mode" in result.error

    @pytest.mark.asyncio
    async def test_browser_step_with_empty_start_url(self):
        """Playwright mode with empty start_url still dispatches (no URL navigation)."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="playwright", start_url="")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        mock_result = StepResult(step_id="browser-v27", status="completed", output="ok")

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_playwright_mode", new_callable=AsyncMock, return_value=mock_result):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_replay_screenshots_attached_to_result(self):
        """When replay_screenshots exist, they are attached to the result output."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="computer_use")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        # Simulate computer_use mode returning a result and populating replay list
        mock_result = StepResult(
            step_id="browser-v27",
            status="completed",
            output={"text": "done"},
        )

        async def fake_computer_use(step, cfg, prompt, creds, ctx, sandbox, storage, started, replay=None):
            if replay is not None:
                replay.append({"action": "click", "screenshot": "base64data"})
            return mock_result

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_computer_use_mode", side_effect=fake_computer_use):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"
        assert "replay" in result.output
        assert result.output["replay_count"] == 1

    @pytest.mark.asyncio
    async def test_lightpanda_mode_dispatched(self):
        """LightPanda mode is dispatched to _browser_lightpanda_mode."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="lightpanda")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        mock_result = StepResult(step_id="browser-v27", status="completed", output="lp-ok")

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_lightpanda_mode", new_callable=AsyncMock, return_value=mock_result):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"
        assert result.output == "lp-ok"

    @pytest.mark.asyncio
    async def test_browserbase_mode_dispatched(self):
        """Browserbase mode is dispatched to _browser_browserbase_mode."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="browserbase")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        mock_result = StepResult(step_id="browser-v27", status="completed", output="bb-ok")

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_browserbase_mode", new_callable=AsyncMock, return_value=mock_result):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"
        assert result.output == "bb-ok"

    @pytest.mark.asyncio
    async def test_computer_use_mode_dispatched(self):
        """Computer use mode is dispatched to _browser_computer_use_mode."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="computer_use")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()
        mock_result = StepResult(step_id="browser-v27", status="completed", output="cu-ok")

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_computer_use_mode", new_callable=AsyncMock, return_value=mock_result):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_exception_in_handler_returns_failed_with_duration(self):
        """Exception in a mode handler is caught and returns failed with duration."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="playwright")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch(
                    "sandcastle.engine.executor._browser_playwright_mode",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("playwright crashed"),
                ):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "failed"
        assert "playwright crashed" in result.error
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_result_with_non_dict_output_wraps_for_replay(self):
        """When output is not a dict and replay data exists, output is wrapped."""
        from sandcastle.engine.executor import _execute_browser_step

        cfg = BrowserConfig(mode="computer_use")
        step = _make_step(browser_config=cfg)
        context = _make_context()
        storage = _make_mock_storage()

        mock_result = StepResult(
            step_id="browser-v27",
            status="completed",
            output="plain string result",
        )

        async def fake_computer_use(step, cfg, prompt, creds, ctx, sandbox, storage, started, replay=None):
            if replay is not None:
                replay.append({"action": "type", "text": "hello"})
            return mock_result

        with patch("sandcastle.engine.executor.resolve_templates", return_value="go"):
            with patch("sandcastle.engine.executor.resolve_storage_refs", new_callable=AsyncMock, return_value="go"):
                with patch("sandcastle.engine.executor._browser_computer_use_mode", side_effect=fake_computer_use):
                    result = await _execute_browser_step(step, context, MagicMock(), storage)

        assert result.status == "completed"
        # Non-dict output is wrapped into {"result": ..., "replay": ..., "replay_count": ...}
        assert isinstance(result.output, dict)
        assert result.output["result"] == "plain string result"
        assert result.output["replay_count"] == 1


# ===========================================================================
# 4. Sandbox backends (15+ tests)
# ===========================================================================


class TestE2BBackendV27:
    """Tests for E2B backend request format and health checks."""

    def test_name_is_e2b(self):
        backend = E2BBackend(e2b_api_key="test-key-v27")
        assert backend.name == "e2b"

    @pytest.mark.asyncio
    async def test_health_false_without_api_key(self):
        backend = E2BBackend(e2b_api_key="")
        assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_health_true_with_key_and_sdk(self):
        backend = E2BBackend(e2b_api_key="key-v27")
        with patch.dict("sys.modules", {"e2b": MagicMock()}):
            assert await backend.health() is True

    @pytest.mark.asyncio
    async def test_health_false_when_sdk_missing(self):
        backend = E2BBackend(e2b_api_key="key-v27")
        with patch.dict("sys.modules", {"e2b": None}):
            assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        """E2B close() is a no-op since sandboxes are ephemeral."""
        backend = E2BBackend(e2b_api_key="key")
        await backend.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_start_validates_runner_file(self):
        """E2B start() validates runner file path against traversal attacks."""
        backend = E2BBackend(e2b_api_key="key-v27")
        with pytest.raises(ValueError, match="Invalid runner file path"):
            async for _ in backend.start(
                runner_file="../../../etc/passwd",
                envs={},
                use_claude_runner=False,
                timeout=30,
            ):
                pass


class TestDockerBackendV27:
    """Tests for Docker backend container configuration."""

    def test_name_is_docker(self):
        backend = DockerBackend()
        assert backend.name == "docker"

    def test_default_resource_limits(self):
        """Default resource limits are set in constructor."""
        backend = DockerBackend()
        assert backend._memory_limit == 512 * 1024 * 1024  # 512 MB
        assert backend._pids_limit == 100
        assert backend._cpu_period == 100_000
        assert backend._cpu_quota == 50_000

    def test_custom_resource_limits(self):
        """Custom CPU, memory, and PID limits are stored."""
        backend = DockerBackend(
            memory_limit=1024 * 1024 * 1024,
            pids_limit=200,
            cpu_period=200_000,
            cpu_quota=100_000,
        )
        assert backend._memory_limit == 1024 * 1024 * 1024
        assert backend._pids_limit == 200
        assert backend._cpu_period == 200_000
        assert backend._cpu_quota == 100_000

    def test_custom_seccomp_profile(self):
        """Custom seccomp profile path is stored."""
        backend = DockerBackend(seccomp_profile="/etc/seccomp/custom.json")
        assert backend._seccomp_profile == "/etc/seccomp/custom.json"

    @pytest.mark.asyncio
    async def test_health_check_caching(self):
        """Health result is cached for 30 seconds."""
        backend = DockerBackend()
        backend._health_cache = (True, time.monotonic())

        # Should return cached True without actually calling Docker
        result = await backend.health()
        assert result is True
        assert backend._client is None  # No client created

    @pytest.mark.asyncio
    async def test_health_cache_expired_triggers_real_check(self):
        """Expired cache triggers a real health check via _health_uncached."""
        backend = DockerBackend()
        # Set cache to 60 seconds ago (expired)
        backend._health_cache = (True, time.monotonic() - 60.0)

        # Mock the Docker client to simulate a successful health check
        mock_docker = AsyncMock()
        mock_docker.version = AsyncMock(return_value={"Version": "24.0"})
        backend._client = mock_docker

        result = await backend.health()
        assert result is True
        mock_docker.version.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_clears_client(self):
        """Close sets the internal client to None."""
        backend = DockerBackend()
        mock_client = AsyncMock()
        backend._client = mock_client

        await backend.close()
        assert backend._client is None
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_handles_exception(self):
        """Close handles exceptions during client shutdown gracefully."""
        backend = DockerBackend()
        mock_client = AsyncMock()
        mock_client.close.side_effect = RuntimeError("socket closed")
        backend._client = mock_client

        await backend.close()  # Should not raise
        assert backend._client is None

    @pytest.mark.asyncio
    async def test_start_validates_runner_file(self):
        """Docker start() rejects path traversal in runner_file."""
        backend = DockerBackend()
        with pytest.raises(ValueError, match="Invalid runner file path"):
            async for _ in backend.start(
                runner_file="../../exploit.js",
                envs={},
                use_claude_runner=False,
                timeout=30,
            ):
                pass

    def test_non_root_user_in_config(self):
        """Docker config uses non-root user 1000:1000 for security."""
        # Verify DockerBackend's start() builds config with User "1000:1000"
        # by inspecting the source - constructor stores the image
        backend = DockerBackend(docker_image="test-runner:v27")
        assert backend._image == "test-runner:v27"
        # The actual config creation happens inside start() and uses User: "1000:1000"
        # We verify by patching Docker and inspecting the config dict

    @pytest.mark.asyncio
    async def test_auto_remove_flag_in_container_config(self):
        """Docker container config includes AutoRemove=True to prevent orphans."""
        backend = DockerBackend(docker_image="test-runner:v27")

        # Mock the aiodocker module and Docker client
        mock_container = AsyncMock()
        mock_container.start = AsyncMock()
        mock_container.log = AsyncMock(return_value=AsyncIterHelper([]))
        mock_container.delete = AsyncMock()
        mock_container.put_archive = AsyncMock()

        mock_docker = MagicMock()
        mock_docker.containers = MagicMock()
        mock_docker.containers.create = AsyncMock(return_value=mock_container)

        backend._client = mock_docker

        # Create a real runner file for the backend to read
        import tempfile
        from pathlib import Path
        from sandcastle.engine.backends import _RUNNER_DIR

        runner_name = "test_runner_v27.mjs"
        runner_path = _RUNNER_DIR / runner_name
        runner_existed = runner_path.exists()
        if not runner_existed:
            runner_path.write_text("console.log('test');")

        try:
            # Mock Path reading and seccomp
            with patch("asyncio.to_thread", new_callable=AsyncMock, return_value='{"defaultAction": "SCMP_ACT_ALLOW"}'):
                events = []
                async for event in backend.start(
                    runner_file=runner_name,
                    envs={"KEY": "val"},
                    use_claude_runner=False,
                    timeout=10,
                ):
                    events.append(event)

            # Verify create was called with correct config
            call_args = mock_docker.containers.create.call_args
            config = call_args[1]["config"] if "config" in call_args[1] else call_args[0][0] if call_args[0] else call_args[1].get("config")
            assert config["HostConfig"]["AutoRemove"] is True
            assert config["HostConfig"]["CapDrop"] == ["ALL"]
            assert config["HostConfig"]["PidsLimit"] == 100
            assert config["HostConfig"]["ReadonlyRootfs"] is True
            assert config["User"] == "1000:1000"
            assert config["HostConfig"]["Memory"] == 512 * 1024 * 1024
            assert config["HostConfig"]["MemorySwap"] == 512 * 1024 * 1024
        finally:
            if not runner_existed and runner_path.exists():
                runner_path.unlink()


class AsyncIterHelper:
    """Helper to make a list behave as an async iterator."""

    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


class TestLocalBackendV27:
    """Tests for Local backend subprocess execution."""

    def test_name_is_local(self):
        backend = LocalBackend()
        assert backend.name == "local"

    @pytest.mark.asyncio
    async def test_health_checks_node_availability(self):
        """Health returns True if node is on PATH."""
        backend = LocalBackend()
        with patch("shutil.which", return_value="/usr/local/bin/node"):
            assert await backend.health() is True

    @pytest.mark.asyncio
    async def test_health_false_when_node_missing(self):
        """Health returns False when node is not found."""
        backend = LocalBackend()
        with patch("shutil.which", return_value=None):
            assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        """Local backend close is a no-op."""
        backend = LocalBackend()
        await backend.close()

    @pytest.mark.asyncio
    async def test_start_rejects_path_traversal(self):
        """Local start() rejects path traversal in runner file."""
        backend = LocalBackend()
        with pytest.raises(ValueError, match="Invalid runner file path"):
            async for _ in backend.start(
                runner_file="../../../etc/passwd",
                envs={},
                use_claude_runner=False,
                timeout=30,
            ):
                pass

    @pytest.mark.asyncio
    async def test_start_rejects_absolute_path(self):
        """Local start() rejects absolute runner file paths."""
        backend = LocalBackend()
        with pytest.raises(ValueError, match="Invalid runner file path"):
            async for _ in backend.start(
                runner_file="/etc/passwd",
                envs={},
                use_claude_runner=False,
                timeout=30,
            ):
                pass


class TestCloudflareBackendV27:
    """Tests for Cloudflare backend worker URL construction and health."""

    def test_name_is_cloudflare(self):
        backend = CloudflareBackend(worker_url="https://sandbox.workers.dev")
        assert backend.name == "cloudflare"

    def test_worker_url_trailing_slash_stripped(self):
        """Trailing slash is removed from worker URL."""
        backend = CloudflareBackend(worker_url="https://sandbox.workers.dev/")
        assert backend._worker_url == "https://sandbox.workers.dev"

    def test_http_upgraded_to_https(self):
        """Non-localhost HTTP URLs are upgraded to HTTPS."""
        backend = CloudflareBackend(worker_url="http://sandbox.workers.dev")
        assert backend._worker_url == "https://sandbox.workers.dev"

    def test_localhost_http_not_upgraded(self):
        """Localhost HTTP URLs are NOT upgraded to HTTPS."""
        backend = CloudflareBackend(worker_url="http://localhost:8787")
        assert backend._worker_url == "http://localhost:8787"

    def test_ipv4_loopback_http_not_upgraded(self):
        """127.0.0.1 HTTP URLs are NOT upgraded to HTTPS."""
        backend = CloudflareBackend(worker_url="http://127.0.0.1:8787")
        assert backend._worker_url == "http://127.0.0.1:8787"

    def test_ipv6_loopback_http_not_upgraded(self):
        """[::1] HTTP URLs are NOT upgraded to HTTPS."""
        backend = CloudflareBackend(worker_url="http://[::1]:8787")
        assert backend._worker_url == "http://[::1]:8787"

    def test_empty_url_stored(self):
        """Empty worker URL is stored as-is."""
        backend = CloudflareBackend(worker_url="")
        assert backend._worker_url == ""

    @pytest.mark.asyncio
    async def test_health_false_when_no_url(self):
        """Health returns False when no worker URL is configured."""
        backend = CloudflareBackend(worker_url="")
        assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_health_caching(self):
        """Health result is cached for 30 seconds."""
        backend = CloudflareBackend(worker_url="https://sandbox.workers.dev")
        backend._health_cache = (True, time.monotonic())

        result = await backend.health()
        assert result is True

    @pytest.mark.asyncio
    async def test_close_clears_client(self):
        """Close sets the pooled httpx client to None."""
        backend = CloudflareBackend(worker_url="https://sandbox.workers.dev")
        mock_client = AsyncMock()
        backend._client = mock_client

        await backend.close()
        assert backend._client is None

    @pytest.mark.asyncio
    async def test_start_requires_worker_url(self):
        """Start raises RuntimeError when no worker URL is set."""
        backend = CloudflareBackend(worker_url="")
        with pytest.raises(RuntimeError, match="CLOUDFLARE_WORKER_URL is required"):
            async for _ in backend.start(
                runner_file="runner.mjs",
                envs={},
                use_claude_runner=False,
                timeout=30,
            ):
                pass

    @pytest.mark.asyncio
    async def test_start_validates_runner_file(self):
        """Cloudflare start() rejects path traversal in runner file."""
        backend = CloudflareBackend(worker_url="https://sandbox.workers.dev")
        with pytest.raises(ValueError, match="Invalid runner file path"):
            async for _ in backend.start(
                runner_file="../../../etc/passwd",
                envs={},
                use_claude_runner=False,
                timeout=30,
            ):
                pass


# ===========================================================================
# 5. Backend factory and file validation
# ===========================================================================


class TestBackendFactoryV27:
    """Tests for create_backend() factory."""

    def test_create_e2b(self):
        backend = create_backend("e2b", e2b_api_key="key-v27")
        assert isinstance(backend, E2BBackend)

    def test_create_docker(self):
        backend = create_backend("docker")
        assert isinstance(backend, DockerBackend)

    def test_create_local(self):
        backend = create_backend("local")
        assert isinstance(backend, LocalBackend)

    def test_create_cloudflare(self):
        backend = create_backend("cloudflare", cloudflare_worker_url="https://x.workers.dev")
        assert isinstance(backend, CloudflareBackend)

    def test_invalid_backend_raises_valueerror(self):
        """Unknown backend type raises ValueError with helpful message."""
        with pytest.raises(ValueError, match="Unknown sandbox backend 'bogus'"):
            create_backend("bogus")

    def test_docker_custom_limits_passed_through(self):
        """Factory passes custom Docker resource limits to DockerBackend."""
        backend = create_backend(
            "docker",
            docker_memory_limit=1024 * 1024 * 1024,
            docker_pids_limit=500,
            docker_cpu_period=200_000,
            docker_cpu_quota=150_000,
        )
        assert isinstance(backend, DockerBackend)
        assert backend._memory_limit == 1024 * 1024 * 1024
        assert backend._pids_limit == 500
        assert backend._cpu_period == 200_000
        assert backend._cpu_quota == 150_000


class TestFileValidation:
    """Tests for _validate_runner_file and _validate_tool_filename path traversal blocks."""

    def test_runner_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("../../exploit.js")

    def test_runner_rejects_absolute_path(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("/etc/passwd")

    def test_runner_rejects_null_byte(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("runner\x00.mjs")

    def test_runner_rejects_backslash(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("subdir\\runner.mjs")

    def test_runner_rejects_empty(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_runner_file("")

    def test_runner_accepts_valid_filename(self):
        """Valid runner filenames do not raise."""
        _validate_runner_file("runner.mjs")
        _validate_runner_file("agent-runner-v2.js")

    def test_tool_rejects_slash(self):
        with pytest.raises(ValueError, match="contains path separator"):
            _validate_tool_filename("subdir/exploit.py")

    def test_tool_rejects_backslash(self):
        with pytest.raises(ValueError, match="contains path separator"):
            _validate_tool_filename("subdir\\exploit.py")

    def test_tool_rejects_dot_dot(self):
        with pytest.raises(ValueError, match="Invalid tool filename"):
            _validate_tool_filename("..exploit.py")

    def test_tool_rejects_dot_prefix(self):
        with pytest.raises(ValueError, match="starts with dot"):
            _validate_tool_filename(".hidden")

    def test_tool_rejects_null_byte(self):
        with pytest.raises(ValueError, match="Invalid tool filename"):
            _validate_tool_filename("tool\x00.py")

    def test_tool_rejects_empty(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_tool_filename("")

    def test_tool_accepts_valid_filename(self):
        """Valid tool filenames do not raise."""
        _validate_tool_filename("slack.py")
        _validate_tool_filename("jira-connector.js")


# ===========================================================================
# 6. SSEEvent dataclass
# ===========================================================================


class TestSSEEvent:
    """Tests for the SSEEvent data structure."""

    def test_sse_event_creation(self):
        event = SSEEvent(event="result", data={"output": "hello"})
        assert event.event == "result"
        assert event.data == {"output": "hello"}

    def test_sse_event_error_type(self):
        event = SSEEvent(event="error", data={"type": "error", "error": "timeout"})
        assert event.event == "error"
        assert event.data["error"] == "timeout"
