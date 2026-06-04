"""Tests for SandshoreRuntime.sandbox_exec + LocalBackend.exec_command.

Browser dom/computer_use/lightpanda modes call ``sandbox.sandbox_exec(...)`` to run
setup/extraction scripts in the sandbox. Previously the method did not exist (every
such step raised AttributeError); these tests cover the LOCAL backend implementation
and the clear-error path for backends without a persistent exec primitive.
"""

from __future__ import annotations

import pytest

from sandcastle.engine.backends import LocalBackend
from sandcastle.engine.sandshore import SandshoreError, get_sandshore_runtime


@pytest.mark.asyncio
async def test_local_backend_exec_command_runs():
    out = await LocalBackend().exec_command("printf", ["%s", "hello"])
    assert out == {"stdout": "hello", "stderr": "", "exit_code": 0}


@pytest.mark.asyncio
async def test_local_backend_exec_command_nonzero_exit():
    out = await LocalBackend().exec_command("sh", ["-c", "echo oops >&2; exit 3"])
    assert out["exit_code"] == 3
    assert "oops" in out["stderr"]


@pytest.mark.asyncio
async def test_local_backend_exec_command_timeout():
    with pytest.raises(RuntimeError, match="timed out"):
        await LocalBackend().exec_command("sleep", ["5"], timeout=0.2)


@pytest.mark.asyncio
async def test_runtime_sandbox_exec_delegates_to_local_backend():
    rt = get_sandshore_runtime("", "", sandbox_backend="local")
    # sandbox positional is accepted for call-site compatibility and ignored.
    out = await rt.sandbox_exec(object(), "printf", ["%s", "via-runtime"])
    assert out["stdout"] == "via-runtime"
    assert out["exit_code"] == 0


@pytest.mark.asyncio
async def test_runtime_sandbox_exec_clear_error_when_backend_unsupported():
    rt = get_sandshore_runtime("", "k", sandbox_backend="local")

    class _NoExecBackend:
        name = "e2b"

        async def close(self) -> None:
            pass

    rt._backend = _NoExecBackend()  # simulate a backend without exec_command
    with pytest.raises(SandshoreError, match="not supported on the 'e2b' backend"):
        await rt.sandbox_exec(object(), "echo", ["x"])
