"""Claude Agent SDK alternative runtime for Sandcastle.

This runtime executes the agent loop IN-PROCESS using the open-source
``claude-agent-sdk`` package, instead of delegating to Anthropic's managed
agent infrastructure (which is what the existing ``runtime: "anthropic"``
backend does in ``agent_runtime.py``).

When to choose which runtime
----------------------------

Managed Agents runtime (``runtime: "anthropic"``):
    - Zero infra to operate, Anthropic manages the agent loop, tool execution,
      retries, and timeouts on their side.
    - Lower latency for cold starts (warm pool on Anthropic side).
    - Pay per token + managed surcharge, but no compute on your side.
    - Less flexible: you cannot inject custom skills, slash commands, or
      attach a custom local filesystem as the agent's working dir.

Agent SDK runtime (``runtime: "agent-sdk"``):
    - Runs the agent loop locally, in the Sandcastle worker process.
    - Required for air-gapped / EU sovereignty deployments where workflow
      traffic must stay on the operator's infra (only the model API call
      leaves, and even that can be pointed at an EU endpoint or a self-hosted
      proxy).
    - Full access to Claude Code style features: ``.claude/skills/`` directory,
      slash commands in ``.claude/commands/``, hooks, custom MCP servers
      bound to local filesystems.
    - You pay for the compute that runs the loop (CPU + memory in your
      worker), in addition to the token cost.
    - No dependency on Anthropic managed agent infrastructure availability.

The SDK is imported lazily inside functions so that ``import
sandcastle.engine.agent_sdk_runtime`` keeps working even when
``claude-agent-sdk`` is not installed. Operators opt in with::

    pip install claude-agent-sdk

We intentionally do not list ``claude-agent-sdk`` in Sandcastle's project
dependencies. It stays an optional install.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Permission modes accepted by the runtime.
PermissionMode = Literal["auto", "prompt", "read_only"]


class AgentSDKNotInstalled(Exception):
    """Raised when ``claude-agent-sdk`` is not importable but a run was requested."""


class AgentSDKConfigError(Exception):
    """Raised when an :class:`AgentSDKConfig` fails validation."""


@dataclass
class AgentSDKConfig:
    """Configuration for an Agent SDK run.

    Attributes mirror the Claude Agent SDK surface without leaking SDK types,
    so callers can construct configs even when the SDK is not installed.
    """

    model: str = "claude-sonnet-4-6"
    system_prompt: str | None = None
    tools: list[dict] = field(default_factory=list)
    mcp_servers: dict[str, str] = field(default_factory=dict)
    permission_mode: PermissionMode = "prompt"
    skills_dir: str | None = None
    commands_dir: str | None = None
    working_dir: str | None = None
    max_turns: int = 50
    timeout_seconds: int = 600


@dataclass
class AgentSDKResult:
    """Result of a single Agent SDK run."""

    output: str
    tool_calls: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    transcript_path: str | None = None
    error: str | None = None


def _try_import_sdk() -> tuple[Any, Any] | None:
    """Attempt to import the SDK lazily.

    Returns a tuple ``(ClaudeAgent, AgentDefinition)`` if importable, else
    ``None``. Kept as a function (not a module-level import) so that this
    module is safe to import without the SDK installed.
    """

    try:
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            AgentDefinition,
            ClaudeAgent,
        )
    except ImportError:
        return None
    return ClaudeAgent, AgentDefinition


def is_available() -> bool:
    """Return True if the ``claude-agent-sdk`` package is importable."""

    return _try_import_sdk() is not None


def validate_config(config: AgentSDKConfig) -> list[str]:
    """Validate a config and return a list of human-readable error strings.

    An empty list means the config is acceptable. The function never raises;
    callers decide how to react.
    """

    errors: list[str] = []

    if not isinstance(config.max_turns, int) or config.max_turns <= 0:
        errors.append("max_turns must be a positive integer")

    if not isinstance(config.timeout_seconds, int) or config.timeout_seconds <= 0:
        errors.append("timeout_seconds must be a positive integer")

    if config.permission_mode not in ("auto", "prompt", "read_only"):
        errors.append(
            f"permission_mode must be one of auto, prompt, read_only "
            f"(got {config.permission_mode!r})"
        )

    if not isinstance(config.tools, list):
        errors.append("tools must be a list of tool definition dicts")

    if not isinstance(config.mcp_servers, dict):
        errors.append("mcp_servers must be a dict of name to URL or command")

    # If any MCP server URL uses a skills:// scheme but no skills_dir is set,
    # the SDK has nothing to resolve against.
    for name, target in (config.mcp_servers or {}).items():
        if isinstance(target, str) and target.startswith("skills://") and not config.skills_dir:
            errors.append(
                f"mcp_servers[{name!r}] uses skills:// scheme but skills_dir is not set"
            )

    return errors


class AgentSDKRunner:
    """Runs prompts through the Claude Agent SDK in-process."""

    name: str = "agent-sdk"

    async def run(self, prompt: str, config: AgentSDKConfig) -> AgentSDKResult:
        """Execute ``prompt`` against the SDK with ``config``.

        Raises:
            AgentSDKNotInstalled: if ``claude-agent-sdk`` is not importable.
            AgentSDKConfigError: if ``config`` fails validation.
        """

        errors = validate_config(config)
        if errors:
            raise AgentSDKConfigError("; ".join(errors))

        imported = _try_import_sdk()
        if imported is None:
            raise AgentSDKNotInstalled(
                "claude-agent-sdk is not installed. Install with: "
                "pip install claude-agent-sdk"
            )

        ClaudeAgent, AgentDefinition = imported

        definition = AgentDefinition(
            model=config.model,
            system_prompt=config.system_prompt,
            tools=config.tools,
            mcp_servers=config.mcp_servers,
            permission_mode=config.permission_mode,
            skills_dir=config.skills_dir,
            commands_dir=config.commands_dir,
            working_dir=config.working_dir,
            max_turns=config.max_turns,
        )

        agent = ClaudeAgent(definition=definition)

        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                agent.run(prompt),
                timeout=config.timeout_seconds,
            )
        except TimeoutError:
            duration_ms = int((time.monotonic() - started) * 1000)
            return AgentSDKResult(
                output="",
                tool_calls=[],
                cost_usd=0.0,
                duration_ms=duration_ms,
                transcript_path=None,
                error=f"timeout after {config.timeout_seconds}s",
            )
        except Exception as exc:  # noqa: BLE001 - surface to caller as result.error
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.exception("Agent SDK run failed")
            return AgentSDKResult(
                output="",
                tool_calls=[],
                cost_usd=0.0,
                duration_ms=duration_ms,
                transcript_path=None,
                error=str(exc),
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        return _parse_response(response, duration_ms=duration_ms)


def _parse_response(response: Any, *, duration_ms: int) -> AgentSDKResult:
    """Translate the SDK response object into an :class:`AgentSDKResult`.

    The SDK exposes attributes like ``output``, ``tool_calls``, ``cost_usd``,
    and ``transcript_path`` on its response. We read defensively via
    ``getattr`` so a minor SDK shape change does not crash the runtime.
    """

    output = getattr(response, "output", "") or ""
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    cost_usd = float(getattr(response, "cost_usd", 0.0) or 0.0)
    transcript_path = getattr(response, "transcript_path", None)
    error = getattr(response, "error", None)

    return AgentSDKResult(
        output=output,
        tool_calls=tool_calls,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        transcript_path=transcript_path,
        error=error,
    )


__all__ = [
    "AgentSDKConfig",
    "AgentSDKConfigError",
    "AgentSDKNotInstalled",
    "AgentSDKResult",
    "AgentSDKRunner",
    "PermissionMode",
    "is_available",
    "validate_config",
]
