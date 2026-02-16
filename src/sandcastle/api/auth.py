"""API key authentication middleware and tenant helpers."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import select
from starlette.responses import JSONResponse

from sandcastle.config import settings
from sandcastle.models.db import ApiKey, async_session

logger = logging.getLogger(__name__)

# Public endpoints that don't require authentication
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

# Path prefixes that don't require authentication
PUBLIC_PREFIXES = ("/templates",)


def hash_key(key: str) -> str:
    """Hash an API key with SHA-256."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


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
        return await call_next(request)

    # Skip auth for public paths
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    # Skip auth for public path prefixes (e.g. /templates, /templates/{name})
    if any(request.url.path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return await call_next(request)

    # Skip auth for dashboard static files
    if request.url.path.startswith("/dashboard"):
        return await call_next(request)

    # Extract API key from header
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]

    if not api_key:
        return _error_response(401, "UNAUTHORIZED", "API key required")

    # Verify key
    key_hash = hash_key(api_key)
    try:
        async with async_session() as session:
            stmt = select(ApiKey).where(
                ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True)
            )
            result = await session.execute(stmt)
            db_key = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Auth DB error: {e}")
        return _error_response(503, "SERVICE_UNAVAILABLE", "Authentication service unavailable")

    if not db_key:
        return _error_response(401, "UNAUTHORIZED", "Invalid API key")

    # Set tenant context on request
    request.state.tenant_id = db_key.tenant_id

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


def get_tenant_id(request: Request) -> str | None:
    """Extract tenant_id from request state (set by auth middleware).

    When auth is enabled, all tenant-scoped queries must use this to filter data.
    """
    return getattr(request.state, "tenant_id", None)
