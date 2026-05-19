"""Anthropic self-hosted sandbox runtime for Sandcastle.

Wraps Anthropic's May 19, 2026 Managed Agents self-hosted sandbox feature.
Lets a managed-agent session execute its tool calls inside customer
infrastructure (Cloudflare microVMs, Daytona, Modal, Vercel, or a plain
Docker host) instead of Anthropic's hosted sandbox pool.

Spec:
- https://claude.com/blog/claude-managed-agents-updates
- https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes

Uses the same ``managed-agents-2026-04-01`` beta header that Sandcastle
already sends; the new bit is the ``environment`` block on session
creation and a poller worker that claims work items from
``/v1/environments/{id}/work``.

Key safety contracts enforced here, NOT delegated to the SDK:

1. ``ANTHROPIC_API_KEY`` must NOT be present on the worker host. Tool
   calls can read env vars, so an org-scoped credential would leak. Only
   the environment-scoped key (``sk-ant-oat01-...``) is allowed.
2. Memory Stores are NOT supported with self-hosted sandboxes
   (explicitly disclaimed by Anthropic). Sandcastle hard-errors when a
   workflow combines both rather than silently dropping memory writes.
3. Claude Platform on AWS does not support self-hosted sandboxes. We
   warn (not error) when ``ANTHROPIC_REGION`` looks AWS-bound.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider enum
# ---------------------------------------------------------------------------


class SandboxProvider(str, Enum):
    """Supported self-hosted sandbox providers.

    These map 1:1 to the cookbooks Anthropic ships at
    https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/self_hosted_sandboxes.
    """

    CLOUDFLARE = "cloudflare"  # microVMs + isolates, zero-trust secrets
    DAYTONA = "daytona"  # long-running stateful + SSH
    MODAL = "modal"  # sub-second startup + GPU on demand
    VERCEL = "vercel"  # VPC peering + ms startup
    DOCKER = "docker"  # local Docker (development / on-prem)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SelfHostedSandboxError(Exception):
    """Base class for all self-hosted sandbox errors."""


class OrgKeyLeakError(SelfHostedSandboxError):
    """Worker host has ANTHROPIC_API_KEY set; refuse to start.

    Anthropic's own guidance: setting ANTHROPIC_API_KEY on the worker
    host exposes an org-scoped credential to agent tool calls. We refuse
    to start the worker process unless that key is absent.
    """


class MemoryStoresIncompatibleError(SelfHostedSandboxError):
    """Workflow combines memory_stores + self_hosted runtime.

    Anthropic disclaims this combination; the session-create call
    silently drops memory operations rather than erroring, which is
    worse for users. Sandcastle hard-errors at config-parse time.
    """


class EnvironmentKeyFormatError(SelfHostedSandboxError):
    """Environment key does not match the sk-ant-oat01-* prefix."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


# Anthropic environment keys carry this prefix. Validating the prefix
# at config-load time catches "user pasted their org key by mistake"
# before that key ends up in a remote worker process.
_ENV_KEY_PREFIX = "sk-ant-oat01-"


@dataclass
class SelfHostedSandboxConfig:
    """Per-workflow self-hosted sandbox configuration.

    Attached to a managed-agent step via:

    .. code-block:: yaml

        - id: heavy-research
          type: managed-agent
          managed_agent_config:
            agent_template: researcher
            self_hosted_sandbox:
              environment_id: env_abc123
              environment_key_env: SANDCASTLE_ENV_KEY_PRIMARY
              provider: cloudflare
    """

    environment_id: str
    # Name of the env var holding the environment key. We never accept
    # the key inline in YAML to keep secrets out of workflow text +
    # git history.
    environment_key_env: str = "ANTHROPIC_ENVIRONMENT_KEY"
    provider: SandboxProvider = SandboxProvider.DOCKER
    # Free-form metadata Anthropic stores on the session. Workers can
    # filter on it during ``work.list``.
    metadata: dict[str, Any] = field(default_factory=dict)
    # Soft cap on concurrent work items the worker fleet will claim.
    # Used by autoscaling decisions, not enforced server-side.
    max_concurrent_sessions: int = 8


# ---------------------------------------------------------------------------
# Validation helpers (used by config-parse + runtime startup)
# ---------------------------------------------------------------------------


def assert_org_key_not_set(
    env: dict[str, str] | None = None, *, raise_on_violation: bool = True
) -> None:
    """Refuse to run if the worker process has an org-scoped Anthropic key.

    Reads from the current process environment by default. Pass an
    explicit dict to make this testable.
    """
    env = env if env is not None else os.environ
    if "ANTHROPIC_API_KEY" in env and env["ANTHROPIC_API_KEY"]:
        msg = (
            "ANTHROPIC_API_KEY is set in the worker environment. Self-hosted "
            "sandbox workers MUST use the environment-scoped key "
            "(sk-ant-oat01-...) instead - the org key would be exposed to "
            "every tool call executed inside the sandbox. Remove "
            "ANTHROPIC_API_KEY from the worker host and set "
            "ANTHROPIC_ENVIRONMENT_KEY (or the custom name in your "
            "SelfHostedSandboxConfig.environment_key_env)."
        )
        if raise_on_violation:
            raise OrgKeyLeakError(msg)
        logger.warning(msg)


def assert_memory_compatible(
    sandbox_config: SelfHostedSandboxConfig | None,
    memory_stores: list[str] | None,
) -> None:
    """Reject the memory_stores + self_hosted combination.

    Both args come straight from the parsed ManagedAgentConfig, so this
    is a single-call assertion at session-create time.
    """
    if sandbox_config is None:
        return
    if memory_stores:
        raise MemoryStoresIncompatibleError(
            "Memory Stores are not supported with self-hosted sandboxes "
            "(Anthropic explicitly disclaims this combination). Drop the "
            "memory_stores field, or move this step to the default hosted "
            "runtime. See "
            "https://platform.claude.com/docs/en/managed-agents/"
            "self-hosted-sandboxes#limitations"
        )


def assert_env_key_format(env_key: str) -> None:
    """Validate the environment key string shape.

    Catches the very common "I pasted the org key by mistake" error
    before the key reaches a remote worker. Anthropic does not document
    the env-key prefix as load-bearing, but it's stable enough to use as
    a guardrail.
    """
    if not env_key or not env_key.startswith(_ENV_KEY_PREFIX):
        raise EnvironmentKeyFormatError(
            f"Environment key must start with {_ENV_KEY_PREFIX!r}. Got a "
            "key with a different prefix; this is most likely an "
            "ANTHROPIC_API_KEY (sk-ant-...) pasted into the wrong slot."
        )


def warn_on_aws_region(
    env: dict[str, str] | None = None,
) -> str | None:
    """Emit a warning if the worker looks AWS-bound.

    Returns the offending region string for surfacing in test output;
    returns None when clean.
    """
    env = env if env is not None else os.environ
    region = env.get("ANTHROPIC_REGION") or env.get("AWS_REGION")
    if region and ("aws" in region.lower() or region.startswith("us-")):
        msg = (
            f"ANTHROPIC_REGION/AWS_REGION={region!r} looks AWS-bound. "
            "Self-hosted sandboxes are NOT supported on Claude Platform "
            "on AWS yet. The session will likely fail. Confirm you are "
            "running against the standard Anthropic endpoint, not AWS "
            "Bedrock or AWS Claude Platform."
        )
        logger.warning(msg)
        return region
    return None


# ---------------------------------------------------------------------------
# Session-create payload helper
# ---------------------------------------------------------------------------


def build_environment_block(config: SelfHostedSandboxConfig) -> dict[str, Any]:
    """Return the ``environment`` block to merge into a session-create body.

    Anthropic spec:

    .. code-block:: json

        {
          "environment_id": "env_abc123",
          "metadata": {"provider": "cloudflare", "..."}
        }
    """
    block: dict[str, Any] = {"environment_id": config.environment_id}
    metadata = {"provider": config.provider.value, **config.metadata}
    block["metadata"] = metadata
    return block


# ---------------------------------------------------------------------------
# Public surface for runtime dispatch
# ---------------------------------------------------------------------------


def prepare_session_payload(
    base_payload: dict[str, Any],
    sandbox_config: SelfHostedSandboxConfig,
    memory_stores: list[str] | None = None,
) -> dict[str, Any]:
    """Validate + augment a managed-agent session-create payload.

    Single entry point that combines:
      * org-key leak guard (process env)
      * memory_stores incompatibility check
      * AWS region warning
      * environment block injection

    Callers should run this on the payload they would otherwise post to
    ``/v1/sessions`` and forward the returned dict unchanged.
    """
    assert_org_key_not_set()
    assert_memory_compatible(sandbox_config, memory_stores)
    warn_on_aws_region()

    out = dict(base_payload)
    out["environment"] = build_environment_block(sandbox_config)
    return out
