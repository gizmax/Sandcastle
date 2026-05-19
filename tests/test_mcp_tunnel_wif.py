"""Tests for the WIF token-exchange helper used by MCP tunnels."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from sandcastle.engine.mcp_tunnel import (
    MCPTunnelConfig,
    MCPTunnelServer,
    TunnelAuthMode,
)
from sandcastle.engine.mcp_tunnel_wif import (
    WIFExchangeRejectedError,
    WIFSubjectTokenMissingError,
    WIFTokenExchangeClient,
    WIFTokenExchangeError,
    assemble_cloudflared_env,
)


# ---------------------------------------------------------------------------
# Helpers: httpx MockTransport-based STS mock
# ---------------------------------------------------------------------------


def _make_sts_handler(
    *,
    access_token: str = "tunnel-tok-abc",
    expires_in: int = 3600,
    ca_cert: str = "-----BEGIN CERT-----\nFAKE\n-----END CERT-----",
    status_code: int = 200,
    body_override: dict | None = None,
    call_log: list[httpx.Request] | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if call_log is not None:
            call_log.append(request)
        if body_override is not None:
            return httpx.Response(status_code, json=body_override)
        if status_code >= 400:
            return httpx.Response(status_code, text="upstream rejection")
        return httpx.Response(
            status_code,
            json={
                "access_token": access_token,
                "expires_in": expires_in,
                "ca_certificate": ca_cert,
                "token_type": "Bearer",
            },
        )

    return handler


def _make_client(
    tmp_path: Path,
    *,
    sa_token: str = "eyJhbGciOiJSUzI1NiJ9.payload.sig",
    handler=None,
) -> WIFTokenExchangeClient:
    token_path = tmp_path / "sa-token"
    token_path.write_text(sa_token, encoding="utf-8")
    if handler is None:
        handler = _make_sts_handler()
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return WIFTokenExchangeClient(
        oidc_issuer="https://kubernetes.default.svc",
        audience="https://sts.anthropic.com",
        sa_token_path=str(token_path),
        sts_url="https://sts.anthropic.com/v1/token",
        http_client=http_client,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_happy_path_returns_three_keys(tmp_path):
    client = _make_client(tmp_path)
    out = await client.exchange_token()
    assert set(out.keys()) == {"tunnel_token", "ca_cert", "expires_at"}
    assert out["tunnel_token"] == "tunnel-tok-abc"
    assert "BEGIN CERT" in out["ca_cert"]
    # ISO-8601 with timezone offset.
    assert "T" in out["expires_at"]
    assert out["expires_at"].endswith("+00:00")


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_skips_network(tmp_path):
    calls: list[httpx.Request] = []
    handler = _make_sts_handler(call_log=calls, expires_in=3600)
    client = _make_client(tmp_path, handler=handler)
    await client.exchange_token()
    await client.exchange_token()
    await client.exchange_token()
    assert len(calls) == 1  # second + third served from cache


@pytest.mark.asyncio
async def test_cache_miss_after_expiry_reexchanges(tmp_path):
    calls: list[httpx.Request] = []
    # 5s TTL: well below the 60s safety skew, so every call is "expired"
    # and we re-hit the STS.
    handler = _make_sts_handler(call_log=calls, expires_in=5)
    client = _make_client(tmp_path, handler=handler)
    await client.exchange_token()
    await client.exchange_token()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_invalidate_cache_forces_reexchange(tmp_path):
    calls: list[httpx.Request] = []
    handler = _make_sts_handler(call_log=calls, expires_in=3600)
    client = _make_client(tmp_path, handler=handler)
    await client.exchange_token()
    client.invalidate_cache()
    await client.exchange_token()
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_sa_token_path_raises_typed_error(tmp_path):
    bogus = tmp_path / "does-not-exist"
    transport = httpx.MockTransport(_make_sts_handler())
    client = WIFTokenExchangeClient(
        oidc_issuer="iss",
        audience="aud",
        sa_token_path=str(bogus),
        http_client=httpx.AsyncClient(transport=transport),
    )
    with pytest.raises(WIFSubjectTokenMissingError, match="does not exist"):
        await client.exchange_token()


@pytest.mark.asyncio
async def test_empty_sa_token_raises(tmp_path):
    client = _make_client(tmp_path, sa_token="   \n  ")
    with pytest.raises(WIFSubjectTokenMissingError, match="empty"):
        await client.exchange_token()


@pytest.mark.asyncio
async def test_sts_401_surfaces_useful_error(tmp_path):
    handler = _make_sts_handler(status_code=401)
    client = _make_client(tmp_path, handler=handler)
    with pytest.raises(WIFExchangeRejectedError) as excinfo:
        await client.exchange_token()
    assert excinfo.value.status_code == 401
    assert "upstream rejection" in str(excinfo.value)


@pytest.mark.asyncio
async def test_sts_malformed_response_raises(tmp_path):
    # Missing the required fields entirely.
    handler = _make_sts_handler(body_override={"foo": "bar"})
    client = _make_client(tmp_path, handler=handler)
    with pytest.raises(WIFTokenExchangeError, match="missing required fields"):
        await client.exchange_token()


# ---------------------------------------------------------------------------
# Manual mode + cloudflared env assembly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_mode_reads_static_files_without_calling_sts(tmp_path):
    # Build a manual-mode config + ensure no STS call is needed at all
    # (we pass wif_client=None to prove that).
    tok = tmp_path / "tunnel.token"
    tok.write_text("manual-token-xyz", encoding="utf-8")
    cert = tmp_path / "ca.pem"
    cert.write_text("CA-CERT-CONTENTS", encoding="utf-8")
    cfg = MCPTunnelConfig(
        tunnel_id="t1",
        auth_mode=TunnelAuthMode.MANUAL,
        tunnel_token_file=str(tok),
        ca_cert_file=str(cert),
        servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
    )
    env = await assemble_cloudflared_env(cfg, wif_client=None)
    assert env["TUNNEL_TOKEN"] == "manual-token-xyz"
    assert env["TUNNEL_CA_CERT_PATH"].endswith("ca.pem")
    assert env["TUNNEL_ID"] == "t1"


@pytest.mark.asyncio
async def test_wif_assemble_cloudflared_env_passes_correct_env_vars(tmp_path):
    client = _make_client(tmp_path)
    cfg = MCPTunnelConfig(
        tunnel_id="tunnel_acme",
        auth_mode=TunnelAuthMode.WIF,
        servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
    )
    ca_out = tmp_path / "ca-out.pem"
    env = await assemble_cloudflared_env(
        cfg, wif_client=client, ca_cert_out_path=str(ca_out)
    )
    assert env["TUNNEL_ID"] == "tunnel_acme"
    assert env["TUNNEL_TOKEN"] == "tunnel-tok-abc"
    assert env["TUNNEL_CA_CERT_PATH"] == str(ca_out)
    assert "BEGIN CERT" in ca_out.read_text(encoding="utf-8")
    assert "T" in env["TUNNEL_TOKEN_EXPIRES_AT"]


@pytest.mark.asyncio
async def test_wif_mode_without_client_raises(tmp_path):
    cfg = MCPTunnelConfig(
        tunnel_id="t",
        auth_mode=TunnelAuthMode.WIF,
        servers=[MCPTunnelServer(name="x", hostname="x.example.com")],
    )
    with pytest.raises(WIFTokenExchangeError, match="WIFTokenExchangeClient"):
        await assemble_cloudflared_env(cfg, wif_client=None)


# ---------------------------------------------------------------------------
# Cache freshness boundary
# ---------------------------------------------------------------------------


def test_cached_exchange_freshness_respects_skew(monkeypatch):
    from sandcastle.engine.mcp_tunnel_wif import _CachedExchange

    now = time.time()
    # expires in 30s; safety skew is 60s, so this is already stale.
    stale = _CachedExchange(
        tunnel_token="x", ca_cert="y", expires_at_epoch=now + 30
    )
    assert stale.is_fresh(now=now) is False

    fresh = _CachedExchange(
        tunnel_token="x", ca_cert="y", expires_at_epoch=now + 3600
    )
    assert fresh.is_fresh(now=now) is True
