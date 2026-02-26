"""Pluggable sandbox backends for Sandcastle.

Each backend implements the ``SandboxBackend`` protocol and can execute
agent runner scripts in an isolated (or semi-isolated) environment.
The ``create_backend()`` factory selects the right implementation based
on the ``SANDBOX_BACKEND`` setting.

Supported backends:
- **e2b** (default) - Cloud sandboxes via E2B SDK
- **docker** - Local Docker containers via aiodocker
- **local** - Direct subprocess on the host (no isolation, dev only)
- **cloudflare** - Edge sandboxes via Cloudflare Workers
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Bundled runner scripts
_RUNNER_DIR = Path(__file__).parent


@dataclass
class SSEEvent:
    """A single SSE event from the execution stream."""

    event: str  # "system", "assistant", "user", "result", "error"
    data: dict


def _validate_runner_file(runner_file: str) -> None:
    """Validate runner_file path to prevent path traversal attacks.

    Raises ``ValueError`` if the path contains directory traversal
    sequences or is an absolute path.
    """
    if ".." in runner_file or runner_file.startswith("/"):
        raise ValueError(f"Invalid runner file path: {runner_file}")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SandboxBackend(Protocol):
    """Interface that every sandbox backend must implement."""

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
        tool_files: dict[str, str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Execute *runner_file* inside the sandbox and stream events."""
        ...  # pragma: no cover

    async def health(self) -> bool:
        """Return True when the backend is available and ready."""
        ...  # pragma: no cover

    async def close(self) -> None:
        """Release any resources held by the backend."""
        ...  # pragma: no cover

    @property
    def name(self) -> str:
        """Short identifier for this backend (e.g. ``"e2b"``)."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# E2B Backend
# ---------------------------------------------------------------------------


class E2BBackend:
    """Cloud sandboxes via the E2B Python SDK."""

    def __init__(
        self,
        e2b_api_key: str,
        template: str = "",
        timeout: float = 300.0,
        event_queue_size: int = 1000,
        npm_install_timeout: int = 60,
        npm_poll_deadline: float = 90.0,
        execution_grace_period: float = 30.0,
    ) -> None:
        self._api_key = e2b_api_key
        self._template = template
        self._timeout = timeout
        self._event_queue_size = event_queue_size
        self._npm_install_timeout = npm_install_timeout
        self._npm_poll_deadline = npm_poll_deadline
        self._execution_grace_period = execution_grace_period

    @property
    def name(self) -> str:
        return "e2b"

    async def health(self) -> bool:
        """Check if the E2B SDK is importable and an API key is set.

        This is a cheap in-process check - no network call needed.
        """
        if not self._api_key:
            return False
        try:
            import e2b  # noqa: F401
            return True
        except ImportError:
            return False

    async def close(self) -> None:
        """No-op - E2B sandboxes are ephemeral and cleaned up after each run."""

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
        tool_files: dict[str, str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        _validate_runner_file(runner_file)

        from e2b import AsyncSandbox

        queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue(
            maxsize=self._event_queue_size
        )
        sandbox = None

        def on_stdout(data: Any) -> None:
            line = data.line if hasattr(data, "line") else str(data)
            try:
                parsed = json.loads(line)
                event = SSEEvent(
                    event=parsed.get("type", "message"),
                    data=parsed,
                )
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full, dropping event")
            except (json.JSONDecodeError, ValueError):
                logger.debug("Non-JSON stdout: %s", line[:200])

        def on_stderr(data: Any) -> None:
            line = data.line if hasattr(data, "line") else str(data)
            logger.debug("Sandbox stderr: %s", line[:500])

        try:
            sandbox_kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "timeout": int(timeout),
                "envs": envs,
            }
            if self._template:
                sandbox_kwargs["template"] = self._template

            sandbox = await AsyncSandbox.create(**sandbox_kwargs)

            if not self._template:
                runner_path = _RUNNER_DIR / runner_file
                runner_code = runner_path.read_text() if runner_path.exists() else ""
                if not runner_code:
                    raise RuntimeError(f"Runner script not found: {runner_file}")

                pkg = (
                    "@anthropic-ai/claude-agent-sdk"
                    if use_claude_runner
                    else "openai"
                )

                # Upload runner file and start npm install in parallel.
                # npm install runs in background mode to avoid E2B SDK
                # gRPC hang - the SDK's foreground commands.run() can block
                # indefinitely when the internal event stream stalls.
                upload_coro = sandbox.files.write(
                    f"/home/user/{runner_file}", runner_code
                )
                npm_coro = sandbox.commands.run(
                    f"npm install {pkg} 2>/dev/null || true",
                    background=True,
                    timeout=self._npm_install_timeout,
                )
                _, npm_handle = await asyncio.gather(upload_coro, npm_coro)

                # Upload tool connector files into /home/user/tools/
                if tool_files:
                    await sandbox.commands.run("mkdir -p /home/user/tools", timeout=5)
                    tool_uploads = [
                        sandbox.files.write(f"/home/user/tools/{fname}", content)
                        for fname, content in tool_files.items()
                    ]
                    if tool_uploads:
                        await asyncio.gather(*tool_uploads)
                        logger.info(
                            "Uploaded %d tool files to sandbox",
                            len(tool_uploads),
                        )

                # Wait for npm to finish using event-based polling with a
                # hard deadline instead of a naive sleep loop.
                try:
                    start_ts = time.monotonic()
                    while npm_handle.exit_code is None:
                        elapsed = time.monotonic() - start_ts
                        if elapsed > self._npm_poll_deadline:
                            logger.warning(
                                "npm install timed out after %.0fs, "
                                "proceeding anyway",
                                self._npm_poll_deadline,
                            )
                            break
                        # Progressively back off: 0.5s, 1s, 2s, capped at 3s
                        wait_time = min(0.5 * (2 ** min(int(elapsed / 5), 3)), 3.0)
                        await asyncio.sleep(wait_time)

                    if npm_handle.exit_code is not None and npm_handle.exit_code != 0:
                        logger.warning(
                            "npm install exited with code %d",
                            npm_handle.exit_code,
                        )
                except Exception as exc:
                    logger.warning("npm install error: %s", exc)

            handle = await sandbox.commands.run(
                f"node /home/user/{runner_file}",
                background=True,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
                cwd="/home/user",
                timeout=int(timeout),
            )

            # Python-side deadline as safety net - E2B SDK timeout
            # on background commands may not reliably kill the process.
            deadline = time.monotonic() + timeout + self._execution_grace_period
            while True:
                if time.monotonic() > deadline:
                    logger.warning(
                        "E2B execution exceeded deadline (%.0fs + %.0fs grace), "
                        "stopping",
                        timeout,
                        self._execution_grace_period,
                    )
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2.0)
                    if event is None:
                        break
                    yield event
                except asyncio.TimeoutError:
                    if handle.exit_code is not None:
                        break
                    continue

            # Drain remaining events
            while not queue.empty():
                event = queue.get_nowait()
                if event is not None:
                    yield event

            # Don't call handle.wait() - it can hang due to E2B SDK
            # gRPC internals. We already consumed all events above and
            # checked exit_code, so the process is done.

        finally:
            if sandbox:
                try:
                    await sandbox.kill()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Docker Backend
# ---------------------------------------------------------------------------


class DockerBackend:
    """Local Docker containers via aiodocker.

    Maintains a pooled Docker client to avoid reconnecting on every
    ``health()`` or ``start()`` call.
    """

    def __init__(
        self,
        docker_image: str = "sandcastle-runner:latest",
        docker_url: str | None = None,
        timeout: float = 300.0,
        memory_limit: int = 512 * 1024 * 1024,
        seccomp_profile: str = "",
        pids_limit: int = 100,
        cpu_period: int = 100_000,
        cpu_quota: int = 50_000,
    ) -> None:
        self._image = docker_image
        self._url = docker_url
        self._timeout = timeout
        self._memory_limit = memory_limit
        self._seccomp_profile = seccomp_profile
        self._pids_limit = pids_limit
        self._cpu_period = cpu_period
        self._cpu_quota = cpu_quota
        self._client: Any = None  # aiodocker.Docker | None
        # Health cache: (result, timestamp)
        self._health_cache: tuple[bool, float] = (False, 0.0)
        self._health_cache_ttl: float = 30.0

    @property
    def name(self) -> str:
        return "docker"

    def _get_client(self) -> Any:
        """Return the pooled aiodocker client, creating it on first use.

        Raises ``RuntimeError`` if aiodocker is not installed.
        """
        if self._client is not None:
            return self._client

        try:
            import aiodocker
        except ImportError:
            raise RuntimeError(
                "aiodocker not installed. "
                "Install with: pip install sandcastle-ai[docker]"
            )

        kwargs: dict[str, Any] = {}
        if self._url:
            kwargs["url"] = self._url
        self._client = aiodocker.Docker(**kwargs)
        return self._client

    async def health(self) -> bool:
        """Check Docker daemon connectivity (cached for 30s)."""
        cached_result, cached_at = self._health_cache
        if time.monotonic() - cached_at < self._health_cache_ttl:
            return cached_result

        result = await self._health_uncached()
        self._health_cache = (result, time.monotonic())
        return result

    async def _health_uncached(self) -> bool:
        """Perform the actual Docker health check."""
        try:
            docker = self._get_client()
        except RuntimeError:
            logger.error(
                "aiodocker not installed. "
                "Install with: pip install sandcastle-ai[docker]"
            )
            return False

        try:
            await docker.version()
            return True
        except Exception as exc:
            logger.warning("Docker health check failed: %s", exc)
            # Reset client on connection failure so next call retries
            self._client = None
            return False

    async def close(self) -> None:
        """Close the pooled Docker client and release the connection."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                logger.debug("Error closing Docker client: %s", exc)
            finally:
                self._client = None

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
        tool_files: dict[str, str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        _validate_runner_file(runner_file)

        import io
        import tarfile

        docker = self._get_client()
        container = None

        try:
            # Read runner script
            runner_path = _RUNNER_DIR / runner_file
            runner_code = runner_path.read_text() if runner_path.exists() else ""
            if not runner_code:
                raise RuntimeError(f"Runner script not found: {runner_file}")

            # Create container with AutoRemove to avoid orphaned containers.
            # Note: AutoRemove requires the container to be started, and
            # prevents manual deletion via delete(force=True). We use it
            # only when we plan to let the container exit naturally.
            # Resolve seccomp profile
            seccomp_path = self._seccomp_profile or str(
                _RUNNER_DIR / "seccomp-default.json"
            )
            security_opt = []
            try:
                with open(seccomp_path) as f:
                    import json as _json

                    seccomp_data = _json.dumps(_json.load(f))
                    security_opt.append(f"seccomp={seccomp_data}")
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("Seccomp profile not loaded: %s", exc)

            config = {
                "Image": self._image,
                "Cmd": ["node", f"/home/user/{runner_file}"],
                "Env": [f"{k}={v}" for k, v in envs.items()],
                "WorkingDir": "/home/user",
                "User": "1000:1000",
                "NetworkMode": "bridge",
                "HostConfig": {
                    "AutoRemove": True,
                    "Memory": self._memory_limit,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": security_opt,
                    "PidsLimit": self._pids_limit,
                    "CpuPeriod": self._cpu_period,
                    "CpuQuota": self._cpu_quota,
                },
            }

            container = await docker.containers.create_or_run(config=config)

            # Upload runner script + tool connector files via tar archive
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                data = runner_code.encode()
                info = tarfile.TarInfo(name=runner_file)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                if tool_files:
                    for fname, content in tool_files.items():
                        fdata = content.encode()
                        finfo = tarfile.TarInfo(name=f"tools/{fname}")
                        finfo.size = len(fdata)
                        tar.addfile(finfo, io.BytesIO(fdata))
            tar_stream.seek(0)
            await container.put_archive("/home/user", tar_stream.read())

            # Start and collect output
            await container.start()
            logs = await container.log(
                stdout=True, stderr=True, follow=True
            )

            async for line in logs:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    yield SSEEvent(
                        event=parsed.get("type", "message"),
                        data=parsed,
                    )
                except (json.JSONDecodeError, ValueError):
                    logger.debug("Non-JSON docker output: %s", line[:200])

        finally:
            if container:
                try:
                    # With AutoRemove=True the container is cleaned up by
                    # Docker after it exits. Force-delete handles the case
                    # where the container is still running (e.g. timeout).
                    await container.delete(force=True)
                except Exception as exc:
                    # Expected when AutoRemove already cleaned up the
                    # container, or when the container was never started.
                    logger.debug(
                        "Container cleanup (expected with AutoRemove): %s", exc
                    )


# ---------------------------------------------------------------------------
# Local Backend (subprocess, no isolation)
# ---------------------------------------------------------------------------


class LocalBackend:
    """Direct subprocess execution on the host - for development only."""

    def __init__(self, timeout: float = 300.0) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "local"

    async def health(self) -> bool:
        """Check if Node.js is available on the PATH.

        This is a cheap in-process check - no caching needed.
        """
        return shutil.which("node") is not None

    async def close(self) -> None:
        """No-op - local backend holds no persistent resources."""

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
        tool_files: dict[str, str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        _validate_runner_file(runner_file)

        import os
        import tempfile

        runner_path = _RUNNER_DIR / runner_file
        if not runner_path.exists():
            raise RuntimeError(f"Runner script not found: {runner_path}")

        # Write tool connector files to a temp tools/ directory
        tools_dir = None
        if tool_files:
            tools_dir = Path(tempfile.mkdtemp(prefix="sandcastle-tools-"))
            for fname, content in tool_files.items():
                (tools_dir / fname).write_text(content)

        # Merge host env with provided envs
        proc_env = {**os.environ, **envs}

        proc = await asyncio.create_subprocess_exec(
            "node",
            str(runner_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
            cwd=str(tools_dir) if tools_dir else str(_RUNNER_DIR),
        )

        try:
            assert proc.stdout is not None
            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning("Local backend timed out after %.0fs", timeout)
                    break

                if not line_bytes:
                    break

                line = line_bytes.decode().strip()
                if not line:
                    continue

                try:
                    parsed = json.loads(line)
                    yield SSEEvent(
                        event=parsed.get("type", "message"),
                        data=parsed,
                    )
                except (json.JSONDecodeError, ValueError):
                    logger.debug("Non-JSON local output: %s", line[:200])

            await proc.wait()

        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass


# ---------------------------------------------------------------------------
# Cloudflare Backend
# ---------------------------------------------------------------------------


class CloudflareBackend:
    """Edge sandboxes via a deployed Cloudflare Worker.

    Maintains a pooled httpx client to avoid reconnecting on every
    ``health()`` or ``start()`` call.
    """

    def __init__(
        self,
        worker_url: str,
        timeout: float = 300.0,
    ) -> None:
        self._worker_url = worker_url.rstrip("/") if worker_url else ""
        self._timeout = timeout
        self._client: Any = None  # httpx.AsyncClient | None
        # Health cache: (result, timestamp)
        self._health_cache: tuple[bool, float] = (False, 0.0)
        self._health_cache_ttl: float = 30.0

    @property
    def name(self) -> str:
        return "cloudflare"

    def _get_client(self) -> Any:
        """Return the pooled httpx client, creating it on first use."""
        if self._client is not None:
            return self._client

        import httpx

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout)
        )
        return self._client

    async def health(self) -> bool:
        """Check Cloudflare Worker health endpoint (cached for 30s)."""
        if not self._worker_url:
            return False

        cached_result, cached_at = self._health_cache
        if time.monotonic() - cached_at < self._health_cache_ttl:
            return cached_result

        result = await self._health_uncached()
        self._health_cache = (result, time.monotonic())
        return result

    async def _health_uncached(self) -> bool:
        """Perform the actual Cloudflare Worker health check."""
        try:
            client = self._get_client()
            resp = await client.get(f"{self._worker_url}/health")
            data = resp.json()
            return data.get("ok", False)
        except Exception as exc:
            logger.warning("Cloudflare health check failed: %s", exc)
            return False

    async def close(self) -> None:
        """Close the pooled httpx client and release connections."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.debug("Error closing Cloudflare httpx client: %s", exc)
            finally:
                self._client = None

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
        tool_files: dict[str, str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        _validate_runner_file(runner_file)

        if not self._worker_url:
            raise RuntimeError(
                "CLOUDFLARE_WORKER_URL is required for the cloudflare backend"
            )

        # Read runner script to send to CF Worker
        runner_path = _RUNNER_DIR / runner_file
        runner_code = runner_path.read_text() if runner_path.exists() else ""
        if not runner_code:
            raise RuntimeError(f"Runner script not found: {runner_file}")

        payload: dict[str, Any] = {
            "runner_file": runner_file,
            "runner_content": runner_code,
            "envs": envs,
        }
        if tool_files:
            payload["tool_files"] = tool_files

        import httpx

        # Use a dedicated client with the per-request timeout for the /run
        # call, since the pooled client uses the default backend timeout
        # which may differ from the step-level timeout.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout)
        ) as run_client:
            resp = await run_client.post(
                f"{self._worker_url}/run",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # CF Sandbox returns batch response (stdout as a whole)
        stdout = data.get("stdout", "")
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                yield SSEEvent(
                    event=parsed.get("type", "message"),
                    data=parsed,
                )
            except (json.JSONDecodeError, ValueError):
                logger.debug("Non-JSON CF output: %s", line[:200])

        # Check for execution errors
        if data.get("exitCode", 0) != 0:
            stderr = data.get("stderr", "")
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": f"CF sandbox failed: {stderr}"},
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_VALID_BACKENDS = frozenset({"e2b", "docker", "local", "cloudflare"})


def create_backend(
    backend_type: str,
    *,
    e2b_api_key: str = "",
    template: str = "",
    docker_image: str = "sandcastle-runner:latest",
    docker_url: str | None = None,
    docker_memory_limit: int = 512 * 1024 * 1024,
    docker_seccomp_profile: str = "",
    docker_pids_limit: int = 100,
    docker_cpu_period: int = 100_000,
    docker_cpu_quota: int = 50_000,
    cloudflare_worker_url: str = "",
    timeout: float = 300.0,
    e2b_event_queue_size: int = 1000,
    e2b_npm_install_timeout: int = 60,
    e2b_npm_poll_deadline: float = 90.0,
    e2b_execution_grace_period: float = 30.0,
) -> SandboxBackend:
    """Create the appropriate sandbox backend.

    Raises ``ValueError`` for unknown backend types.
    """
    if backend_type not in _VALID_BACKENDS:
        raise ValueError(
            f"Unknown sandbox backend '{backend_type}'. "
            f"Valid options: {', '.join(sorted(_VALID_BACKENDS))}"
        )

    if backend_type == "e2b":
        return E2BBackend(
            e2b_api_key=e2b_api_key,
            template=template,
            timeout=timeout,
            event_queue_size=e2b_event_queue_size,
            npm_install_timeout=e2b_npm_install_timeout,
            npm_poll_deadline=e2b_npm_poll_deadline,
            execution_grace_period=e2b_execution_grace_period,
        )
    if backend_type == "docker":
        return DockerBackend(
            docker_image=docker_image,
            docker_url=docker_url,
            timeout=timeout,
            memory_limit=docker_memory_limit,
            seccomp_profile=docker_seccomp_profile,
            pids_limit=docker_pids_limit,
            cpu_period=docker_cpu_period,
            cpu_quota=docker_cpu_quota,
        )
    if backend_type == "local":
        return LocalBackend(timeout=timeout)
    # cloudflare
    return CloudflareBackend(
        worker_url=cloudflare_worker_url,
        timeout=timeout,
    )
