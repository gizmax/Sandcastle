"""Tests for the Claude Agent SDK alternative runtime."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sandcastle.engine import agent_sdk_runtime as sdk_rt
from sandcastle.engine.agent_sdk_runtime import (
    AgentSDKConfig,
    AgentSDKConfigError,
    AgentSDKNotInstalled,
    AgentSDKResult,
    AgentSDKRunner,
    is_available,
    validate_config,
)


# ---------------------------------------------------------------------------
# Helpers: fake SDK that mirrors the shape the runtime expects.
# ---------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    output: str = "hello"
    tool_calls: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    transcript_path: str | None = None
    error: str | None = None


class _FakeAgentDefinition:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeClaudeAgent:
    """Stand-in for ``claude_agent_sdk.ClaudeAgent``.

    Records the definition it was constructed with and the prompt it was run
    on, so tests can assert config round-trips correctly.
    """

    last_instance: "_FakeClaudeAgent | None" = None

    def __init__(self, definition: _FakeAgentDefinition) -> None:
        self.definition = definition
        self.run = AsyncMock(return_value=_FakeResponse())
        _FakeClaudeAgent.last_instance = self


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse | None = None) -> type:
    """Install a fake ``claude_agent_sdk`` module into ``sys.modules``.

    Returns the fake ClaudeAgent class so tests can inspect it.
    """

    fake_module = ModuleType("claude_agent_sdk")

    if response is not None:
        class _Agent(_FakeClaudeAgent):
            def __init__(self, definition: _FakeAgentDefinition) -> None:
                super().__init__(definition)
                self.run = AsyncMock(return_value=response)
        agent_cls: type = _Agent
    else:
        agent_cls = _FakeClaudeAgent

    fake_module.ClaudeAgent = agent_cls  # type: ignore[attr-defined]
    fake_module.AgentDefinition = _FakeAgentDefinition  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)
    return agent_cls


def _block_sdk_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import claude_agent_sdk`` raise ImportError inside the runtime."""

    # Drop any cached copy and shadow with a meta path finder that refuses it.
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)

    import importlib.abc
    import importlib.machinery

    class _Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name: str, path: Any = None, target: Any = None) -> Any:
            if name == "claude_agent_sdk":
                raise ImportError("blocked for test")
            return None

    blocker = _Blocker()
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_module_importable_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lazy import: re-importing the runtime works even when SDK is missing."""

    _block_sdk_import(monkeypatch)
    monkeypatch.delitem(sys.modules, "sandcastle.engine.agent_sdk_runtime", raising=False)

    module = importlib.import_module("sandcastle.engine.agent_sdk_runtime")
    assert hasattr(module, "AgentSDKRunner")
    assert module.is_available() is False


def test_is_available_false_when_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_sdk_import(monkeypatch)
    assert is_available() is False


def test_is_available_true_when_sdk_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch)
    assert is_available() is True


@pytest.mark.asyncio
async def test_run_raises_when_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_sdk_import(monkeypatch)
    runner = AgentSDKRunner()
    with pytest.raises(AgentSDKNotInstalled):
        await runner.run("hi", AgentSDKConfig())


@pytest.mark.asyncio
async def test_run_invokes_sdk_with_config(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_cls = _install_fake_sdk(monkeypatch)

    cfg = AgentSDKConfig(
        model="claude-sonnet-4-6",
        system_prompt="be helpful",
        tools=[{"name": "search"}],
        mcp_servers={"local": "http://localhost:9000"},
        permission_mode="auto",
        skills_dir="/tmp/skills",
        commands_dir="/tmp/cmds",
        working_dir="/tmp/work",
        max_turns=7,
        timeout_seconds=60,
    )

    runner = AgentSDKRunner()
    result = await runner.run("hello there", cfg)

    assert isinstance(result, AgentSDKResult)
    instance = agent_cls.last_instance  # type: ignore[attr-defined]
    assert instance is not None
    assert instance.definition.kwargs["model"] == "claude-sonnet-4-6"
    assert instance.definition.kwargs["system_prompt"] == "be helpful"
    assert instance.definition.kwargs["tools"] == [{"name": "search"}]
    assert instance.definition.kwargs["mcp_servers"] == {"local": "http://localhost:9000"}
    assert instance.definition.kwargs["max_turns"] == 7
    instance.run.assert_awaited_once_with("hello there")


@pytest.mark.asyncio
async def test_result_parsed_from_sdk_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(
        output="final answer",
        tool_calls=[{"name": "bash", "input": {"cmd": "ls"}}],
        cost_usd=0.0123,
        transcript_path="/tmp/transcript.jsonl",
        error=None,
    )
    _install_fake_sdk(monkeypatch, response=response)

    runner = AgentSDKRunner()
    result = await runner.run("go", AgentSDKConfig())

    assert result.output == "final answer"
    assert result.tool_calls == [{"name": "bash", "input": {"cmd": "ls"}}]
    assert result.cost_usd == pytest.approx(0.0123)
    assert result.transcript_path == "/tmp/transcript.jsonl"
    assert result.error is None
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_cost_reported_back_from_result(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(output="ok", cost_usd=2.5)
    _install_fake_sdk(monkeypatch, response=response)

    runner = AgentSDKRunner()
    result = await runner.run("compute", AgentSDKConfig())

    assert result.cost_usd == pytest.approx(2.5)


def test_validate_config_rejects_non_positive_max_turns() -> None:
    errors = validate_config(AgentSDKConfig(max_turns=0))
    assert any("max_turns" in e for e in errors)

    errors = validate_config(AgentSDKConfig(max_turns=-3))
    assert any("max_turns" in e for e in errors)


def test_validate_config_rejects_non_positive_timeout() -> None:
    errors = validate_config(AgentSDKConfig(timeout_seconds=0))
    assert any("timeout_seconds" in e for e in errors)

    errors = validate_config(AgentSDKConfig(timeout_seconds=-1))
    assert any("timeout_seconds" in e for e in errors)


def test_validate_config_rejects_skills_scheme_without_dir() -> None:
    cfg = AgentSDKConfig(mcp_servers={"s": "skills://my-skill"}, skills_dir=None)
    errors = validate_config(cfg)
    assert any("skills_dir" in e for e in errors)


def test_validate_config_accepts_valid_defaults() -> None:
    assert validate_config(AgentSDKConfig()) == []


@pytest.mark.parametrize("mode", ["auto", "prompt", "read_only"])
@pytest.mark.asyncio
async def test_permission_mode_round_trips(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    agent_cls = _install_fake_sdk(monkeypatch)
    runner = AgentSDKRunner()

    cfg = AgentSDKConfig(permission_mode=mode)  # type: ignore[arg-type]
    await runner.run("ok", cfg)

    instance = agent_cls.last_instance  # type: ignore[attr-defined]
    assert instance is not None
    assert instance.definition.kwargs["permission_mode"] == mode


@pytest.mark.asyncio
async def test_run_with_invalid_config_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SDK present so the config check runs first.
    _install_fake_sdk(monkeypatch)
    runner = AgentSDKRunner()
    with pytest.raises(AgentSDKConfigError):
        await runner.run("hi", AgentSDKConfig(max_turns=0))


def test_runner_name_attribute() -> None:
    assert AgentSDKRunner.name == "agent-sdk"
