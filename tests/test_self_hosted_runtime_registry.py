"""Smoke tests for the self-hosted sandbox runtime registry entry (v0.32).

No network: every test stays inside the RUNTIMES dict, the dispatch
helper, and the lazy-import seam. Sibling Phase 2a ships the actual
SelfHostedWorker class - here we only verify that registry wiring is
correct and that a missing sibling module fails fast with an actionable
error.
"""

from __future__ import annotations

import inspect
import sys

import pytest

from sandcastle.engine.agent_runtime import (
    AgentRuntime,
    AnthropicRuntime,
    AgentSDKRuntimeAdapter,
    AutoRuntime,
    LocalRuntime,
    RUNTIMES,
    SelfHostedSandboxRuntime,
    get_runtime,
)


def test_registry_has_self_hosted_sandbox_entry() -> None:
    """RUNTIMES gains a 'self-hosted-sandbox' entry of the expected type."""
    assert "self-hosted-sandbox" in RUNTIMES
    rt = RUNTIMES["self-hosted-sandbox"]
    assert isinstance(rt, SelfHostedSandboxRuntime)
    assert isinstance(rt, AgentRuntime)
    assert rt.name == "self-hosted-sandbox"


def test_get_runtime_returns_same_singleton() -> None:
    """get_runtime() hands back the same registry instance."""
    rt = get_runtime("self-hosted-sandbox")
    assert rt is RUNTIMES["self-hosted-sandbox"]


def test_get_runtime_rejects_unknown_name() -> None:
    """Regression guard: unknown runtime names still raise ValueError."""
    with pytest.raises(ValueError, match="Unknown runtime"):
        get_runtime("does-not-exist")


def test_existing_runtimes_unchanged() -> None:
    """Adding the new entry must not displace any of the existing ones."""
    assert isinstance(RUNTIMES["anthropic"], AnthropicRuntime)
    assert isinstance(RUNTIMES["auto"], AutoRuntime)
    assert isinstance(RUNTIMES["local"], LocalRuntime)
    assert isinstance(RUNTIMES["agent-sdk"], AgentSDKRuntimeAdapter)
    # Exactly the five we expect - guard against accidental extras.
    assert set(RUNTIMES) == {
        "auto",
        "anthropic",
        "local",
        "agent-sdk",
        "self-hosted-sandbox",
    }


def test_execute_is_awaitable() -> None:
    """SelfHostedSandboxRuntime.execute is an async coroutine function."""
    rt = SelfHostedSandboxRuntime()
    assert inspect.iscoroutinefunction(rt.execute)
    assert inspect.iscoroutinefunction(rt.is_available)


def test_missing_worker_module_raises_typed_error_with_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SelfHostedWorker is unimportable, _load_worker_cls raises with hint."""
    # Force the import to fail even if a future module appears on path.
    monkeypatch.setitem(sys.modules, "sandcastle.engine.self_hosted_worker", None)

    rt = SelfHostedSandboxRuntime()
    with pytest.raises(RuntimeError) as exc_info:
        rt._load_worker_cls()

    msg = str(exc_info.value)
    assert "SelfHostedWorker is not available" in msg
    assert "self-hosted" in msg  # install hint mentions the extra
    assert "deploy/cookbooks/docker" in msg  # points at the cookbook


@pytest.mark.asyncio
async def test_is_available_returns_false_when_worker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_available() must not raise when the sibling module is missing."""
    monkeypatch.setitem(sys.modules, "sandcastle.engine.self_hosted_worker", None)
    monkeypatch.setenv("ANTHROPIC_ENVIRONMENT_KEY", "sk-ant-oat01-test")

    rt = SelfHostedSandboxRuntime()
    assert await rt.is_available() is False
