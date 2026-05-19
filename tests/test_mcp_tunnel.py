"""Tests for the MCP tunnels connector (v0.33 prep, gated)."""

from __future__ import annotations

import logging

import pytest

from sandcastle.engine.mcp_tunnel import (
    MCP_TUNNEL_BETA_HEADER,
    MCPTunnelConfig,
    MCPTunnelConfigError,
    MCPTunnelGatedError,
    MCPTunnelServer,
    TunnelAuthMode,
    assert_enabled,
    build_cloudflared_args,
    build_mcp_servers_block,
    build_request_headers,
    is_enabled,
    validate_tunnel_config,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_beta_header_is_mcp_client_family():
    # Distinct from managed-agents-2026-04-01 - tunnels ride the
    # existing mcp-client beta header per Anthropic spec.
    assert MCP_TUNNEL_BETA_HEADER == "mcp-client-2025-11-20"


def test_request_headers_contain_beta():
    h = build_request_headers("sk-ant-api03-xxx")
    assert h["anthropic-beta"] == MCP_TUNNEL_BETA_HEADER
    assert h["x-api-key"] == "sk-ant-api03-xxx"
    assert h["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


class TestGating:
    def test_enabled_truthy_values(self):
        for v in ("1", "true", "TRUE", "yes", "Yes", "on"):
            assert is_enabled({"SANDCASTLE_MCP_TUNNELS_ENABLED": v}) is True

    def test_disabled_by_default(self):
        assert is_enabled({}) is False

    def test_unrecognised_value_is_falsy(self):
        assert is_enabled({"SANDCASTLE_MCP_TUNNELS_ENABLED": "maybe"}) is False

    def test_assert_enabled_raises_when_off(self):
        with pytest.raises(MCPTunnelGatedError) as exc:
            assert_enabled({})
        assert "claude.com/form/claude-managed-agents" in str(exc.value)

    def test_assert_enabled_passes_when_on(self):
        assert_enabled({"SANDCASTLE_MCP_TUNNELS_ENABLED": "1"})


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_wif_config_passes(self):
        cfg = MCPTunnelConfig(
            tunnel_id="tunnel_abc",
            auth_mode=TunnelAuthMode.WIF,
            servers=[
                MCPTunnelServer(
                    name="jira",
                    hostname="jira.acme-tunnel.anthropic.cloud",
                )
            ],
        )
        validate_tunnel_config(cfg)  # Must not raise.

    def test_empty_tunnel_id_rejected(self):
        cfg = MCPTunnelConfig(
            tunnel_id="",
            servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
        )
        with pytest.raises(MCPTunnelConfigError, match="tunnel_id"):
            validate_tunnel_config(cfg)

    def test_no_servers_rejected(self):
        with pytest.raises(MCPTunnelConfigError, match="at least one server"):
            validate_tunnel_config(MCPTunnelConfig(tunnel_id="t", servers=[]))

    def test_manual_mode_requires_both_files(self):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            auth_mode=TunnelAuthMode.MANUAL,
            tunnel_token_file="/tmp/token",  # ca_cert_file missing
            servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
        )
        with pytest.raises(MCPTunnelConfigError, match="ca_cert_file"):
            validate_tunnel_config(cfg)

    def test_manual_mode_complete_passes(self):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            auth_mode=TunnelAuthMode.MANUAL,
            tunnel_token_file="/etc/sc/token",
            ca_cert_file="/etc/sc/ca.pem",
            servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
        )
        validate_tunnel_config(cfg)

    def test_duplicate_server_names_rejected(self):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            servers=[
                MCPTunnelServer(name="dup", hostname="a.example.com"),
                MCPTunnelServer(name="dup", hostname="b.example.com"),
            ],
        )
        with pytest.raises(MCPTunnelConfigError, match="duplicate"):
            validate_tunnel_config(cfg)

    def test_bad_hostname_shape_rejected(self):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            servers=[MCPTunnelServer(name="x", hostname="localhost")],
        )
        with pytest.raises(MCPTunnelConfigError, match="fully qualified"):
            validate_tunnel_config(cfg)


# ---------------------------------------------------------------------------
# mcp_servers block builder
# ---------------------------------------------------------------------------


class TestMCPServersBlock:
    def test_basic_block_shape(self):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            servers=[
                MCPTunnelServer(
                    name="jira",
                    hostname="jira.acme.anthropic.cloud",
                )
            ],
        )
        block = build_mcp_servers_block(cfg, env={})
        assert len(block) == 1
        assert block[0]["type"] == "url"
        assert block[0]["url"] == "https://jira.acme.anthropic.cloud"
        assert block[0]["name"] == "jira"

    def test_auth_token_injected_from_env(self):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            servers=[
                MCPTunnelServer(
                    name="jira",
                    hostname="jira.acme.anthropic.cloud",
                    auth_token_env="JIRA_BEARER",
                )
            ],
        )
        block = build_mcp_servers_block(cfg, env={"JIRA_BEARER": "abc123"})
        assert block[0]["authorization_token"] == "abc123"

    def test_missing_token_warns_but_emits_block(self, caplog):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            servers=[
                MCPTunnelServer(
                    name="jira",
                    hostname="jira.acme.anthropic.cloud",
                    auth_token_env="JIRA_BEARER",
                )
            ],
        )
        with caplog.at_level(logging.WARNING):
            block = build_mcp_servers_block(cfg, env={})
        # Block emitted, but no token field -> upstream will 401.
        assert "authorization_token" not in block[0]
        assert any("JIRA_BEARER" in r.message for r in caplog.records)

    def test_allowed_tools_lands_in_tool_configuration(self):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            servers=[
                MCPTunnelServer(
                    name="jira",
                    hostname="jira.acme.anthropic.cloud",
                    allowed_tools=["create_issue", "search"],
                )
            ],
        )
        block = build_mcp_servers_block(cfg, env={})
        tc = block[0]["tool_configuration"]
        assert tc["enabled"] is True
        assert tc["allowed_tools"] == ["create_issue", "search"]


# ---------------------------------------------------------------------------
# cloudflared argv
# ---------------------------------------------------------------------------


class TestCloudflaredArgs:
    def test_wif_args(self):
        cfg = MCPTunnelConfig(
            tunnel_id="tunnel_xyz",
            auth_mode=TunnelAuthMode.WIF,
            servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
        )
        args = build_cloudflared_args(cfg)
        assert args[0] == "cloudflared"
        assert "--auth" in args
        assert args[args.index("--auth") + 1] == "wif"
        assert args[-1] == "tunnel_xyz"

    def test_manual_args_include_token_and_ca(self):
        cfg = MCPTunnelConfig(
            tunnel_id="tunnel_xyz",
            auth_mode=TunnelAuthMode.MANUAL,
            tunnel_token_file="/etc/sc/token",
            ca_cert_file="/etc/sc/ca.pem",
            servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
        )
        args = build_cloudflared_args(cfg)
        assert "--token-file" in args
        assert args[args.index("--token-file") + 1] == "/etc/sc/token"
        assert "--origin-ca-pool" in args
        assert args[args.index("--origin-ca-pool") + 1] == "/etc/sc/ca.pem"

    def test_manual_args_missing_files_raises(self):
        cfg = MCPTunnelConfig(
            tunnel_id="tunnel_xyz",
            auth_mode=TunnelAuthMode.MANUAL,
            servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
        )
        with pytest.raises(MCPTunnelConfigError):
            build_cloudflared_args(cfg)

    def test_custom_cloudflared_path(self):
        cfg = MCPTunnelConfig(
            tunnel_id="t",
            cloudflared_path="/opt/local/bin/cloudflared",
            servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
        )
        args = build_cloudflared_args(cfg)
        assert args[0] == "/opt/local/bin/cloudflared"
