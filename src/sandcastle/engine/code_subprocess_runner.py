"""Out-of-process runner for workflow ``code`` steps.

The in-process ``exec`` path in :mod:`sandcastle.engine.executor` runs
restricted-but-still-Python code inside the API/worker process. Even with the
admin gate, the AST validator, the regex blocklist, and the secret-free file
helpers, a novel sandbox escape would land in the parent's address space next to
``settings``, the DB session factory, and other tenants' in-flight data.

This module runs the same restricted code in a *separate Python subprocess* that:

- does NOT inherit the parent's environment secrets (a minimal ``env`` is passed);
- rebuilds the SAME restricted ``exec_globals`` and re-runs the SAME AST + regex
  validation the parent uses, by importing ``_validate_code_step_ast``,
  ``_CODE_STEP_BLOCKED_PATTERNS`` and ``make_code_sandbox_helpers`` (no forked copy
  of the security checks);
- applies POSIX ``resource`` limits (CPU seconds, address space) before exec;
- can be *really killed* on timeout, which the in-process thread path could not do.

The parent (:func:`run_code_in_subprocess`) enforces the wall-clock timeout and
kills the child if it overruns. The child protocol is a single JSON object on
stdout: ``{"status": "completed", "output": <json>}`` or
``{"status": "failed", "error": "<message>"}``. User ``print`` output is diverted
to stderr so it cannot corrupt the result channel.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from sandcastle.engine.subprocess_env import build_minimal_subprocess_env


class SubprocessInfraError(RuntimeError):
    """Raised for infrastructure failures (spawn / serialization) so the caller

    can fall back to the in-process path instead of failing the step.
    """


# ---------------------------------------------------------------------------
# Child side - runs inside the spawned subprocess
# ---------------------------------------------------------------------------


def _build_exec_globals(input_data: Any, step_outputs: Any, data_dir: str) -> dict[str, Any]:
    """Rebuild the exact restricted globals the in-process path uses.

    Kept byte-for-byte in sync with ``_execute_code_step`` in executor.py: the
    same ``__builtins__`` allowlist, the same ``json`` / ``base64`` modules, and
    the same ``read_file_b64`` / ``save_file_b64`` helpers bound to ``data_dir``.
    """
    import base64 as _b64_mod

    from sandcastle.engine.code_sandbox_helpers import make_code_sandbox_helpers

    resolved_data_dir = Path(data_dir).resolve()
    read_file_b64, save_file_b64 = make_code_sandbox_helpers(resolved_data_dir)

    return {
        "__builtins__": {
            "len": len,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "sorted": sorted,
            "min": min,
            "max": max,
            "sum": sum,
            "abs": abs,
            "round": round,
            "isinstance": isinstance,
            "print": print,
            "None": None,
            "True": True,
            "False": False,
        },
        "_input": input_data,
        "_steps": step_outputs,
        "json": json,
        "base64": _b64_mod,
        "read_file_b64": read_file_b64,
        "save_file_b64": save_file_b64,
        "result": None,
    }


def _apply_resource_limits(cpu_seconds: int) -> None:
    """Cap CPU time and address space in the child on POSIX.

    On non-POSIX platforms ``resource`` is unavailable, so we rely on the
    parent's wall-clock timeout instead. Failures to set a limit are tolerated
    (they must never prevent the code from running when limits are simply
    unsupported).
    """
    try:
        import resource
    except ImportError:
        return
    try:
        soft = max(1, int(cpu_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (soft, soft + 1))
    except (ValueError, OSError):
        pass
    try:
        # 4 GiB address-space cap. Generous on purpose: Python plus the imported
        # security modules already use a few hundred MB, and we only want to stop
        # pathological allocations, not break normal transforms.
        cap = 4 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (ValueError, OSError):
        pass


def _child_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate then execute the code step inside the child process."""
    from sandcastle.engine.executor import (
        _CODE_STEP_BLOCKED_PATTERNS,
        _validate_code_step_ast,
    )

    code = payload["code"]

    # Defense in depth: re-run the SAME checks the parent already ran, so the
    # child never execs code that failed validation even if invoked directly.
    blocked = _CODE_STEP_BLOCKED_PATTERNS.search(code)
    if blocked:
        return {
            "status": "failed",
            "error": f"Code step uses blocked pattern '{blocked.group()}'.",
        }
    ast_error = _validate_code_step_ast(code)
    if ast_error:
        return {"status": "failed", "error": ast_error}

    exec_globals = _build_exec_globals(
        payload.get("_input"),
        payload.get("_steps"),
        payload["data_dir"],
    )

    _apply_resource_limits(int(payload.get("cpu_seconds", 30)))

    # Divert user print() output to stderr so it cannot corrupt the JSON result
    # written to stdout.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        exec(code, exec_globals)  # noqa: S102
    except Exception as exc:  # noqa: BLE001 - surface the message like the in-process path
        sys.stdout = real_stdout
        return {"status": "failed", "error": str(exc)}
    finally:
        sys.stdout = real_stdout

    return {"status": "completed", "output": exec_globals.get("result", None)}


def _child_main() -> int:
    """Entry point when run as ``python -m sandcastle.engine.code_subprocess_runner``."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(
            json.dumps({"status": "failed", "error": f"bad runner payload: {exc}"})
        )
        return 0
    result = _child_run(payload)
    # default=str keeps a non-JSON-serializable ``result`` from hard-failing the
    # step, mirroring the intent that code steps return simple data.
    sys.stdout.write(json.dumps(result, default=str))
    return 0


# ---------------------------------------------------------------------------
# Parent side - called from the executor
# ---------------------------------------------------------------------------


def _child_env() -> dict[str, str]:
    """Build a minimal environment for the child that excludes parent secrets.

    Only what a bare Python subprocess needs to import ``sandcastle`` is passed:
    ``PYTHONPATH`` (so the package resolves in dev/editable installs) plus a few
    OS-level, non-secret variables. Provider API keys, DB URLs, tokens, and every
    other parent env var are intentionally dropped.
    """
    env: dict[str, str] = {"PYTHONPATH": os.pathsep.join(sys.path)}
    env.update(build_minimal_subprocess_env())
    # Keep hash randomization deterministic-free but present; harmless.
    return env


async def run_code_in_subprocess(
    code: str,
    input_data: Any,
    step_outputs: Any,
    data_dir: str,
    timeout: int,
) -> dict[str, Any]:
    """Run a code step in an isolated subprocess and return a result dict.

    Returns ``{"status": "completed", "output": <obj>}`` or
    ``{"status": "failed", "error": "<message>"}``. Raises
    :class:`SubprocessInfraError` for spawn/serialization problems so the caller
    can fall back to the in-process path (a genuine sandbox result never raises).
    """
    import asyncio

    payload = {
        "code": code,
        "_input": input_data,
        "_steps": step_outputs,
        "data_dir": str(data_dir),
        "cpu_seconds": int(timeout),
    }
    try:
        payload_bytes = json.dumps(payload).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SubprocessInfraError(
            f"code step input is not JSON-serializable: {exc}"
        ) from exc

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "sandcastle.engine.code_subprocess_runner",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_env(),
        )
    except (OSError, ValueError) as exc:
        raise SubprocessInfraError(f"could not spawn code subprocess: {exc}") from exc

    # Shield communicate() so a timeout kills the child and then lets the same
    # coroutine finish draining and closing the pipes, instead of cancelling it
    # mid-read (which leaks a pipe reader and emits a ResourceWarning).
    comm = asyncio.ensure_future(proc.communicate(input=payload_bytes))
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.shield(comm),
            timeout=max(1, int(timeout)),
        )
    except asyncio.TimeoutError:
        # Real kill - the in-process thread path could not do this.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await comm  # drain and close the pipes after the kill
        except Exception:  # noqa: BLE001 - best effort reap
            pass
        return {
            "status": "failed",
            "error": f"Code step timed out after {int(timeout)}s",
        }

    text = (stdout or b"").decode("utf-8", "replace").strip()
    if not text:
        err_tail = (stderr or b"").decode("utf-8", "replace").strip()[-500:]
        raise SubprocessInfraError(
            f"code subprocess produced no result (exit {proc.returncode}): {err_tail}"
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SubprocessInfraError(
            f"code subprocess returned malformed result: {exc}: {text[:200]}"
        ) from exc


if __name__ == "__main__":
    raise SystemExit(_child_main())
