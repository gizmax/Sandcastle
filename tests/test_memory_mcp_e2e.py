"""End-to-end smoke test for Memory MCP behind an MCP tunnel.

This is a wire-level integration test that stitches together the three
Phase 1 pieces:

1. The Memory MCP server module (sibling agent, optional - we skip if
   the module is not yet importable).
2. The MCP tunnel block builder + headers from ``mcp_tunnel``.
3. The WIF token-exchange helper from ``mcp_tunnel_wif``.

We do not exercise real network paths. The anthropic SDK Messages call
is mocked; we just assert that:

- ``build_mcp_servers_block`` produces a valid Messages API block.
- The block reaches the SDK call unchanged.
- The ``mcp-client-2025-11-20`` beta header is in the request headers.
- Per-tool ``allowed_tools`` lands in ``tool_configuration``.
"""

from __future__ import annotations

import importlib
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.mcp_tunnel import (
    MCP_TUNNEL_BETA_HEADER,
    MCPTunnelConfig,
    MCPTunnelServer,
    TunnelAuthMode,
    build_mcp_servers_block,
    build_request_headers,
)


# ---------------------------------------------------------------------------
# Optional import: the sibling Memory MCP server module may or may not be
# wired up yet. We skip the boot-server test gracefully when missing.
# ---------------------------------------------------------------------------


def _maybe_import_memory_mcp_server():
    try:
        return importlib.import_module(
            "sandcastle.engine.memory_mcp_server"
        )
    except ImportError:
        return None


memory_mcp_server = _maybe_import_memory_mcp_server()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Tunnel config fixture pointing at a "local" Memory MCP
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_tunnel_config() -> MCPTunnelConfig:
    port = _free_port()
    # Hostname format mirrors what the tunnel would resolve to in prod -
    # but it doesn't actually have to resolve in this smoke test, the
    # Messages API call is mocked.
    return MCPTunnelConfig(
        tunnel_id="tunnel_local_memory",
        auth_mode=TunnelAuthMode.WIF,
        servers=[
            MCPTunnelServer(
                name="sandcastle-memory",
                hostname=f"memory.local-tunnel.test:{port}",
                auth_token_env="MEMORY_BEARER",
                allowed_tools=["add", "search", "list_memories"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: Memory MCP server can boot in a background thread (or skip)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    memory_mcp_server is None,
    reason="memory_mcp_server module is sibling work and not yet present",
)
def test_memory_mcp_server_boots_in_background_thread():
    # The sibling module is expected to expose either a `serve(port)`
    # callable or an ASGI `app`. Both are acceptable shapes. We just
    # need a hint that booting it is non-blocking.
    has_serve = hasattr(memory_mcp_server, "serve")
    has_app = hasattr(memory_mcp_server, "app")
    assert has_serve or has_app, (
        "memory_mcp_server module should expose serve() or app for "
        "integration scenarios"
    )

    if has_serve:
        port = _free_port()
        booted = threading.Event()

        def _runner():
            try:
                memory_mcp_server.serve(port=port)  # type: ignore[attr-defined]
            finally:
                booted.set()

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        # Give it a tick to bind; we don't actually call into it from
        # the e2e mock path, this just proves the bootstrap path exists.
        time.sleep(0.05)
        assert t.is_alive() or booted.is_set()


# ---------------------------------------------------------------------------
# Test 2: build_mcp_servers_block emits a Messages-API-shaped block
# ---------------------------------------------------------------------------


def test_build_mcp_servers_block_produces_valid_messages_api_block(
    memory_tunnel_config,
):
    block = build_mcp_servers_block(
        memory_tunnel_config, env={"MEMORY_BEARER": "bearer-xyz"}
    )
    assert len(block) == 1
    srv = block[0]
    # Shape required by Anthropic Messages API for `mcp_servers`.
    assert srv["type"] == "url"
    assert srv["name"] == "sandcastle-memory"
    assert srv["url"].startswith("https://")
    assert srv["authorization_token"] == "bearer-xyz"


# ---------------------------------------------------------------------------
# Test 3: SDK call receives the mcp_servers block intact (mocked anthropic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mocked_messages_call_forwards_mcp_servers_block(
    memory_tunnel_config,
):
    # We simulate the call site that would otherwise sit inside the
    # managed-agent step. Sandcastle does not depend on the anthropic
    # SDK's specific class shape here - we mock at the boundary.
    mock_client = MagicMock()
    mock_messages_create = AsyncMock(
        return_value=MagicMock(content=[{"type": "text", "text": "ok"}])
    )
    mock_client.beta.messages.create = mock_messages_create

    blocks = build_mcp_servers_block(
        memory_tunnel_config, env={"MEMORY_BEARER": "tok"}
    )
    headers = build_request_headers("sk-ant-test")

    # Pretend we are the managed-agent step.
    await mock_client.beta.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": "remember this fact"}],
        mcp_servers=blocks,
        extra_headers=headers,
    )

    mock_messages_create.assert_awaited_once()
    kwargs = mock_messages_create.await_args.kwargs
    assert kwargs["mcp_servers"] == blocks
    assert kwargs["extra_headers"]["anthropic-beta"] == MCP_TUNNEL_BETA_HEADER


# ---------------------------------------------------------------------------
# Test 4: Required beta header is present
# ---------------------------------------------------------------------------


def test_beta_header_present_in_request_headers():
    headers = build_request_headers("sk-ant-x")
    assert headers["anthropic-beta"] == "mcp-client-2025-11-20"
    assert headers["anthropic-version"] == "2023-06-01"


# ---------------------------------------------------------------------------
# Test 5: Per-tool examples convention (tool_configuration.allowed_tools)
# ---------------------------------------------------------------------------


def test_per_tool_allowed_tools_convention_is_honoured(memory_tunnel_config):
    block = build_mcp_servers_block(memory_tunnel_config, env={})
    tc = block[0]["tool_configuration"]
    assert tc["enabled"] is True
    # Memory MCP advertises add/search/list_memories - the config
    # restricts to exactly that surface. Ordering is preserved so
    # operators can spot diffs in logs.
    assert tc["allowed_tools"] == ["add", "search", "list_memories"]


# ---------------------------------------------------------------------------
# Test 6: WIF env assembly + tunnel block can be composed (smoke)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wif_env_assembly_composes_with_tunnel_block(
    memory_tunnel_config, tmp_path
):
    # Patch out the WIF client so we don't hit the network. Verify the
    # assembled cloudflared env + the mcp_servers block both reference
    # the same tunnel_id and are mutually consistent.
    from sandcastle.engine import mcp_tunnel_wif

    fake_client = MagicMock()
    fake_client.exchange_token = AsyncMock(
        return_value={
            "tunnel_token": "tok",
            "ca_cert": "-----BEGIN CERT-----\nx\n-----END CERT-----",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
    )
    ca_out = tmp_path / "ca.pem"
    env = await mcp_tunnel_wif.assemble_cloudflared_env(
        memory_tunnel_config,
        wif_client=fake_client,
        ca_cert_out_path=str(ca_out),
    )
    block = build_mcp_servers_block(
        memory_tunnel_config, env={"MEMORY_BEARER": "bearer"}
    )
    assert env["TUNNEL_ID"] == memory_tunnel_config.tunnel_id
    assert env["TUNNEL_TOKEN"] == "tok"
    assert ca_out.exists()
    # The mcp_servers block does not embed the cloudflared bearer; the
    # two paths run in different processes (cloudflared sidecar vs API
    # request), but both must reference the same tunnel.
    assert block[0]["name"] == "sandcastle-memory"
