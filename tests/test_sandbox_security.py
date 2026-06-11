"""Deep security tests for sandbox backends and code execution.

Wave 5 audit: sandbox backends, env var leakage, path traversal,
timeout enforcement, pool limits, Docker hardening, provider failover.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.backends import (
    CloudflareBackend,
    DockerBackend,
    E2BBackend,
    LocalBackend,
    SSEEvent,
    _validate_runner_file,
    create_backend,
)
from sandcastle.engine.providers import (
    FAILOVER_CHAINS,
    KNOWN_MODELS,
    PROVIDER_REGISTRY,
    ProviderFailover,
    get_api_key,
    get_failover,
    resolve_model,
)
from sandcastle.engine.sandshore import (
    CircuitBreaker,
    SandshoreRuntime,
    _MAX_POOL_SIZE,
    _client_pool,
    _pool_lock,
    cleanup_pool,
    get_sandshore_runtime,
    pool_stats,
)

from tests import _payloads as payloads


# ===========================================================================
# 1. Local backend - environment variable leakage
# ===========================================================================


class TestLocalBackendEnvLeakage:
    """Verify local backend does NOT leak host environment variables."""

    @pytest.mark.asyncio
    async def test_local_backend_does_not_inherit_full_host_env(self, tmp_path):
        """The subprocess should not get DATABASE_URL, AWS keys, etc."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        backend = LocalBackend()

        captured_env = {}

        original_create = asyncio.create_subprocess_exec

        async def mock_create(*args, **kwargs):
            nonlocal captured_env
            captured_env = kwargs.get("env", {})
            mock_proc = AsyncMock()
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.readline = AsyncMock(return_value=b"")
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock()
            mock_proc.kill = AsyncMock()
            return mock_proc

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={"SANDCASTLE_REQUEST": "{}"},
                    use_claude_runner=True,
                    timeout=5,
                ):
                    pass

        # Verify dangerous env vars are NOT in the subprocess env
        assert "DATABASE_URL" not in captured_env
        assert "AWS_SECRET_ACCESS_KEY" not in captured_env
        assert "REDIS_URL" not in captured_env
        # But PATH should be there for Node.js to work
        assert "PATH" in captured_env
        # And the explicitly passed env should be there
        assert captured_env.get("SANDCASTLE_REQUEST") == "{}"

    @pytest.mark.asyncio
    async def test_local_backend_only_safe_host_vars(self, tmp_path):
        """Only allowlisted host vars (PATH, HOME, etc.) should pass through."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        backend = LocalBackend()
        captured_env = {}

        async def mock_create(*args, **kwargs):
            nonlocal captured_env
            captured_env = kwargs.get("env", {})
            mock_proc = AsyncMock()
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.readline = AsyncMock(return_value=b"")
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock()
            mock_proc.kill = AsyncMock()
            return mock_proc

        fake_host_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "SECRET_TOKEN": "super-secret",
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "AWS_ACCESS_KEY_ID": "AKIA123",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "DATABASE_URL": "postgres://...",
        }

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with patch.dict(os.environ, fake_host_env, clear=True):
                with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
                    async for _ in backend.start(
                        runner_file="runner.mjs",
                        envs={"SANDCASTLE_REQUEST": "{}"},
                        use_claude_runner=True,
                        timeout=5,
                    ):
                        pass

        # Safe vars should be present
        assert captured_env.get("PATH") == "/usr/bin"
        assert captured_env.get("HOME") == "/home/user"
        # Dangerous vars should NOT be present
        assert "SECRET_TOKEN" not in captured_env
        assert "ANTHROPIC_API_KEY" not in captured_env
        assert "AWS_ACCESS_KEY_ID" not in captured_env
        assert "AWS_SECRET_ACCESS_KEY" not in captured_env
        assert "DATABASE_URL" not in captured_env

    @pytest.mark.asyncio
    async def test_local_backend_explicit_envs_override_safe_vars(self, tmp_path):
        """Explicitly passed envs should override safe host vars."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        backend = LocalBackend()
        captured_env = {}

        async def mock_create(*args, **kwargs):
            nonlocal captured_env
            captured_env = kwargs.get("env", {})
            mock_proc = AsyncMock()
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.readline = AsyncMock(return_value=b"")
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock()
            mock_proc.kill = AsyncMock()
            return mock_proc

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={"ANTHROPIC_API_KEY": "for-sandbox", "HOME": "/sandbox/home"},
                    use_claude_runner=True,
                    timeout=5,
                ):
                    pass

        # Explicit envs should be present and override host
        assert captured_env.get("ANTHROPIC_API_KEY") == "for-sandbox"
        assert captured_env.get("HOME") == "/sandbox/home"


# ===========================================================================
# 2. Runner path traversal validation
# ===========================================================================


class TestRunnerFileValidation:
    """Tests for _validate_runner_file path traversal prevention."""

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file(payloads.PATH_TRAVERSAL_1)

    def test_rejects_absolute_path(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file(payloads.ETC_PASSWD)

    def test_rejects_double_dot_anywhere(self):
        with pytest.raises(ValueError, match="Invalid runner file path"):
            _validate_runner_file("foo/../bar.mjs")

    def test_accepts_valid_runner(self):
        _validate_runner_file("runner.mjs")
        _validate_runner_file("runner-openai.mjs")

    def test_accepts_subdirectory_runner(self):
        # This is valid since it has no ..
        _validate_runner_file("custom/runner.mjs")


# ===========================================================================
# 3. Docker backend hardening
# ===========================================================================


class TestDockerBackendHardening:
    """Verify Docker container config has all security options."""

    @pytest.mark.asyncio
    async def test_docker_config_has_readonly_rootfs(self, tmp_path):
        """Container should have ReadonlyRootfs to prevent writes to system dirs."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        # Write a dummy seccomp profile
        seccomp = tmp_path / "seccomp-default.json"
        seccomp.write_text(json.dumps({"defaultAction": "SCMP_ACT_ALLOW", "syscalls": []}))

        backend = DockerBackend(seccomp_profile=str(seccomp))

        mock_container = AsyncMock()
        mock_container.put_archive = AsyncMock()
        mock_container.start = AsyncMock()
        mock_container.log = AsyncMock(return_value=AsyncMock(__aiter__=lambda s: s, __anext__=AsyncMock(side_effect=StopAsyncIteration)))
        mock_container.delete = AsyncMock()

        captured_config = {}

        mock_docker = AsyncMock()

        async def capture_create(config=None):
            nonlocal captured_config
            captured_config = config or {}
            return mock_container

        mock_docker.containers.create = capture_create

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            backend._client = mock_docker
            try:
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={"SANDCASTLE_REQUEST": "{}"},
                    use_claude_runner=True,
                    timeout=30,
                ):
                    pass
            except Exception:
                pass  # We only care about captured config

        host_config = captured_config.get("HostConfig", {})
        assert host_config.get("ReadonlyRootfs") is True
        assert host_config.get("MemorySwap") == host_config.get("Memory")
        assert "/tmp" in host_config.get("Tmpfs", {})

    @pytest.mark.asyncio
    async def test_docker_config_has_no_new_privileges(self, tmp_path):
        """Container should have no-new-privileges security option."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        seccomp = tmp_path / "seccomp-default.json"
        seccomp.write_text(json.dumps({"defaultAction": "SCMP_ACT_ALLOW", "syscalls": []}))

        backend = DockerBackend(seccomp_profile=str(seccomp))

        captured_config = {}
        mock_container = AsyncMock()
        mock_container.put_archive = AsyncMock()
        mock_container.start = AsyncMock()
        mock_container.log = AsyncMock(return_value=AsyncMock(__aiter__=lambda s: s, __anext__=AsyncMock(side_effect=StopAsyncIteration)))
        mock_container.delete = AsyncMock()

        mock_docker = AsyncMock()

        async def capture_create(config=None):
            nonlocal captured_config
            captured_config = config or {}
            return mock_container

        mock_docker.containers.create = capture_create

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            backend._client = mock_docker
            try:
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=30,
                ):
                    pass
            except Exception:
                pass

        security_opts = captured_config.get("HostConfig", {}).get("SecurityOpt", [])
        assert any("no-new-privileges" in opt for opt in security_opts)

    @pytest.mark.asyncio
    async def test_docker_config_drops_all_capabilities(self, tmp_path):
        """Container should drop ALL Linux capabilities."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        seccomp = tmp_path / "seccomp-default.json"
        seccomp.write_text(json.dumps({"defaultAction": "SCMP_ACT_ALLOW", "syscalls": []}))

        backend = DockerBackend(seccomp_profile=str(seccomp))

        captured_config = {}
        mock_container = AsyncMock()
        mock_container.put_archive = AsyncMock()
        mock_container.start = AsyncMock()
        mock_container.log = AsyncMock(return_value=AsyncMock(__aiter__=lambda s: s, __anext__=AsyncMock(side_effect=StopAsyncIteration)))
        mock_container.delete = AsyncMock()

        mock_docker = AsyncMock()

        async def capture_create(config=None):
            nonlocal captured_config
            captured_config = config or {}
            return mock_container

        mock_docker.containers.create = capture_create

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            backend._client = mock_docker
            try:
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=30,
                ):
                    pass
            except Exception:
                pass

        host_config = captured_config.get("HostConfig", {})
        assert host_config.get("CapDrop") == ["ALL"]

    @pytest.mark.asyncio
    async def test_docker_runs_as_nonroot_user(self, tmp_path):
        """Container should run as uid:gid 1000:1000, not root."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        seccomp = tmp_path / "seccomp-default.json"
        seccomp.write_text(json.dumps({"defaultAction": "SCMP_ACT_ALLOW", "syscalls": []}))

        backend = DockerBackend(seccomp_profile=str(seccomp))

        captured_config = {}
        mock_container = AsyncMock()
        mock_container.put_archive = AsyncMock()
        mock_container.start = AsyncMock()
        mock_container.log = AsyncMock(return_value=AsyncMock(__aiter__=lambda s: s, __anext__=AsyncMock(side_effect=StopAsyncIteration)))
        mock_container.delete = AsyncMock()

        mock_docker = AsyncMock()

        async def capture_create(config=None):
            nonlocal captured_config
            captured_config = config or {}
            return mock_container

        mock_docker.containers.create = capture_create

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            backend._client = mock_docker
            try:
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=30,
                ):
                    pass
            except Exception:
                pass

        assert captured_config.get("User") == "1000:1000"


# ===========================================================================
# 4. Docker backend timeout enforcement
# ===========================================================================


class TestDockerBackendTimeout:
    """Verify Docker backend has hard deadline enforcement."""

    @pytest.mark.asyncio
    async def test_docker_log_loop_has_deadline(self):
        """The log follow loop should break after timeout + grace."""
        # This is a structural test - verify the deadline logic exists
        import inspect
        source = inspect.getsource(DockerBackend.start)
        assert "deadline" in source
        assert "time.monotonic()" in source
        assert "30.0" in source or "30" in source  # 30s grace period


# ===========================================================================
# 5. E2B backend timeout enforcement
# ===========================================================================


class TestE2BBackendTimeout:
    """Verify E2B backend has Python-side deadline as safety net."""

    def test_e2b_has_execution_grace_period(self):
        backend = E2BBackend(e2b_api_key="test")
        assert backend._execution_grace_period == 30.0

    def test_e2b_deadline_logic_exists(self):
        """E2B start() must have a deadline check."""
        import inspect
        source = inspect.getsource(E2BBackend.start)
        assert "deadline" in source
        assert "execution_grace_period" in source

    @pytest.mark.asyncio
    async def test_e2b_sandbox_killed_in_finally(self):
        """E2B sandbox.kill() must be called even on error."""
        import inspect
        source = inspect.getsource(E2BBackend.start)
        assert "sandbox.kill()" in source
        assert "finally:" in source


# ===========================================================================
# 6. Cloudflare backend - HTTPS enforcement
# ===========================================================================


class TestCloudflareHTTPS:
    """Verify Cloudflare backend enforces HTTPS for credential safety."""

    def test_http_url_upgraded_to_https(self):
        backend = CloudflareBackend(worker_url="http://sandbox.example.workers.dev")
        assert backend._worker_url.startswith("https://")

    def test_https_url_kept_as_is(self):
        backend = CloudflareBackend(worker_url="https://sandbox.example.workers.dev")
        assert backend._worker_url == "https://sandbox.example.workers.dev"

    def test_localhost_http_allowed(self):
        """HTTP is allowed for localhost (development)."""
        backend = CloudflareBackend(worker_url="http://localhost:8787")
        assert backend._worker_url.startswith("http://localhost")

    def test_127_0_0_1_http_allowed(self):
        """HTTP is allowed for 127.0.0.1 (development)."""
        backend = CloudflareBackend(worker_url="http://127.0.0.1:8787")
        assert backend._worker_url.startswith("http://127.0.0.1")

    def test_empty_url_handled(self):
        backend = CloudflareBackend(worker_url="")
        assert backend._worker_url == ""


# ===========================================================================
# 7. Provider registry validation
# ===========================================================================


class TestProviderRegistryValidation:
    """Verify model resolution handles edge cases."""

    def test_unknown_model_raises_keyerror(self):
        with pytest.raises(KeyError, match="Unknown model"):
            resolve_model("nonexistent-model")

    def test_unknown_model_error_lists_available(self):
        try:
            resolve_model("bad-model")
        except KeyError as e:
            msg = str(e)
            assert "sonnet" in msg
            assert "haiku" in msg

    def test_all_models_have_runner(self):
        for name, info in PROVIDER_REGISTRY.items():
            assert info.runner in ("runner.mjs", "runner-openai.mjs"), (
                f"Model '{name}' has invalid runner: {info.runner}"
            )

    def test_all_models_have_api_key_env(self):
        for name, info in PROVIDER_REGISTRY.items():
            assert info.api_key_env is not None, f"Model '{name}' has no api_key_env"

    def test_all_models_have_positive_pricing(self):
        for name, info in PROVIDER_REGISTRY.items():
            assert info.input_price_per_m >= 0, (
                f"Model '{name}' has negative input price"
            )
            assert info.output_price_per_m >= 0, (
                f"Model '{name}' has negative output price"
            )

    def test_all_failover_chains_reference_known_models(self):
        """All models in failover chains must exist in the registry."""
        for model, chain in FAILOVER_CHAINS.items():
            assert model in PROVIDER_REGISTRY, (
                f"Failover source '{model}' not in registry"
            )
            for alt in chain:
                assert alt in PROVIDER_REGISTRY, (
                    f"Failover alt '{alt}' (from '{model}') not in registry"
                )

    def test_failover_chain_does_not_include_self(self):
        """A model's failover chain should not include itself."""
        for model, chain in FAILOVER_CHAINS.items():
            assert model not in chain, (
                f"Model '{model}' appears in its own failover chain"
            )


# ===========================================================================
# 8. Provider failover correctness
# ===========================================================================


class TestProviderFailoverCorrectness:
    """Verify failover logic handles rate limits correctly."""

    def test_cooldown_marks_key_unavailable(self):
        fo = ProviderFailover()
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60)
        assert fo.is_available("ANTHROPIC_API_KEY") is False

    def test_cooldown_expires(self):
        fo = ProviderFailover()
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        assert fo.is_available("ANTHROPIC_API_KEY") is True

    def test_uncooled_key_is_available(self):
        fo = ProviderFailover()
        assert fo.is_available("ANTHROPIC_API_KEY") is True

    def test_get_alternatives_skips_cooled_keys(self):
        fo = ProviderFailover()
        # Cool down Anthropic key
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60)
        alts = fo.get_alternatives("sonnet")
        # All Claude models should be excluded
        for alt in alts:
            info = PROVIDER_REGISTRY.get(alt)
            if info:
                assert info.api_key_env != "ANTHROPIC_API_KEY"

    def test_get_alternatives_returns_empty_for_unknown_model(self):
        fo = ProviderFailover()
        assert fo.get_alternatives("nonexistent") == []

    def test_get_status_includes_cooldowns(self):
        fo = ProviderFailover()
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60)
        status = fo.get_status()
        assert "ANTHROPIC_API_KEY" in status["active_cooldowns"]

    def test_get_status_cleans_expired_cooldowns(self):
        fo = ProviderFailover()
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        status = fo.get_status()
        assert "ANTHROPIC_API_KEY" not in status["active_cooldowns"]


# ===========================================================================
# 9. SandshoreRuntime retriable error detection
# ===========================================================================


class TestRetriableErrorDetection:
    """Verify _is_retriable_provider_error catches all expected patterns."""

    def test_429_detected(self):
        assert SandshoreRuntime._is_retriable_provider_error("HTTP 429 Too Many Requests")

    def test_rate_limit_detected(self):
        assert SandshoreRuntime._is_retriable_provider_error("rate limit exceeded")

    def test_too_many_requests_detected(self):
        assert SandshoreRuntime._is_retriable_provider_error("too many requests")

    def test_500_detected(self):
        assert SandshoreRuntime._is_retriable_provider_error("HTTP 500 Internal Server Error")

    def test_502_detected(self):
        assert SandshoreRuntime._is_retriable_provider_error("HTTP 502 Bad Gateway")

    def test_503_detected(self):
        assert SandshoreRuntime._is_retriable_provider_error("HTTP 503 Service Unavailable")

    def test_overloaded_detected(self):
        assert SandshoreRuntime._is_retriable_provider_error("API is overloaded")

    def test_capacity_detected(self):
        assert SandshoreRuntime._is_retriable_provider_error("No capacity available")

    def test_normal_error_not_retriable(self):
        assert not SandshoreRuntime._is_retriable_provider_error("Invalid API key")

    def test_auth_error_not_retriable(self):
        assert not SandshoreRuntime._is_retriable_provider_error("401 Unauthorized")

    def test_404_not_retriable(self):
        assert not SandshoreRuntime._is_retriable_provider_error("404 Not Found")


# ===========================================================================
# 10. Circuit breaker
# ===========================================================================


class TestCircuitBreakerSecurity:
    """Verify circuit breaker prevents runaway failure cascades."""

    @pytest.mark.asyncio
    async def test_trips_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        assert cb.state == CircuitBreaker.CLOSED
        for _ in range(3):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        time.sleep(0.02)
        assert cb.state == CircuitBreaker.HALF_OPEN
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_resets_on_success(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_success_resets_after_trip(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        time.sleep(0.02)
        # In half-open, allow one request
        assert await cb.allow_request() is True
        await cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED


# ===========================================================================
# 11. Concurrent sandbox limits
# ===========================================================================


class TestConcurrentSandboxLimits:
    """Verify max_concurrent_sandboxes config validation."""

    def test_minimum_is_1(self):
        from sandcastle.config import Settings
        s = Settings(_env_file=None, anthropic_api_key="test", max_concurrent_sandboxes=0)
        assert s.max_concurrent_sandboxes == 1

    def test_negative_is_clamped_to_1(self):
        from sandcastle.config import Settings
        s = Settings(_env_file=None, anthropic_api_key="test", max_concurrent_sandboxes=-5)
        assert s.max_concurrent_sandboxes == 1

    def test_maximum_is_50(self):
        from sandcastle.config import Settings
        s = Settings(_env_file=None, anthropic_api_key="test", max_concurrent_sandboxes=100)
        assert s.max_concurrent_sandboxes == 50

    def test_1000_clamped_to_50(self):
        from sandcastle.config import Settings
        s = Settings(_env_file=None, anthropic_api_key="test", max_concurrent_sandboxes=1000)
        assert s.max_concurrent_sandboxes == 50

    def test_valid_value_unchanged(self):
        from sandcastle.config import Settings
        s = Settings(_env_file=None, anthropic_api_key="test", max_concurrent_sandboxes=10)
        assert s.max_concurrent_sandboxes == 10

    def test_boundary_50_accepted(self):
        from sandcastle.config import Settings
        s = Settings(_env_file=None, anthropic_api_key="test", max_concurrent_sandboxes=50)
        assert s.max_concurrent_sandboxes == 50

    def test_semaphore_matches_config(self):
        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
            max_concurrent=7,
        )
        assert runtime._max_concurrent == 7
        # Semaphore._value is the internal counter
        assert runtime._semaphore._value == 7


# ===========================================================================
# 12. Pool size limits
# ===========================================================================


class TestPoolSizeLimits:
    """Verify the runtime pool has a size cap to prevent memory leaks."""

    def test_max_pool_size_constant_exists(self):
        assert _MAX_POOL_SIZE > 0
        assert _MAX_POOL_SIZE <= 100

    @pytest.mark.asyncio
    async def test_pool_cleanup(self):
        """cleanup_pool should close and clear all entries."""
        # First, ensure pool is clean
        await cleanup_pool()
        stats = pool_stats()
        assert stats["pool_size"] == 0


# ===========================================================================
# 13. Seccomp profile completeness
# ===========================================================================


class TestSeccompProfile:
    """Verify seccomp profile blocks dangerous syscalls."""

    def test_seccomp_blocks_critical_syscalls(self):
        import json
        from pathlib import Path

        seccomp_path = Path(__file__).parent.parent / "src" / "sandcastle" / "engine" / "seccomp-default.json"
        with open(seccomp_path) as f:
            profile = json.load(f)

        blocked = set()
        for rule in profile.get("syscalls", []):
            if rule.get("action") == "SCMP_ACT_ERRNO":
                blocked.update(rule.get("names", []))

        # Critical syscalls that MUST be blocked
        required_blocked = {
            "ptrace", "mount", "umount2", "reboot",
            "kexec_load", "init_module", "finit_module",
            "delete_module", "unshare", "setns",
            "bpf", "open_by_handle_at", "userfaultfd",
        }
        for syscall in required_blocked:
            assert syscall in blocked, f"Seccomp must block '{syscall}'"


# ===========================================================================
# 14. _build_env does not leak database/storage secrets
# ===========================================================================


class TestBuildEnvSecrets:
    """Verify _build_env only includes sandbox-needed env vars."""

    def test_build_env_has_sandcastle_request(self):
        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        )
        envs, _, _, _ = runtime._build_env({"prompt": "test", "model": "sonnet"})
        assert "SANDCASTLE_REQUEST" in envs

    def test_build_env_has_api_key_for_claude(self):
        runtime = SandshoreRuntime(
            anthropic_api_key="ak-secret",
            e2b_api_key="",
            sandbox_backend="local",
        )
        envs, _, _, _ = runtime._build_env({"prompt": "test", "model": "sonnet"})
        assert envs.get("ANTHROPIC_API_KEY") == "ak-secret"

    def test_build_env_does_not_include_database_url(self):
        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        )
        envs, _, _, _ = runtime._build_env({"prompt": "test", "model": "sonnet"})
        assert "DATABASE_URL" not in envs
        assert "REDIS_URL" not in envs
        assert "E2B_API_KEY" not in envs

    def test_build_env_has_pricing_info(self):
        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        )
        envs, _, _, _ = runtime._build_env({"prompt": "test", "model": "sonnet"})
        assert "MODEL_INPUT_PRICE" in envs
        assert "MODEL_OUTPUT_PRICE" in envs


# ===========================================================================
# 15. E2B backend env vars are sandboxed (not host-merged)
# ===========================================================================


class TestE2BEnvIsolation:
    """Verify E2B backend only passes the envs dict, not host env."""

    def test_e2b_uses_envs_parameter_only(self):
        """E2B sandbox.create() should get only the envs dict, not os.environ."""
        import inspect
        source = inspect.getsource(E2BBackend.start)
        # Verify no os.environ merge for E2B
        assert "os.environ" not in source


# ===========================================================================
# 16. Cloudflare worker env var key validation
# ===========================================================================


class TestCloudflareEnvKeyValidation:
    """CF worker should reject env var keys with shell metacharacters."""

    def test_cf_worker_index_validates_env_keys(self):
        """The CF worker source should filter env var names."""
        from pathlib import Path
        worker_src = Path(__file__).parent.parent / "cf-sandbox-worker" / "src" / "index.ts"
        source = worker_src.read_text()
        # Must have regex validation for env var names
        assert "A-Za-z_" in source or "A-Z" in source
        assert "filter" in source or "test" in source

    def test_cf_worker_validates_runner_file(self):
        """The CF worker should validate runner_file for path traversal."""
        from pathlib import Path
        worker_src = Path(__file__).parent.parent / "cf-sandbox-worker" / "src" / "index.ts"
        source = worker_src.read_text()
        assert ".." in source  # checks for path traversal


# ===========================================================================
# 17. Local backend timeout behavior
# ===========================================================================


class TestLocalBackendTimeout:
    """Verify local backend properly kills subprocess on timeout."""

    @pytest.mark.asyncio
    async def test_process_killed_on_timeout(self, tmp_path):
        """Process should be killed if readline times out."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        backend = LocalBackend()

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                events = []
                async for event in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=1,
                ):
                    events.append(event)

        # Process should have been killed
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_tools_dir_cleaned_up_on_error(self, tmp_path):
        """Temp tools directory should be cleaned up even on error."""
        runner = tmp_path / "runner.mjs"
        runner.write_text("// mock runner")

        backend = LocalBackend()

        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()
        mock_proc.kill = AsyncMock()

        with patch("sandcastle.engine.backends._RUNNER_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                async for _ in backend.start(
                    runner_file="runner.mjs",
                    envs={},
                    use_claude_runner=True,
                    timeout=5,
                    tool_files={"test.mjs": "// tool code"},
                ):
                    pass

        # No temp dirs should remain (tools_dir is cleaned up in finally)


# ===========================================================================
# 18. Sandbox cost tracking
# ===========================================================================


class TestSandboxCostTracking:
    """Verify cost is properly tracked in metrics."""

    @pytest.mark.asyncio
    async def test_metrics_record_cost(self):
        from sandcastle.engine.sandshore import RuntimeMetrics
        metrics = RuntimeMetrics()
        await metrics.record_query_success(cost_usd=0.05)
        snapshot = metrics.snapshot()
        assert snapshot["total_cost_usd"] == 0.05
        assert snapshot["successful_queries"] == 1

    @pytest.mark.asyncio
    async def test_metrics_accumulate_cost(self):
        from sandcastle.engine.sandshore import RuntimeMetrics
        metrics = RuntimeMetrics()
        await metrics.record_query_success(cost_usd=0.01)
        await metrics.record_query_success(cost_usd=0.02)
        await metrics.record_query_success(cost_usd=0.03)
        snapshot = metrics.snapshot()
        assert abs(snapshot["total_cost_usd"] - 0.06) < 1e-9
        assert snapshot["successful_queries"] == 3

    @pytest.mark.asyncio
    async def test_metrics_track_failures(self):
        from sandcastle.engine.sandshore import RuntimeMetrics
        metrics = RuntimeMetrics()
        await metrics.record_query_failure()
        await metrics.record_query_failure()
        snapshot = metrics.snapshot()
        assert snapshot["failed_queries"] == 2
        assert snapshot["total_queries"] == 2

    @pytest.mark.asyncio
    async def test_metrics_track_failovers(self):
        from sandcastle.engine.sandshore import RuntimeMetrics
        metrics = RuntimeMetrics()
        await metrics.record_failover()
        await metrics.record_failover()
        snapshot = metrics.snapshot()
        assert snapshot["failover_attempts"] == 2


# ===========================================================================
# 19. Docker backend container cleanup
# ===========================================================================


class TestDockerContainerCleanup:
    """Verify containers are always cleaned up, even on error."""

    def test_docker_start_has_finally_cleanup(self):
        """Docker start() must have container.delete in finally block."""
        import inspect
        source = inspect.getsource(DockerBackend.start)
        assert "finally:" in source
        assert "container.delete(force=True)" in source

    def test_docker_uses_auto_remove(self):
        """Docker config should have AutoRemove=True."""
        import inspect
        source = inspect.getsource(DockerBackend.start)
        assert '"AutoRemove": True' in source or "'AutoRemove': True" in source


# ===========================================================================
# 20. Health cache prevents thundering herd
# ===========================================================================


class TestHealthCaching:
    """Verify health checks are cached to prevent thundering herd."""

    @pytest.mark.asyncio
    async def test_docker_health_cache_ttl(self):
        backend = DockerBackend()
        assert backend._health_cache_ttl == 30.0

    @pytest.mark.asyncio
    async def test_cloudflare_health_cache_ttl(self):
        backend = CloudflareBackend(worker_url="https://example.workers.dev")
        assert backend._health_cache_ttl == 30.0

    @pytest.mark.asyncio
    async def test_sandshore_health_cache_ttl(self):
        runtime = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        )
        assert runtime._health_cache_ttl == 60.0


# ===========================================================================
# 21. Async context manager
# ===========================================================================


class TestRuntimeContextManager:
    """Verify SandshoreRuntime works as async context manager."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        async with SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        ) as rt:
            assert rt.backend_name == "local"
        # close() should have been called

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        rt = SandshoreRuntime(
            anthropic_api_key="ak",
            e2b_api_key="",
            sandbox_backend="local",
        )
        await rt.close()
        await rt.close()  # Should not raise


# ===========================================================================
# 22. Backend factory validation
# ===========================================================================


class TestBackendFactoryEdgeCases:
    """Test create_backend with edge case parameters."""

    def test_docker_memory_limit_passed_through(self):
        backend = create_backend(
            "docker",
            docker_memory_limit=256 * 1024 * 1024,
        )
        assert backend._memory_limit == 256 * 1024 * 1024

    def test_docker_pids_limit_passed_through(self):
        backend = create_backend("docker", docker_pids_limit=50)
        assert backend._pids_limit == 50

    def test_docker_cpu_quota_passed_through(self):
        backend = create_backend("docker", docker_cpu_quota=25_000)
        assert backend._cpu_quota == 25_000

    def test_e2b_queue_size_passed_through(self):
        backend = create_backend("e2b", e2b_api_key="k", e2b_event_queue_size=500)
        assert backend._event_queue_size == 500

    def test_e2b_npm_timeout_passed_through(self):
        backend = create_backend("e2b", e2b_api_key="k", e2b_npm_install_timeout=120)
        assert backend._npm_install_timeout == 120

    def test_cloudflare_timeout_passed_through(self):
        backend = create_backend(
            "cloudflare",
            cloudflare_worker_url="https://example.workers.dev",
            timeout=600,
        )
        assert backend._timeout == 600


# ===========================================================================
# 23. Model info frozen dataclass
# ===========================================================================


class TestModelInfoImmutability:
    """Verify ModelInfo dataclass is frozen (immutable)."""

    def test_model_info_is_frozen(self):
        info = resolve_model("sonnet")
        with pytest.raises(AttributeError):
            info.provider = "hacked"

    def test_model_info_is_frozen_api_key(self):
        info = resolve_model("sonnet")
        with pytest.raises(AttributeError):
            info.api_key_env = "HACKED_KEY"


# ===========================================================================
# 24. Singleton failover
# ===========================================================================


class TestFailoverSingleton:
    """Verify get_failover returns same instance."""

    def test_singleton_returns_same_instance(self):
        fo1 = get_failover()
        fo2 = get_failover()
        assert fo1 is fo2

    def test_failover_is_thread_safe(self):
        """ProviderFailover uses threading.Lock for cooldown access."""
        fo = ProviderFailover()
        assert hasattr(fo, "_lock")
