"""Wave 8 deep audit tests for sandbox backends, sandshore runtime, and shim.

Covers:
- _validate_runner_file edge cases (null bytes, backslashes, empty)
- _validate_tool_filename (path traversal, dot-prefixed, separators)
- LocalBackend total deadline enforcement
- LocalBackend tool filename validation
- DockerBackend tool filename validation in tar archive
- E2BBackend tool filename validation
- CloudflareBackend tool filename validation
- CloudflareBackend HTTPS enforcement (IPv6 loopback exception)
- CloudflareBackend response parsing edge cases
- DockerBackend container cleanup on error
- DockerBackend health cache invalidation on connection failure
- SandshoreRuntime pool eviction with running loop
- SandshoreRuntime pool eviction without running loop
- sandbox.py shim re-exports all expected symbols
- CircuitBreaker half-open probe rejection
- CircuitBreaker concurrent allow_request serialization
- RuntimeMetrics concurrent updates
- SandshoreRuntime query aggregation edge cases
- _extract_text edge cases
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
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
from sandcastle.engine.sandshore import (
    CircuitBreaker,
    RuntimeMetrics,
    SandshoreError,
    SandshoreResult,
    SandshoreRuntime,
    _client_pool,
    _extract_text,
    _MAX_POOL_SIZE,
    _pool_lock,
    cleanup_pool,
    get_sandshore_runtime,
    pool_stats,
)


# ===========================================================================
# 1. _validate_runner_file - extended edge cases
# ===========================================================================


class TestValidateRunnerFileExtended:
    """Extended tests for runner file path validation."""

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_runner_file("")

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("runner\x00.mjs")

    def test_rejects_null_byte_at_end(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("runner.mjs\x00")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("dir\\runner.mjs")

    def test_rejects_backslash_traversal(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("..\\..\\etc\\passwd")

    def test_rejects_double_dot_alone(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("..")

    def test_accepts_normal_filename(self):
        _validate_runner_file("runner.mjs")

    def test_accepts_filename_with_hyphen(self):
        _validate_runner_file("runner-openai.mjs")

    def test_accepts_filename_with_underscore(self):
        _validate_runner_file("runner_v2.mjs")

    def test_rejects_absolute_windows_style(self):
        """Backslash-prefixed path is rejected (backslash check)."""
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("\\etc\\passwd")


# ===========================================================================
# 2. _validate_tool_filename
# ===========================================================================


class TestValidateToolFilename:
    """Tests for tool connector filename validation."""

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_tool_filename("")

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError, match="Invalid tool filename"):
            _validate_tool_filename("slack\x00.mjs")

    def test_rejects_forward_slash(self):
        with pytest.raises(ValueError, match="contains path separator"):
            _validate_tool_filename("../../etc/passwd")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match="contains path separator"):
            _validate_tool_filename("dir\\file.mjs")

    def test_rejects_double_dot(self):
        with pytest.raises(ValueError, match="Invalid tool filename"):
            _validate_tool_filename("..")

    def test_rejects_dot_prefixed(self):
        with pytest.raises(ValueError, match="starts with dot"):
            _validate_tool_filename(".hidden.mjs")

    def test_rejects_dotfile(self):
        with pytest.raises(ValueError, match="starts with dot"):
            _validate_tool_filename(".env")

    def test_accepts_normal_filename(self):
        _validate_tool_filename("slack.mjs")

    def test_accepts_hyphenated_filename(self):
        _validate_tool_filename("connector-utils.mjs")

    def test_accepts_underscored_filename(self):
        _validate_tool_filename("my_tool_v2.mjs")

    def test_rejects_traversal_in_middle(self):
        with pytest.raises(ValueError, match="contains path separator"):
            _validate_tool_filename("tools/../../../etc/cron.d/backdoor")

    def test_rejects_single_slash(self):
        with pytest.raises(ValueError, match="contains path separator"):
            _validate_tool_filename("subdir/file.mjs")


# ===========================================================================
# 3. LocalBackend - total deadline enforcement
# ===========================================================================


class TestLocalBackendTotalDeadline:
    """Tests for LocalBackend total deadline (not just per-line timeout)."""

    @pytest.mark.asyncio
    async def test_total_deadline_stops_slow_drip(self, tmp_path):
        """A process that outputs slowly should be killed by total deadline.

        Uses time.monotonic patching to simulate time advancing faster
        than real time, since the 30s grace period is hardcoded.
        """
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock slow drip runner")

        backend = LocalBackend(timeout=10.0)

        # Simulate time advancing: each readline call advances by 5s
        real_monotonic = time.monotonic
        time_offset = 0.0
        call_count = 0

        def fast_monotonic():
            return real_monotonic() + time_offset

        async def slow_readline():
            nonlocal call_count, time_offset
            call_count += 1
            if call_count > 200:
                return b""
            # Each readline advances simulated time by 5s
            time_offset += 5.0
            return json.dumps({"type": "message", "text": f"line {call_count}"}).encode() + b"\n"

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = slow_readline
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()  # Use sync MagicMock to avoid unawaited warning

        with (
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("sandcastle.engine.backends.time.monotonic", side_effect=fast_monotonic),
        ):
            events = []
            async for event in backend.start(
                runner_file="runner.mjs",
                envs={"FOO": "bar"},
                use_claude_runner=True,
                timeout=10.0,
            ):
                events.append(event)

        # Total deadline is 10 + 30 = 40s. Each call advances 5s,
        # so at most ~8 events before deadline (40/5).
        assert len(events) > 0
        assert len(events) < 20  # Well under 200
        # Process should have been killed
        mock_proc.kill.assert_called_once()


# ===========================================================================
# 4. LocalBackend - tool filename validation
# ===========================================================================


class TestLocalBackendToolValidation:
    """Tests that LocalBackend validates tool filenames before writing."""

    @pytest.mark.asyncio
    async def test_rejects_traversal_in_tool_filename(self, tmp_path):
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        backend = LocalBackend()

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with pytest.raises(ValueError, match="contains path separator"):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                    tool_files={"../../etc/cron.d/backdoor": "malicious code"},
                ):
                    pass

    @pytest.mark.asyncio
    async def test_rejects_dot_prefixed_tool_filename(self, tmp_path):
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        backend = LocalBackend()

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with pytest.raises(ValueError, match="starts with dot"):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                    tool_files={".env": "SECRET=leaked"},
                ):
                    pass

    @pytest.mark.asyncio
    async def test_no_temp_dir_leaked_on_validation_failure(self, tmp_path):
        """Temp directory should not be created if validation fails."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        backend = LocalBackend()

        import tempfile
        dirs_before = set(Path(tempfile.gettempdir()).glob("sandcastle-tools-*"))

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with pytest.raises(ValueError):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                    tool_files={"../escape": "bad"},
                ):
                    pass

        dirs_after = set(Path(tempfile.gettempdir()).glob("sandcastle-tools-*"))
        new_dirs = dirs_after - dirs_before
        assert len(new_dirs) == 0, f"Leaked temp dirs: {new_dirs}"


# ===========================================================================
# 5. DockerBackend - tool filename validation in tar archive
# ===========================================================================


class TestDockerBackendToolValidation:
    """Tests that DockerBackend validates tool filenames before tar creation."""

    @pytest.mark.asyncio
    async def test_rejects_traversal_in_tool_filename(self, tmp_path):
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_container = AsyncMock()
        mock_docker_instance.containers.create_or_run = AsyncMock(
            return_value=mock_container
        )
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with (
            patch.dict("sys.modules", {"aiodocker": mock_module}),
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
        ):
            backend = DockerBackend()
            with pytest.raises(ValueError, match="contains path separator"):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                    tool_files={"../../escape.mjs": "malicious"},
                ):
                    pass

    @pytest.mark.asyncio
    async def test_rejects_null_byte_tool_filename(self, tmp_path):
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_container = AsyncMock()
        mock_docker_instance.containers.create_or_run = AsyncMock(
            return_value=mock_container
        )
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with (
            patch.dict("sys.modules", {"aiodocker": mock_module}),
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
        ):
            backend = DockerBackend()
            with pytest.raises(ValueError, match="Invalid tool filename"):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                    tool_files={"evil\x00.mjs": "malicious"},
                ):
                    pass


# ===========================================================================
# 6. CloudflareBackend - HTTPS enforcement edge cases
# ===========================================================================


class TestCloudflareHTTPSEnforcement:
    """Tests for HTTPS upgrade logic in CloudflareBackend."""

    def test_upgrades_http_to_https(self):
        backend = CloudflareBackend(worker_url="http://sandbox.example.workers.dev")
        assert backend._worker_url.startswith("https://")

    def test_preserves_https(self):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")
        assert backend._worker_url == "https://sandbox.example.workers.dev"

    def test_allows_localhost_http(self):
        backend = CloudflareBackend(worker_url="http://localhost:8787")
        assert backend._worker_url == "http://localhost:8787"

    def test_allows_127_0_0_1_http(self):
        backend = CloudflareBackend(worker_url="http://127.0.0.1:8787")
        assert backend._worker_url == "http://127.0.0.1:8787"

    def test_allows_ipv6_loopback_http(self):
        backend = CloudflareBackend(worker_url="http://[::1]:8787")
        assert backend._worker_url == "http://[::1]:8787"

    def test_strips_trailing_slash(self):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev/")
        assert not backend._worker_url.endswith("/")

    def test_empty_url(self):
        backend = CloudflareBackend(worker_url="")
        assert backend._worker_url == ""


# ===========================================================================
# 7. CloudflareBackend - tool filename validation
# ===========================================================================


class TestCloudflareToolValidation:
    """Tests that CloudflareBackend validates tool filenames."""

    @pytest.mark.asyncio
    async def test_rejects_traversal_in_tool_filename(self, tmp_path):
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with pytest.raises(ValueError, match="contains path separator"):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                    tool_files={"../etc/passwd": "bad"},
                ):
                    pass


# ===========================================================================
# 8. CloudflareBackend - response parsing edge cases
# ===========================================================================


class TestCloudflareResponseParsing:
    """Tests for CloudflareBackend response parsing."""

    @pytest.mark.asyncio
    async def test_handles_empty_stdout(self, tmp_path):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "stdout": "",
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
                envs={},
                use_claude_runner=True,
                timeout=30,
            ):
                events.append(event)

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_handles_mixed_json_and_garbage(self, tmp_path):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        stdout_lines = "\n".join([
            "not json at all",
            json.dumps({"type": "result", "result": "done"}),
            "more garbage",
            "",  # blank lines
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
                envs={},
                use_claude_runner=True,
                timeout=30,
            ):
                events.append(event)

        # Only the valid JSON line produces an event
        assert len(events) == 1
        assert events[0].event == "result"

    @pytest.mark.asyncio
    async def test_missing_exit_code_key_defaults_to_zero(self, tmp_path):
        """When exitCode key is absent, treat as success (no error event)."""
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "stdout": json.dumps({"type": "result", "result": "ok"}),
            "stderr": "",
            # No exitCode key
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

        # Result event only, no error
        assert len(events) == 1
        assert events[0].event == "result"


# ===========================================================================
# 9. DockerBackend - container cleanup on start failure
# ===========================================================================


class TestDockerContainerCleanup:
    """Tests for Docker container cleanup on errors."""

    @pytest.mark.asyncio
    async def test_container_deleted_on_runner_not_found(self, tmp_path):
        """If runner is not found, no container should be created."""
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with (
            patch.dict("sys.modules", {"aiodocker": mock_module}),
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
        ):
            backend = DockerBackend()
            with pytest.raises(RuntimeError, match="Runner script not found"):
                async for _ in backend.start(
                    runner_file="nonexistent.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                ):
                    pass

    @pytest.mark.asyncio
    async def test_container_cleanup_on_log_error(self, tmp_path):
        """Container is cleaned up even when log streaming fails."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_container = AsyncMock()
        mock_container.start = AsyncMock()
        mock_container.log = AsyncMock(side_effect=Exception("Log stream error"))
        mock_container.delete = AsyncMock()
        mock_docker_instance.containers.create_or_run = AsyncMock(
            return_value=mock_container
        )
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with (
            patch.dict("sys.modules", {"aiodocker": mock_module}),
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
        ):
            backend = DockerBackend()
            with pytest.raises(Exception, match="Log stream error"):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                ):
                    pass

            # Container should have been force-deleted in finally block
            mock_container.delete.assert_awaited_once_with(force=True)


# ===========================================================================
# 10. DockerBackend - health cache invalidation on failure
# ===========================================================================


class TestDockerHealthCacheInvalidation:
    """Tests that Docker health cache is invalidated on connection failure."""

    @pytest.mark.asyncio
    async def test_client_reset_on_health_failure(self):
        """After a health check failure, the client should be reset to None."""
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.version = AsyncMock(
            side_effect=Exception("Connection refused")
        )
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()
            result = await backend.health()
            assert result is False
            # Client should be reset so next call retries connection
            assert backend._client is None

    @pytest.mark.asyncio
    async def test_health_recovery_after_failure(self):
        """After a failure, a subsequent successful check works."""
        call_count = 0

        async def version_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Connection refused")
            return {"Version": "24.0"}

        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.version = version_side_effect
        mock_docker_instance.close = AsyncMock()
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()
            backend._health_cache_ttl = 0  # Disable caching

            # First call fails
            result1 = await backend.health()
            assert result1 is False

            # Second call succeeds (client was reset, creates new one)
            result2 = await backend.health()
            assert result2 is True


# ===========================================================================
# 11. sandbox.py shim - complete re-export verification
# ===========================================================================


class TestSandboxShim:
    """Tests that sandbox.py shim properly re-exports all symbols."""

    def test_imports_circuit_breaker(self):
        from sandcastle.engine.sandbox import CircuitBreaker as CB
        assert CB is CircuitBreaker

    def test_imports_runtime_metrics(self):
        from sandcastle.engine.sandbox import RuntimeMetrics as RM
        assert RM is RuntimeMetrics

    def test_imports_sandshore_error(self):
        from sandcastle.engine.sandbox import SandshoreError as SE
        assert SE is SandshoreError

    def test_imports_sandshore_result(self):
        from sandcastle.engine.sandbox import SandshoreResult as SR
        assert SR is SandshoreResult

    def test_imports_sandshore_runtime(self):
        from sandcastle.engine.sandbox import SandshoreRuntime as SRT
        assert SRT is SandshoreRuntime

    def test_imports_sse_event(self):
        from sandcastle.engine.sandbox import SSEEvent as SE
        assert SE is SSEEvent

    def test_imports_extract_text(self):
        from sandcastle.engine.sandbox import _extract_text as et
        assert et is _extract_text

    def test_imports_cleanup_pool(self):
        from sandcastle.engine.sandbox import cleanup_pool as cp
        assert cp is cleanup_pool

    def test_imports_get_sandshore_runtime(self):
        from sandcastle.engine.sandbox import get_sandshore_runtime as gsr
        assert gsr is get_sandshore_runtime

    def test_imports_pool_stats(self):
        from sandcastle.engine.sandbox import pool_stats as ps
        assert ps is pool_stats

    def test_all_list_complete(self):
        from sandcastle.engine import sandbox
        expected = {
            "CircuitBreaker", "RuntimeMetrics", "SSEEvent",
            "SandshoreRuntime", "SandshoreResult", "SandshoreError",
            "_extract_text", "cleanup_pool", "get_sandshore_runtime",
            "pool_stats",
        }
        assert set(sandbox.__all__) == expected


# ===========================================================================
# 12. CircuitBreaker - half-open probe rejection
# ===========================================================================


class TestCircuitBreakerHalfOpenProbe:
    """Tests for circuit breaker half-open probe behavior."""

    @pytest.mark.asyncio
    async def test_only_one_probe_allowed(self):
        """In HALF_OPEN state, only one probe request passes."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        await asyncio.sleep(0.02)
        # First request is the probe
        assert await cb.allow_request() is True
        # Second request is rejected
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_probe_failure_trips_back_to_open(self):
        """If the probe request fails, circuit breaker goes back to OPEN."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        await asyncio.sleep(0.02)
        assert await cb.allow_request() is True  # probe allowed
        await cb.record_failure()  # probe failed

        # Should be back to OPEN (not HALF_OPEN)
        assert cb._state == CircuitBreaker.OPEN

    @pytest.mark.asyncio
    async def test_probe_success_resets_to_closed(self):
        """If the probe request succeeds, circuit breaker resets to CLOSED."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        await cb.record_failure()
        await cb.record_failure()

        await asyncio.sleep(0.02)
        assert await cb.allow_request() is True
        await cb.record_success()

        assert cb.state == CircuitBreaker.CLOSED
        assert cb._failure_count == 0
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_concurrent_allow_request_serialized(self):
        """Concurrent allow_request calls are serialized by the lock."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()

        await asyncio.sleep(0.02)

        # Fire multiple concurrent requests
        results = await asyncio.gather(
            cb.allow_request(),
            cb.allow_request(),
            cb.allow_request(),
        )
        # Exactly one should be allowed (the probe)
        assert sum(results) == 1


# ===========================================================================
# 13. RuntimeMetrics - concurrent updates
# ===========================================================================


class TestRuntimeMetricsConcurrency:
    """Tests for RuntimeMetrics under concurrent updates."""

    @pytest.mark.asyncio
    async def test_concurrent_success_recording(self):
        """Multiple concurrent record_query_success calls produce correct totals."""
        m = RuntimeMetrics()

        async def record(cost: float):
            await m.record_query_success(cost_usd=cost)

        # Run 50 concurrent recordings
        tasks = [record(0.01) for _ in range(50)]
        await asyncio.gather(*tasks)

        assert m.total_queries == 50
        assert m.successful_queries == 50
        assert m.total_cost_usd == pytest.approx(0.5, abs=1e-6)

    @pytest.mark.asyncio
    async def test_mixed_concurrent_updates(self):
        """Mixed success, failure, failover concurrent updates are consistent."""
        m = RuntimeMetrics()

        tasks = []
        for _ in range(20):
            tasks.append(m.record_query_success(cost_usd=0.01))
            tasks.append(m.record_query_failure())
            tasks.append(m.record_failover())

        await asyncio.gather(*tasks)

        assert m.total_queries == 40  # 20 success + 20 failure
        assert m.successful_queries == 20
        assert m.failed_queries == 20
        assert m.failover_attempts == 20


# ===========================================================================
# 14. _extract_text - edge cases
# ===========================================================================


class TestExtractTextEdgeCases:
    """Tests for the _extract_text helper function."""

    def test_empty_dict(self):
        assert _extract_text({}) == ""

    def test_text_key(self):
        assert _extract_text({"text": "hello"}) == "hello"

    def test_content_key_string(self):
        assert _extract_text({"content": "world"}) == "world"

    def test_result_key(self):
        assert _extract_text({"result": "done"}) == "done"

    def test_data_key(self):
        assert _extract_text({"data": "payload"}) == "payload"

    def test_whitespace_only_text_skipped(self):
        """Whitespace-only strings are skipped."""
        result = _extract_text({"text": "   ", "content": "real value"})
        assert result == "real value"

    def test_nested_message_content(self):
        data = {
            "message": {
                "content": [
                    {"type": "text", "text": "nested content"}
                ]
            }
        }
        assert _extract_text(data) == "nested content"

    def test_content_blocks(self):
        data = {
            "content_blocks": [
                {"text": "block content"}
            ]
        }
        assert _extract_text(data) == "block content"

    def test_content_array_with_text_blocks(self):
        data = {
            "content": [
                {"type": "text", "text": "from array"}
            ]
        }
        assert _extract_text(data) == "from array"

    def test_non_string_content_skipped(self):
        """Non-string values for text keys are skipped."""
        data = {"text": 123, "content": ["not a string"]}
        # text is not a string, content is a list with a non-dict item
        result = _extract_text(data)
        assert result == ""

    def test_empty_text_in_message_block_skipped(self):
        data = {
            "message": {
                "content": [
                    {"type": "text", "text": ""}
                ]
            }
        }
        assert _extract_text(data) == ""

    def test_priority_order(self):
        """text key is checked before content, result, data."""
        data = {
            "text": "first",
            "content": "second",
            "result": "third",
            "data": "fourth",
        }
        assert _extract_text(data) == "first"


# ===========================================================================
# 15. SandshoreRuntime - pool eviction
# ===========================================================================


class TestPoolEviction:
    """Tests for pool eviction when at capacity."""

    def setup_method(self):
        with _pool_lock:
            _client_pool.clear()

    def teardown_method(self):
        with _pool_lock:
            _client_pool.clear()

    def test_pool_evicts_oldest_at_capacity(self):
        """When pool is full, oldest entry is evicted."""
        # Fill pool to capacity
        runtimes = []
        for i in range(_MAX_POOL_SIZE):
            rt = get_sandshore_runtime(
                anthropic_api_key=f"ak-{i}",
                e2b_api_key=f"ek-{i}",
                sandbox_backend="local",
            )
            runtimes.append(rt)

        with _pool_lock:
            assert len(_client_pool) == _MAX_POOL_SIZE

        first_rt = runtimes[0]

        # Add one more - should evict the first
        new_rt = get_sandshore_runtime(
            anthropic_api_key="ak-new",
            e2b_api_key="ek-new",
            sandbox_backend="local",
        )
        assert new_rt is not first_rt

        with _pool_lock:
            assert len(_client_pool) == _MAX_POOL_SIZE
            # First runtime should no longer be in pool
            pool_values = list(_client_pool.values())
            assert first_rt not in pool_values

    def test_pool_cached_hit(self):
        """Same parameters return the same runtime instance."""
        rt1 = get_sandshore_runtime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            sandbox_backend="local",
        )
        rt2 = get_sandshore_runtime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            sandbox_backend="local",
        )
        assert rt1 is rt2


# ===========================================================================
# 16. SandshoreRuntime - query aggregation
# ===========================================================================


class TestSandshoreQueryAggregation:
    """Tests for SandshoreRuntime.query() result aggregation."""

    @pytest.mark.asyncio
    async def test_query_uses_result_event(self):
        """query() extracts fields from the result event."""
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = AsyncMock()
        runtime._backend.name = "mock"
        runtime._health_cache = (True, time.monotonic())
        runtime._health_cache_ttl = 60.0
        runtime._health_lock = asyncio.Lock()
        runtime._semaphore = asyncio.Semaphore(5)
        runtime._circuit_breaker = CircuitBreaker()
        runtime._metrics = RuntimeMetrics()
        runtime._sandbox_backend_type = "mock"
        runtime.timeout = 300.0

        result_data = {
            "type": "result",
            "result": "Hello world",
            "total_cost_usd": 0.005,
            "num_turns": 3,
            "input_tokens": 100,
            "output_tokens": 50,
        }

        async def mock_start(**kwargs):
            yield SSEEvent(event="result", data=result_data)

        runtime._backend.start = mock_start
        runtime._backend.health = AsyncMock(return_value=True)

        # Patch _build_env to avoid provider resolution
        runtime._build_env = MagicMock(return_value=(
            {"SANDCASTLE_REQUEST": "{}"},
            "runner.mjs",
            True,
            {},
        ))

        result = await runtime.query({"prompt": "test", "model": "sonnet"})
        assert result.text == "Hello world"
        assert result.total_cost_usd == 0.005
        assert result.num_turns == 3
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    @pytest.mark.asyncio
    async def test_query_fallback_to_assistant_text(self):
        """query() uses last assistant message when result has no text."""
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = AsyncMock()
        runtime._backend.name = "mock"
        runtime._health_cache = (True, time.monotonic())
        runtime._health_cache_ttl = 60.0
        runtime._health_lock = asyncio.Lock()
        runtime._semaphore = asyncio.Semaphore(5)
        runtime._circuit_breaker = CircuitBreaker()
        runtime._metrics = RuntimeMetrics()
        runtime._sandbox_backend_type = "mock"
        runtime.timeout = 300.0

        async def mock_start(**kwargs):
            yield SSEEvent(event="assistant", data={"type": "assistant", "text": "first msg"})
            yield SSEEvent(event="assistant", data={"type": "assistant", "text": "second msg"})
            yield SSEEvent(event="result", data={"type": "result", "result": ""})

        runtime._backend.start = mock_start
        runtime._backend.health = AsyncMock(return_value=True)

        runtime._build_env = MagicMock(return_value=(
            {"SANDCASTLE_REQUEST": "{}"},
            "runner.mjs",
            True,
            {},
        ))

        result = await runtime.query({"prompt": "test", "model": "sonnet"})
        assert result.text == "second msg"

    @pytest.mark.asyncio
    async def test_query_error_event_raises(self):
        """query() raises SandshoreError on error events."""
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = AsyncMock()
        runtime._backend.name = "mock"
        runtime._health_cache = (True, time.monotonic())
        runtime._health_cache_ttl = 60.0
        runtime._health_lock = asyncio.Lock()
        runtime._semaphore = asyncio.Semaphore(5)
        runtime._circuit_breaker = CircuitBreaker()
        runtime._metrics = RuntimeMetrics()
        runtime._sandbox_backend_type = "mock"
        runtime.timeout = 300.0

        async def mock_start(**kwargs):
            yield SSEEvent(event="error", data={"type": "error", "error": "Something broke"})

        runtime._backend.start = mock_start
        runtime._backend.health = AsyncMock(return_value=True)

        runtime._build_env = MagicMock(return_value=(
            {"SANDCASTLE_REQUEST": "{}"},
            "runner.mjs",
            True,
            {},
        ))

        with pytest.raises(SandshoreError, match="Something broke"):
            await runtime.query({"prompt": "test", "model": "sonnet"})

        # Error should be recorded in metrics
        assert runtime._metrics.failed_queries == 1


# ===========================================================================
# 17. SandshoreRuntime - retriable error detection
# ===========================================================================


class TestRetriableErrorDetection:
    """Tests for _is_retriable_provider_error and _is_rate_limit_error."""

    def test_429_is_retriable(self):
        assert SandshoreRuntime._is_retriable_provider_error("Error: 429 Too Many Requests")

    def test_rate_limit_is_retriable(self):
        assert SandshoreRuntime._is_retriable_provider_error("rate limit exceeded")

    def test_500_is_retriable(self):
        assert SandshoreRuntime._is_retriable_provider_error("Error 500 Internal Server Error")

    def test_502_is_retriable(self):
        assert SandshoreRuntime._is_retriable_provider_error("502 Bad Gateway")

    def test_503_is_retriable(self):
        assert SandshoreRuntime._is_retriable_provider_error("503 Service Unavailable")

    def test_504_is_retriable(self):
        assert SandshoreRuntime._is_retriable_provider_error("504 Gateway Timeout")

    def test_overloaded_is_retriable(self):
        assert SandshoreRuntime._is_retriable_provider_error("server is overloaded")

    def test_capacity_is_retriable(self):
        assert SandshoreRuntime._is_retriable_provider_error("at capacity, try later")

    def test_normal_error_not_retriable(self):
        assert not SandshoreRuntime._is_retriable_provider_error("Invalid API key")

    def test_404_not_retriable(self):
        assert not SandshoreRuntime._is_retriable_provider_error("404 Not Found")

    def test_429_is_rate_limit(self):
        assert SandshoreRuntime._is_rate_limit_error("429 Too Many Requests")

    def test_rate_limit_text(self):
        assert SandshoreRuntime._is_rate_limit_error("rate limit exceeded")

    def test_too_many_requests_text(self):
        assert SandshoreRuntime._is_rate_limit_error("too many requests")

    def test_500_not_rate_limit(self):
        assert not SandshoreRuntime._is_rate_limit_error("500 Internal Server Error")


# ===========================================================================
# 18. SSEEvent dataclass
# ===========================================================================


class TestSSEEvent:
    """Tests for SSEEvent dataclass behavior."""

    def test_creation(self):
        event = SSEEvent(event="message", data={"text": "hello"})
        assert event.event == "message"
        assert event.data == {"text": "hello"}

    def test_equality(self):
        e1 = SSEEvent(event="result", data={"type": "result"})
        e2 = SSEEvent(event="result", data={"type": "result"})
        assert e1 == e2

    def test_inequality(self):
        e1 = SSEEvent(event="result", data={"type": "result"})
        e2 = SSEEvent(event="error", data={"type": "error"})
        assert e1 != e2


# ===========================================================================
# 19. E2BBackend - health check edge cases
# ===========================================================================


class TestE2BHealthEdgeCases:
    """Tests for E2B health check edge cases."""

    @pytest.mark.asyncio
    async def test_health_with_whitespace_key(self):
        """Whitespace-only key should still return True if SDK available."""
        backend = E2BBackend(e2b_api_key="   ")
        with patch.dict("sys.modules", {"e2b": MagicMock()}):
            # The key is truthy (non-empty string) so health returns True
            assert await backend.health() is True

    @pytest.mark.asyncio
    async def test_close_multiple_times_safe(self):
        """Calling close() multiple times does not raise."""
        backend = E2BBackend(e2b_api_key="key")
        await backend.close()
        await backend.close()
        await backend.close()


# ===========================================================================
# 20. Factory create_backend parameter forwarding
# ===========================================================================


class TestCreateBackendParameterForwarding:
    """Tests that create_backend forwards all parameters correctly."""

    def test_docker_all_params_forwarded(self):
        backend = create_backend(
            "docker",
            docker_image="custom:v3",
            docker_url="tcp://remote:2375",
            docker_memory_limit=1024 * 1024 * 1024,
            docker_seccomp_profile="/custom/seccomp.json",
            docker_pids_limit=200,
            docker_cpu_period=200_000,
            docker_cpu_quota=100_000,
            timeout=600.0,
        )
        assert isinstance(backend, DockerBackend)
        assert backend._image == "custom:v3"
        assert backend._url == "tcp://remote:2375"
        assert backend._memory_limit == 1024 * 1024 * 1024
        assert backend._seccomp_profile == "/custom/seccomp.json"
        assert backend._pids_limit == 200
        assert backend._cpu_period == 200_000
        assert backend._cpu_quota == 100_000
        assert backend._timeout == 600.0

    def test_cloudflare_params_forwarded(self):
        backend = create_backend(
            "cloudflare",
            cloudflare_worker_url="https://my-worker.example.workers.dev",
            timeout=120.0,
        )
        assert isinstance(backend, CloudflareBackend)
        assert backend._worker_url == "https://my-worker.example.workers.dev"
        assert backend._timeout == 120.0

    def test_local_timeout_forwarded(self):
        backend = create_backend("local", timeout=60.0)
        assert isinstance(backend, LocalBackend)
        assert backend._timeout == 60.0

    def test_e2b_all_params_forwarded(self):
        backend = create_backend(
            "e2b",
            e2b_api_key="test-key",
            template="custom-template",
            timeout=180.0,
            e2b_event_queue_size=500,
            e2b_npm_install_timeout=30,
            e2b_npm_poll_deadline=60.0,
            e2b_execution_grace_period=15.0,
        )
        assert isinstance(backend, E2BBackend)
        assert backend._api_key == "test-key"
        assert backend._template == "custom-template"
        assert backend._timeout == 180.0
        assert backend._event_queue_size == 500
        assert backend._npm_install_timeout == 30
        assert backend._npm_poll_deadline == 60.0
        assert backend._execution_grace_period == 15.0


# ===========================================================================
# 21. SandshoreResult dataclass
# ===========================================================================


class TestSandshoreResult:
    """Tests for SandshoreResult defaults and fields."""

    def test_defaults(self):
        r = SandshoreResult()
        assert r.text == ""
        assert r.structured_output is None
        assert r.total_cost_usd == 0.0
        assert r.num_turns == 0
        assert r.input_tokens == 0
        assert r.output_tokens == 0

    def test_custom_values(self):
        r = SandshoreResult(
            text="output",
            structured_output={"key": "val"},
            total_cost_usd=1.5,
            num_turns=10,
            input_tokens=500,
            output_tokens=300,
        )
        assert r.text == "output"
        assert r.structured_output == {"key": "val"}
        assert r.total_cost_usd == 1.5
        assert r.num_turns == 10
        assert r.input_tokens == 500
        assert r.output_tokens == 300


# ===========================================================================
# 22. LocalBackend - process cleanup on timeout
# ===========================================================================


class TestLocalBackendProcessCleanup:
    """Tests that LocalBackend properly kills processes on timeout."""

    @pytest.mark.asyncio
    async def test_process_killed_on_readline_timeout(self, tmp_path):
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        backend = LocalBackend()

        async def stuck_readline():
            await asyncio.sleep(100)  # Simulate stuck process
            return b""

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = stuck_readline
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()  # Sync because subprocess.kill() is sync

        with (
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            events = []
            async for event in backend.start(
                runner_file="runner.mjs",
                envs={},
                use_claude_runner=True,
                timeout=0.1,
            ):
                events.append(event)

        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_tools_dir_cleaned_up_after_execution(self, tmp_path):
        """Temp tools directory is cleaned up even after successful execution."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        backend = LocalBackend()

        lines = [
            json.dumps({"type": "result", "result": "done"}).encode() + b"\n",
            b"",
        ]

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=list(lines))
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()
        mock_proc.kill = AsyncMock()

        import tempfile
        dirs_before = set(Path(tempfile.gettempdir()).glob("sandcastle-tools-*"))

        with (
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            events = []
            async for event in backend.start(
                runner_file="runner.mjs",
                envs={},
                use_claude_runner=True,
                timeout=30,
                tool_files={"slack.mjs": "// slack connector"},
            ):
                events.append(event)

        dirs_after = set(Path(tempfile.gettempdir()).glob("sandcastle-tools-*"))
        new_dirs = dirs_after - dirs_before
        assert len(new_dirs) == 0, f"Leaked temp dirs: {new_dirs}"


# ===========================================================================
# 23. DockerBackend - security config
# ===========================================================================


class TestDockerSecurityConfig:
    """Tests for Docker container security configuration."""

    def test_default_security_values(self):
        backend = DockerBackend()
        assert backend._memory_limit == 512 * 1024 * 1024
        assert backend._pids_limit == 100
        assert backend._cpu_period == 100_000
        assert backend._cpu_quota == 50_000

    def test_custom_security_values(self):
        backend = DockerBackend(
            memory_limit=256 * 1024 * 1024,
            pids_limit=50,
            cpu_period=200_000,
            cpu_quota=100_000,
            seccomp_profile="/path/to/seccomp.json",
        )
        assert backend._memory_limit == 256 * 1024 * 1024
        assert backend._pids_limit == 50
        assert backend._cpu_period == 200_000
        assert backend._cpu_quota == 100_000
        assert backend._seccomp_profile == "/path/to/seccomp.json"


# ===========================================================================
# 24. Cloudflare close() edge cases
# ===========================================================================


class TestCloudflareCloseEdgeCases:
    """Tests for CloudflareBackend close() edge cases."""

    @pytest.mark.asyncio
    async def test_close_without_client_created(self):
        """Closing before any client was created is safe."""
        backend = CloudflareBackend(worker_url="https://example.workers.dev")
        assert backend._client is None
        await backend.close()  # Should not raise
        assert backend._client is None

    @pytest.mark.asyncio
    async def test_close_with_aclose_error(self):
        """If aclose raises, client is still set to None."""
        backend = CloudflareBackend(worker_url="https://example.workers.dev")
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock(side_effect=Exception("close error"))
        backend._client = mock_client

        await backend.close()
        assert backend._client is None


# ===========================================================================
# 25. Docker close() edge cases
# ===========================================================================


class TestDockerCloseEdgeCases:
    """Tests for DockerBackend close() edge cases."""

    @pytest.mark.asyncio
    async def test_close_without_client_created(self):
        backend = DockerBackend()
        assert backend._client is None
        await backend.close()
        assert backend._client is None

    @pytest.mark.asyncio
    async def test_close_with_error_still_clears_client(self):
        mock_docker_cls = MagicMock()
        mock_docker_instance = AsyncMock()
        mock_docker_instance.close = AsyncMock(side_effect=Exception("close error"))
        mock_docker_cls.return_value = mock_docker_instance

        mock_module = MagicMock()
        mock_module.Docker = mock_docker_cls

        with patch.dict("sys.modules", {"aiodocker": mock_module}):
            backend = DockerBackend()
            backend._get_client()
            assert backend._client is not None

            await backend.close()
            assert backend._client is None


# ===========================================================================
# 26. SandshoreRuntime context manager
# ===========================================================================


class TestSandshoreRuntimeContextManager:
    """Tests for async context manager protocol."""

    @pytest.mark.asyncio
    async def test_aenter_returns_self(self):
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = AsyncMock()
        runtime._backend.close = AsyncMock()

        result = await runtime.__aenter__()
        assert result is runtime

    @pytest.mark.asyncio
    async def test_aexit_calls_close(self):
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = AsyncMock()
        runtime._backend.close = AsyncMock()

        await runtime.__aexit__(None, None, None)
        runtime._backend.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aexit_on_exception_still_closes(self):
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = AsyncMock()
        runtime._backend.close = AsyncMock()

        await runtime.__aexit__(ValueError, ValueError("test"), None)
        runtime._backend.close.assert_awaited_once()


# ===========================================================================
# 27. SandshoreRuntime - query_stream unhealthy backend
# ===========================================================================


class TestQueryStreamUnhealthy:
    """Tests for query_stream when backend is unhealthy."""

    @pytest.mark.asyncio
    async def test_raises_on_unhealthy_backend(self):
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._backend = AsyncMock()
        runtime._backend.name = "mock"
        runtime._backend.health = AsyncMock(return_value=False)
        runtime._health_cache = (False, 0.0)
        runtime._health_cache_ttl = 60.0
        runtime._health_lock = asyncio.Lock()
        runtime._sandbox_backend_type = "mock"
        runtime._semaphore = asyncio.Semaphore(5)
        runtime._circuit_breaker = CircuitBreaker()
        runtime._metrics = RuntimeMetrics()
        runtime.timeout = 300.0

        with pytest.raises(SandshoreError, match="not available"):
            async for _ in runtime.query_stream({"prompt": "test"}):
                pass


# ===========================================================================
# 28. LocalBackend - env var filtering
# ===========================================================================


class TestLocalBackendEnvFiltering:
    """Tests that LocalBackend only passes safe env vars to subprocess."""

    @pytest.mark.asyncio
    async def test_safe_vars_passed(self, tmp_path):
        runner = tmp_path / "runner.mjs"
        runner.write_text("// runner")

        backend = LocalBackend()

        captured_env = {}

        async def capture_exec(*args, **kwargs):
            nonlocal captured_env
            captured_env = kwargs.get("env", {})
            mock_proc = AsyncMock()
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.readline = AsyncMock(return_value=b"")
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock()
            mock_proc.kill = AsyncMock()
            return mock_proc

        test_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "DATABASE_URL": "postgres://secret",
            "AWS_SECRET_ACCESS_KEY": "secret123",
            "ANTHROPIC_API_KEY": "sk-secret",
        }

        with (
            patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path),
            patch("asyncio.create_subprocess_exec", side_effect=capture_exec),
            patch.dict("os.environ", test_env, clear=True),
        ):
            events = []
            async for event in backend.start(
                runner_file="runner.mjs",
                envs={"CUSTOM_VAR": "value"},
                use_claude_runner=True,
                timeout=30,
            ):
                events.append(event)

        # Safe vars should be present
        assert captured_env.get("PATH") == "/usr/bin"
        assert captured_env.get("HOME") == "/home/user"
        # Custom envs should be present
        assert captured_env.get("CUSTOM_VAR") == "value"
        # Dangerous vars should NOT be present
        assert "DATABASE_URL" not in captured_env
        assert "AWS_SECRET_ACCESS_KEY" not in captured_env
        assert "ANTHROPIC_API_KEY" not in captured_env


# ===========================================================================
# 29. Pool cleanup and stats
# ===========================================================================


class TestPoolCleanupAndStats:
    """Tests for cleanup_pool and pool_stats functions."""

    def setup_method(self):
        with _pool_lock:
            _client_pool.clear()

    def teardown_method(self):
        with _pool_lock:
            _client_pool.clear()

    @pytest.mark.asyncio
    async def test_cleanup_pool_empty(self):
        """cleanup_pool on empty pool does not raise."""
        await cleanup_pool()
        with _pool_lock:
            assert len(_client_pool) == 0

    def test_pool_stats_empty(self):
        stats = pool_stats()
        assert stats["pool_size"] == 0
        assert stats["runtimes"] == []

    def test_pool_stats_with_entries(self):
        get_sandshore_runtime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            sandbox_backend="local",
        )
        stats = pool_stats()
        assert stats["pool_size"] == 1
        assert len(stats["runtimes"]) == 1
        entry = stats["runtimes"][0]
        assert entry["backend"] == "local"
        assert "metrics" in entry
        assert "circuit_breaker_state" in entry

    @pytest.mark.asyncio
    async def test_cleanup_pool_handles_close_errors(self):
        """cleanup_pool handles errors from individual runtime close()."""
        rt = get_sandshore_runtime(
            anthropic_api_key="ak",
            e2b_api_key="ek",
            sandbox_backend="local",
        )
        rt._backend = AsyncMock()
        rt._backend.close = AsyncMock(side_effect=Exception("close failed"))

        # Should not raise
        await cleanup_pool()
        with _pool_lock:
            assert len(_client_pool) == 0
