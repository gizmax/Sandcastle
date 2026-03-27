"""API key authentication middleware and tenant helpers."""

from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
import os
import secrets
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import select
from starlette.responses import JSONResponse

from sandcastle.config import settings
from sandcastle.models.db import ApiKey, async_session

logger = logging.getLogger(__name__)

# Public endpoints that don't require authentication.
# NOTE: /.well-known/agent.json is public per the A2A discovery spec.
# /a2a is NOT public - it requires auth when AUTH_REQUIRED=true.
# /api/agui is NOT public - it requires auth when AUTH_REQUIRED=true.
PUBLIC_PATHS = {
    "/api/health", "/api/docs", "/api/openapi.json",
    "/api/redoc", "/.well-known/agent.json",
}

# Path prefixes that don't require authentication
PUBLIC_PREFIXES = ("/api/templates",)

# Path suffixes that don't require authentication (e.g. workflow API spec)
PUBLIC_SUFFIXES = ("/spec",)

# Pepper for HMAC key hashing - falls back to a stable default for dev/local mode.
# In production, set API_KEY_PEPPER as an environment variable.
_API_KEY_PEPPER = os.getenv("API_KEY_PEPPER", "sandcastle-default-pepper-change-in-production")

if _API_KEY_PEPPER == "sandcastle-default-pepper-change-in-production":
    # Only warn if auth is actually enabled (avoids noise in local/dev mode)
    import logging as _auth_logging

    _auth_logging.getLogger(__name__).debug(
        "Using default API_KEY_PEPPER. Set API_KEY_PEPPER env var for production."
    )


def hash_key(key: str) -> str:
    """Hash an API key with HMAC-SHA256 using a server-side pepper."""
    return _hmac.new(
        _API_KEY_PEPPER.encode("utf-8"),
        key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_api_key() -> str:
    """Generate a new random API key."""
    return f"sc_{secrets.token_urlsafe(32)}"


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Return a JSON error response matching the ApiResponse schema."""
    return JSONResponse(
        status_code=status_code,
        content={"data": None, "error": {"code": code, "message": message}},
    )


async def auth_middleware(request: Request, call_next):
    """Authenticate requests via X-API-Key or Authorization header.

    Returns JSONResponse directly instead of raising HTTPException,
    since BaseHTTPMiddleware swallows HTTPException as 500.
    """
    # Skip auth if not required
    if not settings.auth_required:
        request.state.tenant_id = None
        request.state.api_key_id = None
        request.state.allowed_workflows = None
        request.state._auth_checked = True
        return await call_next(request)

    # Skip auth for non-API paths (dashboard, static files), EXCEPT for
    # protocol endpoints that require auth (/a2a JSON-RPC endpoint).
    # /.well-known/agent.json is handled by PUBLIC_PATHS above.
    _AUTH_REQUIRED_NON_API_PATHS = {"/a2a"}
    if (
        not request.url.path.startswith("/api")
        and request.url.path not in _AUTH_REQUIRED_NON_API_PATHS
    ):
        request.state._auth_checked = True
        return await call_next(request)

    # Skip auth for public API paths
    if request.url.path in PUBLIC_PATHS:
        request.state._auth_checked = True
        return await call_next(request)

    # Skip auth for public path prefixes (e.g. /api/templates)
    if any(request.url.path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        request.state._auth_checked = True
        return await call_next(request)

    # Skip auth for public path suffixes on /api/v1/ routes (e.g. workflow API spec)
    if "/api/v1/" in request.url.path and any(
        request.url.path.endswith(suffix) for suffix in PUBLIC_SUFFIXES
    ):
        request.state._auth_checked = True
        return await call_next(request)

    # Extract API key from header
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]

    # Fallback: accept token as query parameter ONLY for SSE/EventSource
    # endpoints which do not support custom headers. Restricted to stream
    # paths to avoid leaking keys in URL logs/history/referrer.
    if not api_key:
        path = request.url.path
        if path.endswith("/stream") or "/stream/" in path or path.startswith("/api/agui/"):
            api_key = request.query_params.get("token")

    # Strip whitespace to handle "Bearer  key" (double space) or padded headers
    if api_key:
        api_key = api_key.strip()

    if not api_key:
        return _error_response(401, "UNAUTHORIZED", "API key required")

    # Verify key using constant-time comparison to prevent timing attacks.
    # We narrow candidates by key_prefix (first 8 chars of the raw key) for
    # efficiency, then use hmac.compare_digest for the final hash check.
    key_hash = hash_key(api_key)
    key_prefix = api_key[:8]
    try:
        async with async_session() as session:
            stmt = select(ApiKey).where(
                ApiKey.key_prefix == key_prefix, ApiKey.is_active.is_(True)
            )
            result = await session.execute(stmt)
            candidates = result.scalars().all()
    except Exception as e:
        # Log only exception type, not message (may contain DB connection string)
        logger.error("Auth DB error: %s", type(e).__name__)
        return _error_response(503, "SERVICE_UNAVAILABLE", "Authentication service unavailable")

    # Constant-time comparison of the full HMAC-SHA256 hash
    db_key = None
    for candidate in candidates:
        if _hmac.compare_digest(candidate.key_hash, key_hash):
            db_key = candidate
            break

    if not db_key:
        return _error_response(401, "UNAUTHORIZED", "Invalid API key")

    # Check key expiry
    if db_key.expires_at:
        expires = db_key.expires_at
        # SQLite may return naive datetimes - treat as UTC for comparison
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            return _error_response(401, "KEY_EXPIRED", "API key has expired")

    # Check IP allowlist
    if db_key.allowed_cidrs:
        import ipaddress

        client_ip = request.client.host if request.client else None
        if not client_ip:
            # Deny access when client IP cannot be determined but allowlist is set
            return _error_response(403, "IP_BLOCKED", "Cannot determine client IP address")
        try:
            addr = ipaddress.ip_address(client_ip)
            allowed = any(
                addr in ipaddress.ip_network(cidr, strict=False)
                for cidr in db_key.allowed_cidrs
            )
            if not allowed:
                return _error_response(403, "IP_BLOCKED", "IP address not in allowlist")
        except ValueError:
            return _error_response(403, "IP_BLOCKED", "Invalid client IP address")

    # Set tenant context on request
    request.state.tenant_id = db_key.tenant_id
    request.state.api_key_id = db_key.id
    request.state.allowed_workflows = db_key.allowed_workflows
    request.state._auth_checked = True

    # Update last_used_at
    try:
        async with async_session() as session:
            db_key_update = await session.get(ApiKey, db_key.id)
            if db_key_update:
                db_key_update.last_used_at = datetime.now(timezone.utc)
                await session.commit()
    except Exception:
        pass  # Non-critical

    return await call_next(request)


_UNSET = object()  # sentinel for missing tenant_id


def get_tenant_id(request: Request) -> str | None:
    """Extract tenant_id from request state (set by auth middleware).

    When auth is enabled, all tenant-scoped queries must use this to filter data.
    Raises RuntimeError if auth middleware did not run (defense-in-depth).
    """
    if settings.auth_required and not getattr(request.state, "_auth_checked", False):
        logger.error(
            "get_tenant_id called but auth middleware did not run for %s",
            request.url.path,
        )
        raise RuntimeError("Auth middleware did not run - potential security bypass")
    return getattr(request.state, "tenant_id", None)


def is_admin(request: Request) -> bool:
    """Return True when the request comes from an admin (no tenant scope).

    Admin = auth is enabled AND the API key has no tenant_id (server operator).
    When auth is disabled everyone is treated as admin.
    """
    if not settings.auth_required:
        return True
    return get_tenant_id(request) is None
