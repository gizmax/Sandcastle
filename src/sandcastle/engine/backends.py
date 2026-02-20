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
    ) -> None:
        self._api_key = e2b_api_key
        self._template = template
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "e2b"

    async def health(self) -> bool:
        if not self._api_key:
            return False
        try:
            import e2b  # noqa: F401
            return True
        except ImportError:
            return False

    async def close(self) -> None:
        pass  # E2B sandboxes are ephemeral - nothing to clean up

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
    ) -> AsyncIterator[SSEEvent]:
        from e2b import AsyncSandbox

        queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=1000)
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

                await sandbox.files.write(
                    f"/home/user/{runner_file}", runner_code
                )
                pkg = (
                    "@anthropic-ai/claude-agent-sdk"
                    if use_claude_runner
                    else "openai"
                )
                # Run npm install in background mode to avoid E2B SDK
                # gRPC hang - the SDK's foreground commands.run() can block
                # indefinitely when the internal event stream stalls.
                npm_handle = await sandbox.commands.run(
                    f"npm install {pkg} 2>/dev/null || true",
                    background=True,
                    timeout=60,
                )
                try:
                    deadline = asyncio.get_event_loop().time() + 90
                    while npm_handle.exit_code is None:
                        if asyncio.get_event_loop().time() > deadline:
                            logger.warning(
                                "npm install timed out after 90s, "
                                "proceeding anyway"
                            )
                            break
                        await asyncio.sleep(1.0)
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
            deadline = asyncio.get_event_loop().time() + timeout + 30
            while True:
                if asyncio.get_event_loop().time() > deadline:
                    logger.warning(
                        "E2B execution exceeded deadline (%.0fs + 30s grace), "
                        "stopping", timeout,
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
    """Local Docker containers via aiodocker."""

    def __init__(
        self,
        docker_image: str = "sandcastle-runner:latest",
        docker_url: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._image = docker_image
        self._url = docker_url
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "docker"

    async def health(self) -> bool:
        try:
            import aiodocker
            kwargs: dict[str, Any] = {}
            if self._url:
                kwargs["url"] = self._url
            docker = aiodocker.Docker(**kwargs)
            try:
                await docker.version()
                return True
            finally:
                await docker.close()
        except ImportError:
            logger.error(
                "aiodocker not installed. "
                "Install with: pip install sandcastle-ai[docker]"
            )
            return False
        except Exception:
            return False

    async def close(self) -> None:
        pass

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
    ) -> AsyncIterator[SSEEvent]:
        try:
            import aiodocker
        except ImportError:
            raise RuntimeError(
                "aiodocker not installed. "
                "Install with: pip install sandcastle-ai[docker]"
            )

        import io
        import tarfile

        kwargs: dict[str, Any] = {}
        if self._url:
            kwargs["url"] = self._url
        docker = aiodocker.Docker(**kwargs)
        container = None

        try:
            # Read runner script
            runner_path = _RUNNER_DIR / runner_file
            runner_code = runner_path.read_text() if runner_path.exists() else ""
            if not runner_code:
                raise RuntimeError(f"Runner script not found: {runner_file}")

            # Create container
            config = {
                "Image": self._image,
                "Cmd": ["node", f"/home/user/{runner_file}"],
                "Env": [f"{k}={v}" for k, v in envs.items()],
                "WorkingDir": "/home/user",
                "User": "1000:1000",
                "NetworkMode": "bridge",
                "HostConfig": {
                    "AutoRemove": False,
                    "Memory": 512 * 1024 * 1024,  # 512MB limit
                },
            }

            container = await docker.containers.create_or_run(config=config)

            # Upload runner script via tar archive
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                data = runner_code.encode()
                info = tarfile.TarInfo(name=runner_file)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
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
                    await container.delete(force=True)
                except Exception:
                    pass
            await docker.close()


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
        return shutil.which("node") is not None

    async def close(self) -> None:
        pass

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
    ) -> AsyncIterator[SSEEvent]:
        import os

        runner_path = _RUNNER_DIR / runner_file
        if not runner_path.exists():
            raise RuntimeError(f"Runner script not found: {runner_path}")

        # Merge host env with provided envs
        proc_env = {**os.environ, **envs}

        proc = await asyncio.create_subprocess_exec(
            "node",
            str(runner_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
            cwd=str(_RUNNER_DIR),
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
    """Edge sandboxes via a deployed Cloudflare Worker."""

    def __init__(
        self,
        worker_url: str,
        timeout: float = 300.0,
    ) -> None:
        self._worker_url = worker_url.rstrip("/") if worker_url else ""
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "cloudflare"

    async def health(self) -> bool:
        if not self._worker_url:
            return False

        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._worker_url}/health")
                data = resp.json()
                return data.get("ok", False)
        except Exception:
            return False

    async def close(self) -> None:
        pass

    async def start(
        self,
        runner_file: str,
        envs: dict[str, str],
        use_claude_runner: bool,
        timeout: float,
    ) -> AsyncIterator[SSEEvent]:
        if not self._worker_url:
            raise RuntimeError(
                "CLOUDFLARE_WORKER_URL is required for the cloudflare backend"
            )

        import httpx

        # Read runner script to send to CF Worker
        runner_path = _RUNNER_DIR / runner_file
        runner_code = runner_path.read_text() if runner_path.exists() else ""
        if not runner_code:
            raise RuntimeError(f"Runner script not found: {runner_file}")

        payload = {
            "runner_file": runner_file,
            "runner_content": runner_code,
            "envs": envs,
        }

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout)
        ) as client:
            resp = await client.post(
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
    cloudflare_worker_url: str = "",
    timeout: float = 300.0,
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
        )
    if backend_type == "docker":
        return DockerBackend(
            docker_image=docker_image,
            docker_url=docker_url,
            timeout=timeout,
        )
    if backend_type == "local":
        return LocalBackend(timeout=timeout)
    # cloudflare
    return CloudflareBackend(
        worker_url=cloudflare_worker_url,
        timeout=timeout,
    )
