"""Anthropic MCP tunnels connector for Sandcastle.

Wraps Anthropic's May 19, 2026 "MCP tunnels" research preview that lets
agents reach Model Context Protocol servers on private networks without
exposing those servers to the public internet.

Spec:
- https://claude.com/blog/claude-managed-agents-updates
- https://platform.claude.com/docs/en/agents-and-tools/mcp-tunnels/overview

Architecture: cloudflared sidecar talks outbound-only to an Anthropic-
operated proxy. Three TLS layers: outer mTLS + IP allowlist between
cloudflared and the proxy, inner TLS between the proxy and the MCP
server, and upstream OAuth on each MCP server. Sandcastle's role here is
to assemble the ``mcp_servers`` block that goes into a Messages API
request when a workflow needs to call into a private network.

This module is GATED. The feature requires an Anthropic access request
(https://claude.com/form/claude-managed-agents). We ship the config +
client code so deployments are ready the day access is granted, but the
runtime path raises ``MCPTunnelGatedError`` unless ``SANDCASTLE_MCP_
TUNNELS_ENABLED=1`` is set explicitly.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# Different beta header from the main managed-agents one. Anthropic
# routes MCP-related Messages API features under the existing
# mcp-client-* family.
MCP_TUNNEL_BETA_HEADER = "mcp-client-2025-11-20"

_ENABLE_FLAG = "SANDCASTLE_MCP_TUNNELS_ENABLED"


# ---------------------------------------------------------------------------
# Auth modes
# ---------------------------------------------------------------------------


class TunnelAuthMode(str, Enum):
    """How cloudflared authenticates against the Anthropic proxy.

    WIF (recommended): use Workload Identity Federation, no long-lived
    secrets. Requires ``org:manage_tunnels`` scope on the federated
    token.

    MANUAL: ship a tunnel-token file + customer-managed CA cert. Higher
    operational burden, no IdP dependency.
    """

    WIF = "workload_identity_federation"
    MANUAL = "manual_cert"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MCPTunnelError(Exception):
    """Base class for MCP tunnel errors."""


class MCPTunnelGatedError(MCPTunnelError):
    """Feature is gated and the access flag is not enabled.

    The MCP tunnels preview requires a signed Anthropic access form. We
    refuse to issue requests with the feature beta header until the
    operator opts in by setting SANDCASTLE_MCP_TUNNELS_ENABLED=1.
    """


class MCPTunnelConfigError(MCPTunnelError):
    """Tunnel config is invalid (bad auth combo, missing fields, ...)."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class MCPTunnelServer:
    """One MCP server reachable through the tunnel.

    The ``hostname`` is the Anthropic-assigned subdomain
    (``<subdomain>.<your-tunnel-domain>``). ``auth_token_env`` names the
    env var holding the upstream OAuth bearer; we never accept the token
    inline so it stays out of YAML and git.
    """

    name: str
    hostname: str
    auth_token_env: str = ""
    # Tools to expose. Empty list = all tools the MCP server advertises.
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class MCPTunnelConfig:
    """Tunnel configuration.

    Attached to a workflow via:

    .. code-block:: yaml

        mcp_tunnel:
          tunnel_id: tunnel_abc123
          auth_mode: workload_identity_federation
          servers:
            - name: internal-jira
              hostname: jira.acme-tunnel.anthropic.cloud
              auth_token_env: ACME_JIRA_BEARER
    """

    tunnel_id: str
    auth_mode: TunnelAuthMode = TunnelAuthMode.WIF
    servers: list[MCPTunnelServer] = field(default_factory=list)
    # Path to the cloudflared binary on the host. Override only when the
    # binary is not on PATH.
    cloudflared_path: str = "cloudflared"
    # Manual-mode only: path to the tunnel token file + customer CA cert.
    tunnel_token_file: str | None = None
    ca_cert_file: str | None = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_tunnel_config(config: MCPTunnelConfig) -> None:
    """Cheap, schema-only validation of a parsed tunnel config.

    Catches the common foot-guns (manual mode without cert, no servers,
    bad hostname shape) before the runtime tries to spawn cloudflared.
    """
    if not config.tunnel_id:
        raise MCPTunnelConfigError("tunnel_id must not be empty")

    if not config.servers:
        raise MCPTunnelConfigError(
            "mcp_tunnel.servers must list at least one server entry"
        )

    if config.auth_mode is TunnelAuthMode.MANUAL:
        if not config.tunnel_token_file or not config.ca_cert_file:
            raise MCPTunnelConfigError(
                "manual_cert auth mode requires both tunnel_token_file and "
                "ca_cert_file. Use workload_identity_federation when you can; "
                "it has fewer moving parts and no long-lived secrets."
            )

    seen_names: set[str] = set()
    for srv in config.servers:
        if not srv.name:
            raise MCPTunnelConfigError("each server entry needs a name")
        if srv.name in seen_names:
            raise MCPTunnelConfigError(
                f"duplicate server name {srv.name!r}"
            )
        seen_names.add(srv.name)
        if not srv.hostname or "." not in srv.hostname:
            raise MCPTunnelConfigError(
                f"server {srv.name!r}: hostname must be a fully qualified "
                f"domain like '<subdomain>.<your-tunnel-domain>'"
            )


# ---------------------------------------------------------------------------
# Gating helpers
# ---------------------------------------------------------------------------


def is_enabled(env: dict[str, str] | None = None) -> bool:
    """Return True iff the operator has explicitly enabled the preview."""
    env = env if env is not None else os.environ
    return env.get(_ENABLE_FLAG, "").lower() in ("1", "true", "yes", "on")


def assert_enabled(env: dict[str, str] | None = None) -> None:
    """Raise if the preview gate is closed.

    Use at every runtime entry point that issues the tunnel beta header.
    """
    if not is_enabled(env):
        raise MCPTunnelGatedError(
            "MCP tunnels are a gated Anthropic research preview. To enable "
            f"in Sandcastle, set {_ENABLE_FLAG}=1 after you receive access "
            "from https://claude.com/form/claude-managed-agents."
        )


# ---------------------------------------------------------------------------
# Messages API payload helper
# ---------------------------------------------------------------------------


def build_mcp_servers_block(
    config: MCPTunnelConfig,
    *,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Produce the ``mcp_servers`` array Anthropic's Messages API expects.

    Resolves each ``auth_token_env`` against the current environment. We
    never emit the bearer value into the returned dict if the env var is
    unset - the upstream API will return 401 in that case, which is the
    behaviour callers want (versus emitting an empty Bearer header).
    """
    env = env if env is not None else os.environ
    blocks: list[dict[str, Any]] = []
    for srv in config.servers:
        block: dict[str, Any] = {
            "name": srv.name,
            "type": "url",
            "url": f"https://{srv.hostname}",
        }
        if srv.auth_token_env:
            token = env.get(srv.auth_token_env, "")
            if token:
                block["authorization_token"] = token
            else:
                logger.warning(
                    "mcp_tunnel: server %s declared auth_token_env=%s but "
                    "the env var is empty; upstream call will likely 401",
                    srv.name,
                    srv.auth_token_env,
                )
        if srv.allowed_tools:
            block["tool_configuration"] = {
                "enabled": True,
                "allowed_tools": list(srv.allowed_tools),
            }
        blocks.append(block)
    return blocks


def build_request_headers(api_key: str) -> dict[str, str]:
    """Header set for a Messages API request that uses MCP tunnels.

    Distinct from the main ``managed-agents-2026-04-01`` beta - tunnels
    ride the existing ``mcp-client`` beta family.
    """
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": MCP_TUNNEL_BETA_HEADER,
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# cloudflared command builder (the sidecar runner)
# ---------------------------------------------------------------------------


def build_cloudflared_args(config: MCPTunnelConfig) -> list[str]:
    """Construct argv for the cloudflared subprocess that serves the tunnel.

    Splits by auth mode. We only assemble the argv here; spawning is
    deferred to a thin runner that the Helm/Compose deployments already
    expect. Keeps this module pure-functional and trivially testable.
    """
    args = [config.cloudflared_path, "tunnel", "run"]
    if config.auth_mode is TunnelAuthMode.WIF:
        args += ["--protocol", "http2", "--auth", "wif"]
    else:
        if not config.tunnel_token_file or not config.ca_cert_file:
            raise MCPTunnelConfigError(
                "manual_cert mode missing token/cert files"
            )
        args += [
            "--token-file",
            config.tunnel_token_file,
            "--origin-ca-pool",
            config.ca_cert_file,
        ]
    args.append(config.tunnel_id)
    return args


__all__ = [
    "MCP_TUNNEL_BETA_HEADER",
    "MCPTunnelConfig",
    "MCPTunnelConfigError",
    "MCPTunnelError",
    "MCPTunnelGatedError",
    "MCPTunnelServer",
    "TunnelAuthMode",
    "assert_enabled",
    "build_cloudflared_args",
    "build_mcp_servers_block",
    "build_request_headers",
    "is_enabled",
    "validate_tunnel_config",
]
