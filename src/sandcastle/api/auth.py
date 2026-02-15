"""API key authentication middleware."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select

from sandcastle.config import settings
from sandcastle.models.db import ApiKey, async_session

logger = logging.getLogger(__name__)

# Public endpoints that don't require authentication
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def hash_key(key: str) -> str:
    """Hash an API key with SHA-256."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """Generate a new random API key."""
    return f"sc_{secrets.token_urlsafe(32)}"


async def auth_middleware(request: Request, call_next):
    """Authenticate requests via X-API-Key or Authorization header."""
    # Skip auth if not required
    if not settings.auth_required:
        return await call_next(request)

    # Skip auth for public paths
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    # Skip auth for dashboard static files
    if request.url.path.startswith("/dashboard"):
        return await call_next(request)

    # Extract API key
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]

    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")

    # Verify key
    key_hash = hash_key(api_key)
    async with async_session() as session:
        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
        result = await session.execute(stmt)
        db_key = result.scalar_one_or_none()

    if not db_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Set tenant context on request
    request.state.tenant_id = db_key.tenant_id

    # Update last_used_at
    async with async_session() as session:
        db_key_update = await session.get(ApiKey, db_key.id)
        if db_key_update:
            db_key_update.last_used_at = datetime.now(timezone.utc)
            await session.commit()

    return await call_next(request)
