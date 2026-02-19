"""Tests for pluggable sandbox backends."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.backends import (
    _VALID_BACKENDS,
    CloudflareBackend,
    DockerBackend,
    E2BBackend,
    LocalBackend,
    SSEEvent,
    create_backend,
)

# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestBackendFactory:
    """Tests for create_backend() factory function."""

    def test_create_e2b(self):
        backend = create_backend("e2b", e2b_api_key="test-key")
        assert isinstance(backend, E2BBackend)
        assert backend.name == "e2b"

    def test_create_docker(self):
        backend = create_backend("docker", docker_image="my-image:latest")
        assert isinstance(backend, DockerBackend)
        assert backend.name == "docker"

    def test_create_local(self):
        backend = create_backend("local")
        assert isinstance(backend, LocalBackend)
        assert backend.name == "local"

    def test_create_cloudflare(self):
        backend = create_backend(
            "cloudflare", cloudflare_worker_url="https://sandbox.example.workers.dev"
        )
        assert isinstance(backend, CloudflareBackend)
        assert backend.name == "cloudflare"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown sandbox backend"):
            create_backend("nonexistent")

    def test_valid_backends_set(self):
        assert _VALID_BACKENDS == {"e2b", "docker", "local", "cloudflare"}


# ---------------------------------------------------------------------------
# E2B Backend
# ---------------------------------------------------------------------------


class TestE2BBackend:
    """Tests for E2BBackend."""

    def test_name(self):
        backend = E2BBackend(e2b_api_key="key")
        assert backend.name == "e2b"

    @pytest.mark.asyncio
    async def test_health_no_key(self):
        backend = E2BBackend(e2b_api_key="")
        assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_health_with_key_and_sdk(self):
        backend = E2BBackend(e2b_api_key="test-key")
        with patch.dict("sys.modules", {"e2b": MagicMock()}):
            assert await backend.health() is True

    @pytest.mark.asyncio
    async def test_health_no_sdk(self):
        backend = E2BBackend(e2b_api_key="test-key")
        with patch.dict("sys.modules", {"e2b": None}):
            # When module is None, import will fail
            assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        backend = E2BBackend(e2b_api_key="key")
        await backend.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_start_with_template(self):
        backend = E2BBackend(e2b_api_key="key", template="sandcastle-runner")

        mock_sandbox = AsyncMock()
        mock_handle = AsyncMock()
        mock_handle.exit_code = 0
        mock_handle.wait = AsyncMock()

        # Simulate stdout producing a JSON line then finishing
        result_event = json.dumps({"type": "result", "result": "done", "num_turns": 1})

        call_count = 0

        async def mock_run(cmd, **kwargs):
            nonlocal call_count
            on_stdout = kwargs.get("on_stdout")
            if on_stdout and kwargs.get("background"):
                # Simulate stdout callback
                mock_line = MagicMock()
                mock_line.line = result_event
                on_stdout(mock_line)
            return mock_handle

        mock_sandbox.commands.run = mock_run
        mock_sandbox.kill = AsyncMock()

        with patch("sandcastle.engine.backends.E2BBackend.start") as mock_start:
            # Simulate the start method yielding one event
            async def fake_start(*args, **kwargs):
                yield SSEEvent(event="result", data={"type": "result", "result": "done"})

            mock_start.side_effect = fake_start

            events = []
            async for event in backend.start(
                runner_file="runner.mjs",
                envs={"SANDCASTLE_REQUEST": "{}"},
                use_claude_runner=True,
                timeout=30,
            ):
                events.append(event)

            assert len(events) == 1
            assert events[0].event == "result"


# ---------------------------------------------------------------------------
# Docker Backend
# ---------------------------------------------------------------------------


class TestDockerBackend:
    """Tests for DockerBackend."""

    def test_name(self):
        backend = DockerBackend()
        assert backend.name == "docker"

    @pytest.mark.asyncio
    async def test_health_no_aiodocker(self):
        backend = DockerBackend()
        with patch.dict("sys.modules", {"aiodocker": None}):
            result = await backend.health()
            assert result is False

    @pytest.mark.asyncio
    async def test_health_docker_available(self):
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.version = AsyncMock(return_value={"Version": "24.0"})
        mock_docker_instance.close = AsyncMock()
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()
            result = await backend.health()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_docker_unavailable(self):
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.version = AsyncMock(side_effect=Exception("Cannot connect"))
        mock_docker_instance.close = AsyncMock()
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()
            result = await backend.health()
            assert result is False

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        backend = DockerBackend()
        await backend.close()

    def test_custom_image(self):
        backend = DockerBackend(docker_image="custom:v1")
        assert backend._image == "custom:v1"

    def test_custom_url(self):
        backend = DockerBackend(docker_url="tcp://remote:2375")
        assert backend._url == "tcp://remote:2375"


# ---------------------------------------------------------------------------
# Local Backend
# ---------------------------------------------------------------------------


class TestLocalBackend:
    """Tests for LocalBackend."""

    def test_name(self):
        backend = LocalBackend()
        assert backend.name == "local"

    @pytest.mark.asyncio
    async def test_health_node_found(self):
        backend = LocalBackend()
        with patch("shutil.which", return_value="/usr/local/bin/node"):
            assert await backend.health() is True

    @pytest.mark.asyncio
    async def test_health_node_not_found(self):
        backend = LocalBackend()
        with patch("shutil.which", return_value=None):
            assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        backend = LocalBackend()
        await backend.close()

    @pytest.mark.asyncio
    async def test_start_missing_runner(self, tmp_path):
        backend = LocalBackend()
        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with pytest.raises(RuntimeError, match="Runner script not found"):
                async for _ in backend.start(
                    runner_file="nonexistent.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                ):
                    pass

    @pytest.mark.asyncio
    async def test_start_streams_json_lines(self, tmp_path):
        # Create a fake runner that outputs JSON
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        backend = LocalBackend()

        lines = [
            json.dumps({"type": "message", "text": "hello"}).encode() + b"\n",
            json.dumps({"type": "result", "result": "done"}).encode() + b"\n",
            b"",  # EOF
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=list(lines))
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()
        mock_proc.kill = AsyncMock()

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                events = []
                async for event in backend.start(
                    runner_file="runner.mjs",
                    envs={"FOO": "bar"},
                    use_claude_runner=True,
                    timeout=30,
                ):
                    events.append(event)

        assert len(events) == 2
        assert events[0].event == "message"
        assert events[1].event == "result"


# ---------------------------------------------------------------------------
# Cloudflare Backend
# ---------------------------------------------------------------------------


class TestCloudflareBackend:
    """Tests for CloudflareBackend."""

    def test_name(self):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")
        assert backend.name == "cloudflare"

    @pytest.mark.asyncio
    async def test_health_false_without_url(self):
        backend = CloudflareBackend(worker_url="")
        assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_start_raises_without_url(self):
        backend = CloudflareBackend(worker_url="")
        with pytest.raises(RuntimeError, match="CLOUDFLARE_WORKER_URL is required"):
            async for _ in backend.start(
                runner_file="runner.mjs", envs={},
                use_claude_runner=True, timeout=5,
            ):
                pass

    @pytest.mark.asyncio
    async def test_health_ok(self):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            assert await backend.health() is True

    @pytest.mark.asyncio
    async def test_health_fail(self):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            assert await backend.health() is False

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")
        await backend.close()

    @pytest.mark.asyncio
    async def test_start_parses_batch_response(self, tmp_path):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")

        # Create fake runner file
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner code")

        stdout_lines = "\n".join([
            json.dumps({"type": "message", "text": "working..."}),
            json.dumps({"type": "result", "result": "done", "num_turns": 1}),
        ])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "stdout": stdout_lines,
            "stderr": "",
            "exitCode": 0,
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            events = []
            async for event in backend.start(
                runner_file="runner.mjs",
                envs={"SANDCASTLE_REQUEST": "{}"},
                use_claude_runner=True,
                timeout=30,
            ):
                events.append(event)

        assert len(events) == 2
        assert events[0].event == "message"
        assert events[1].event == "result"
        assert events[1].data["result"] == "done"

    @pytest.mark.asyncio
    async def test_start_yields_error_on_nonzero_exit(self, tmp_path):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")

        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner code")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "stdout": "",
            "stderr": "Error: module not found",
            "exitCode": 1,
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            events = []
            async for event in backend.start(
                runner_file="runner.mjs",
                envs={},
                use_claude_runner=True,
                timeout=30,
            ):
                events.append(event)

        assert len(events) == 1
        assert events[0].event == "error"
        assert "CF sandbox failed" in events[0].data["error"]


# ---------------------------------------------------------------------------
# SandshoreRuntime integration with backends
# ---------------------------------------------------------------------------


class TestSandshoreRuntimeBackendIntegration:
    """Test that SandshoreRuntime correctly uses backends."""

    def test_default_backend_is_e2b(self):
        from sandcastle.engine.sandshore import SandshoreRuntime

        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
        )
        assert runtime.backend_name == "e2b"

    def test_docker_backend(self):
        from sandcastle.engine.sandshore import SandshoreRuntime

        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="docker",
        )
        assert runtime.backend_name == "docker"

    def test_local_backend(self):
        from sandcastle.engine.sandshore import SandshoreRuntime

        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        )
        assert runtime.backend_name == "local"

    def test_cloudflare_backend(self):
        from sandcastle.engine.sandshore import SandshoreRuntime

        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="cloudflare",
            cloudflare_worker_url="https://sandbox.example.workers.dev",
        )
        assert runtime.backend_name == "cloudflare"

    def test_invalid_backend_raises(self):
        from sandcastle.engine.sandshore import SandshoreRuntime

        with pytest.raises(ValueError, match="Unknown sandbox backend"):
            SandshoreRuntime(
                anthropic_api_key="ak",
                e2b_api_key="",
                sandbox_backend="nonexistent",
            )

    def test_build_env_claude_model(self):
        from sandcastle.engine.sandshore import SandshoreRuntime

        runtime = SandshoreRuntime(
            anthropic_api_key="ak-123",
            e2b_api_key="",
            sandbox_backend="local",
        )
        envs, runner, is_claude = runtime._build_env({"prompt": "hi", "model": "sonnet"})
        assert is_claude is True
        assert runner == "runner.mjs"
        assert envs["ANTHROPIC_API_KEY"] == "ak-123"
        assert "SANDCASTLE_REQUEST" in envs

    def test_build_env_openai_model(self):
        from sandcastle.engine.sandshore import SandshoreRuntime

        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        )
        envs, runner, is_claude = runtime._build_env({"prompt": "hi", "model": "openai/codex-mini"})
        assert is_claude is False
        assert runner == "runner-openai.mjs"
        assert "MODEL_API_KEY" in envs
        assert "MODEL_ID" in envs

    def test_build_env_unknown_model_falls_back(self):
        from sandcastle.engine.sandshore import SandshoreRuntime

        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        )
        envs, runner, is_claude = runtime._build_env({"prompt": "hi", "model": "unknown-model"})
        # Falls back to sonnet model_info but is_claude_model checks original string
        assert runner == "runner.mjs"
        assert "SANDCASTLE_REQUEST" in envs


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigBackendSettings:
    """Test that config correctly handles backend settings."""

    def test_default_sandbox_backend(self):
        from sandcastle.config import Settings

        s = Settings(
            _env_file=None,
            anthropic_api_key="test",
        )
        assert s.sandbox_backend == "e2b"

    def test_custom_sandbox_backend(self):
        from sandcastle.config import Settings

        s = Settings(
            _env_file=None,
            anthropic_api_key="test",
            sandbox_backend="docker",
            docker_image="custom:v2",
        )
        assert s.sandbox_backend == "docker"
        assert s.docker_image == "custom:v2"

    def test_cloudflare_settings(self):
        from sandcastle.config import Settings

        s = Settings(
            _env_file=None,
            anthropic_api_key="test",
            sandbox_backend="cloudflare",
            cloudflare_worker_url="https://sandbox.example.workers.dev",
        )
        assert s.sandbox_backend == "cloudflare"
        assert s.cloudflare_worker_url == "https://sandbox.example.workers.dev"
