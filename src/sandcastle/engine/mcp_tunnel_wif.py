"""Workload Identity Federation (WIF) token exchange for MCP tunnels.

Companion to ``sandcastle.engine.mcp_tunnel``. The parent module declares
the two supported auth modes (WIF and manual cert) and assembles the
``mcp_servers`` block + cloudflared argv. This module implements the
actual OIDC -> STS token exchange that the WIF mode relies on:

1. A Kubernetes-projected service account token is mounted at a known
   path inside the pod (typical projected-volume mount).
2. We POST that JWT to Anthropic's STS endpoint, presenting it as the
   ``subject_token`` of an OAuth 2.0 token exchange (RFC 8693).
3. The STS responds with a short-lived ``tunnel_token`` plus a CA cert
   that cloudflared uses to verify the inner TLS leg.

We cache the exchanged token in-process until ``expires_at - 60s`` to
avoid hot-pathing the STS on every Messages API call. For manual mode
we skip the network round-trip entirely and read the static files the
operator placed on disk.

The module is deliberately stdlib-friendly (``time``, ``base64``,
``json``) and uses ``httpx.AsyncClient`` for the one network call so it
plays nicely with the rest of the async Sandcastle runtime.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from sandcastle.engine.mcp_tunnel import (
    MCPTunnelConfig,
    MCPTunnelError,
    TunnelAuthMode,
)

logger = logging.getLogger(__name__)


# Default Anthropic STS endpoint. Override via the client constructor for
# staging/tests. The path is the OAuth 2.0 token-exchange grant per RFC
# 8693, hosted under the tunnels control plane.
DEFAULT_STS_URL = "https://sts.anthropic.com/v1/token"

# RFC 8693 grant type and token-type URIs.
GRANT_TYPE_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
SUBJECT_TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"

# Safety margin: refresh `_CACHE_SKEW_SECONDS` before the STS-declared
# expiry so an in-flight request never sees a token expire mid-call.
_CACHE_SKEW_SECONDS = 60


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WIFTokenExchangeError(MCPTunnelError):
    """Base class for WIF-flow failures."""


class WIFSubjectTokenMissingError(WIFTokenExchangeError):
    """The projected SA token is missing or unreadable.

    Most often a mis-mounted projected-volume in the Pod spec, or the
    pod running outside Kubernetes without an alternative token source.
    """


class WIFExchangeRejectedError(WIFTokenExchangeError):
    """The STS responded with a non-2xx (401/403/400 etc.).

    Carries the upstream status + body so operators can diagnose IdP /
    audience / scope mismatches without re-running the request.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"STS rejected token exchange: HTTP {status_code} - {body[:300]}"
        )


# ---------------------------------------------------------------------------
# Cached exchange result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CachedExchange:
    tunnel_token: str
    ca_cert: str
    expires_at_epoch: float  # absolute seconds since epoch (time.time())

    def is_fresh(self, now: float | None = None) -> bool:
        n = now if now is not None else time.time()
        return n < (self.expires_at_epoch - _CACHE_SKEW_SECONDS)

    def to_response_dict(self) -> dict[str, str]:
        return {
            "tunnel_token": self.tunnel_token,
            "ca_cert": self.ca_cert,
            "expires_at": datetime.fromtimestamp(
                self.expires_at_epoch, tz=UTC
            ).isoformat(),
        }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class WIFTokenExchangeClient:
    """OIDC -> Anthropic STS token-exchange client.

    Construct one per process and reuse: the client maintains an in-memory
    cache keyed off ``(audience, sa_token_path)``. Threadsafe enough for
    asyncio (each call re-reads the SA token from disk and only writes
    the cache slot once on success).
    """

    def __init__(
        self,
        oidc_issuer: str,
        audience: str,
        sa_token_path: str,
        *,
        sts_url: str = DEFAULT_STS_URL,
        http_client: httpx.AsyncClient | None = None,
        requested_scope: str = "org:manage_tunnels",
    ) -> None:
        if not oidc_issuer:
            raise ValueError("oidc_issuer must not be empty")
        if not audience:
            raise ValueError("audience must not be empty")
        if not sa_token_path:
            raise ValueError("sa_token_path must not be empty")
        self.oidc_issuer = oidc_issuer
        self.audience = audience
        self.sa_token_path = sa_token_path
        self.sts_url = sts_url
        self.requested_scope = requested_scope
        # Tests can inject a pre-configured AsyncClient (e.g. with a
        # MockTransport). When None, we create a fresh one per exchange.
        self._injected_client = http_client
        self._cache: _CachedExchange | None = None

    # -- public ----------------------------------------------------------

    async def exchange_token(self) -> dict[str, str]:
        """Return a fresh (or cached) tunnel token + CA cert.

        Output shape matches what callers downstream expect, keyed by
        ``tunnel_token``, ``ca_cert``, ``expires_at`` (ISO-8601).
        """
        if self._cache is not None and self._cache.is_fresh():
            logger.debug("WIF cache hit for audience=%s", self.audience)
            return self._cache.to_response_dict()

        subject_token = self._read_subject_token()
        body = {
            "grant_type": GRANT_TYPE_TOKEN_EXCHANGE,
            "subject_token": subject_token,
            "subject_token_type": SUBJECT_TOKEN_TYPE_JWT,
            "audience": self.audience,
            "scope": self.requested_scope,
            "issuer": self.oidc_issuer,
        }
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
        }

        response = await self._post(body, headers)
        if response.status_code >= 400:
            raise WIFExchangeRejectedError(response.status_code, response.text)

        payload = response.json()
        cached = self._parse_sts_response(payload)
        self._cache = cached
        logger.info(
            "WIF token exchanged for audience=%s, expires_at=%s",
            self.audience,
            cached.to_response_dict()["expires_at"],
        )
        return cached.to_response_dict()

    def invalidate_cache(self) -> None:
        """Drop the cached token (useful after a 401 from cloudflared)."""
        self._cache = None

    # -- internals -------------------------------------------------------

    def _read_subject_token(self) -> str:
        p = Path(self.sa_token_path)
        if not p.exists():
            raise WIFSubjectTokenMissingError(
                f"SA token path does not exist: {self.sa_token_path}. "
                "Confirm the Pod has a projected service-account-token volume "
                "mounted at this location."
            )
        try:
            raw = p.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise WIFSubjectTokenMissingError(
                f"failed to read SA token at {self.sa_token_path}: {exc}"
            ) from exc
        if not raw:
            raise WIFSubjectTokenMissingError(
                f"SA token at {self.sa_token_path} is empty"
            )
        return raw

    async def _post(
        self, body: dict[str, str], headers: dict[str, str]
    ) -> httpx.Response:
        if self._injected_client is not None:
            return await self._injected_client.post(
                self.sts_url, data=body, headers=headers
            )
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(self.sts_url, data=body, headers=headers)

    def _parse_sts_response(self, payload: dict[str, Any]) -> _CachedExchange:
        try:
            tunnel_token = payload["access_token"]
            expires_in = int(payload["expires_in"])
            ca_cert = payload["ca_certificate"]
        except (KeyError, TypeError, ValueError) as exc:
            raise WIFTokenExchangeError(
                "STS response missing required fields "
                "(access_token, expires_in, ca_certificate): "
                f"{json.dumps(payload)[:300]}"
            ) from exc
        return _CachedExchange(
            tunnel_token=tunnel_token,
            ca_cert=ca_cert,
            expires_at_epoch=time.time() + expires_in,
        )


# ---------------------------------------------------------------------------
# Cloudflared env helper
# ---------------------------------------------------------------------------


async def assemble_cloudflared_env(
    config: MCPTunnelConfig,
    *,
    wif_client: WIFTokenExchangeClient | None = None,
    ca_cert_out_path: str | None = None,
) -> dict[str, str]:
    """Return the env dict the cloudflared subprocess needs.

    For WIF mode: exchanges (or reuses cached) tunnel token via the STS
    and optionally writes the CA cert to ``ca_cert_out_path`` so the
    sidecar can ``--origin-ca-pool`` at it. The returned env always
    carries ``TUNNEL_TOKEN`` and ``TUNNEL_CA_CERT_PATH``.

    For MANUAL mode: skips the network, reads the static files declared
    on the config, and surfaces the token via env (the cert stays on the
    operator-provided path).
    """
    env: dict[str, str] = {}
    env["TUNNEL_ID"] = config.tunnel_id

    if config.auth_mode is TunnelAuthMode.WIF:
        if wif_client is None:
            raise WIFTokenExchangeError(
                "WIF auth_mode requires a WIFTokenExchangeClient instance"
            )
        exchanged = await wif_client.exchange_token()
        env["TUNNEL_TOKEN"] = exchanged["tunnel_token"]
        ca_path = ca_cert_out_path or "/tmp/sandcastle-tunnel-ca.pem"
        Path(ca_path).write_text(exchanged["ca_cert"], encoding="utf-8")
        env["TUNNEL_CA_CERT_PATH"] = ca_path
        env["TUNNEL_TOKEN_EXPIRES_AT"] = exchanged["expires_at"]
        return env

    # MANUAL mode: read static files in place. Do not call STS.
    if not config.tunnel_token_file or not config.ca_cert_file:
        raise WIFTokenExchangeError(
            "manual_cert auth_mode is missing tunnel_token_file or ca_cert_file"
        )
    token_path = Path(config.tunnel_token_file)
    cert_path = Path(config.ca_cert_file)
    if not token_path.exists():
        raise WIFTokenExchangeError(
            f"manual tunnel_token_file not found: {config.tunnel_token_file}"
        )
    if not cert_path.exists():
        raise WIFTokenExchangeError(
            f"manual ca_cert_file not found: {config.ca_cert_file}"
        )
    env["TUNNEL_TOKEN"] = token_path.read_text(encoding="utf-8").strip()
    env["TUNNEL_CA_CERT_PATH"] = str(cert_path.resolve())
    return env


__all__ = [
    "DEFAULT_STS_URL",
    "GRANT_TYPE_TOKEN_EXCHANGE",
    "SUBJECT_TOKEN_TYPE_JWT",
    "WIFExchangeRejectedError",
    "WIFSubjectTokenMissingError",
    "WIFTokenExchangeClient",
    "WIFTokenExchangeError",
    "assemble_cloudflared_env",
]
