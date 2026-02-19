"""FastAPI application entrypoint for Sandcastle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from sandcastle import __version__
from sandcastle.api.auth import auth_middleware
from sandcastle.api.routes import router
from sandcastle.config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle - startup and shutdown hooks."""
    if settings.is_local_mode:
        logger.info(
            "Sandcastle starting in local mode (SQLite + filesystem + in-process queue)"
        )
        # Auto-create tables for SQLite (no Alembic needed)
        from sandcastle.models.db import init_db

        await init_db()
        logger.info("Local database initialized")
    else:
        logger.info(
            "Sandcastle starting in production mode (PostgreSQL + Redis + S3)"
        )

    # Load saved settings from DB
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from sandcastle.models.db import ApiKey, Setting, async_session

    async with async_session() as session:
        result = await session.execute(sa_select(Setting))
        saved = {s.key: s.value for s in result.scalars().all()}

        for key, value in saved.items():
            if hasattr(settings, key):
                field_type = type(getattr(settings, key))
                if field_type is bool:
                    setattr(settings, key, value.lower() in ("true", "1", "yes"))
                elif field_type is int:
                    setattr(settings, key, int(value))
                elif field_type is float:
                    setattr(settings, key, float(value))
                else:
                    setattr(settings, key, value)

        if saved:
            logger.info(f"Loaded {len(saved)} saved settings from database")

    # Clean up stale runs (queued/running) left from previous crash/restart
    from datetime import datetime, timezone

    from sqlalchemy import update as sa_update

    from sandcastle.models.db import Run, RunStatus

    async with async_session() as session:
        # Count first, then update
        count_result = await session.execute(
            sa_select(func.count()).select_from(Run).where(
                Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING])
            )
        )
        orphan_count = count_result.scalar() or 0

        if orphan_count:
            await session.execute(
                sa_update(Run)
                .where(Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
                .values(
                    status=RunStatus.FAILED,
                    error="Server restarted - run was orphaned",
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            logger.info(f"Cleaned up {orphan_count} orphaned runs from previous session")

    # Start the cron scheduler (skip in multi-worker deployments)
    if settings.scheduler_enabled:
        from sandcastle.queue.scheduler import restore_schedules, start_scheduler

        await start_scheduler()
        await restore_schedules()
    else:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")

    # Warn when authentication is disabled
    if not settings.auth_required:
        logger.warning(
            "Authentication is DISABLED. All API endpoints are publicly accessible. "
            "Set AUTH_REQUIRED=true for production deployments."
        )

    # Warn about placeholder credentials
    _placeholders = {"minioadmin", "your-webhook-signing-secret", "sandcastle"}
    _cred_warnings = []
    if settings.webhook_secret in _placeholders:
        _cred_warnings.append("WEBHOOK_SECRET")
    if settings.aws_access_key_id in _placeholders:
        _cred_warnings.append("AWS_ACCESS_KEY_ID")
    if settings.aws_secret_access_key in _placeholders:
        _cred_warnings.append("AWS_SECRET_ACCESS_KEY")
    if _cred_warnings:
        logger.warning(
            "Placeholder credentials detected for: %s. "
            "Set secure values via environment variables for production.",
            ", ".join(_cred_warnings),
        )

    # Bootstrap admin API key from env var (if configured and not yet in DB)
    if settings.admin_api_key:
        from sandcastle.api.auth import hash_key

        admin_hash = hash_key(settings.admin_api_key)
        async with async_session() as session:
            existing = await session.execute(
                sa_select(ApiKey).where(ApiKey.key_hash == admin_hash)
            )
            if not existing.scalar_one_or_none():
                admin_key = ApiKey(
                    key_hash=admin_hash,
                    key_prefix=settings.admin_api_key[:8],
                    tenant_id=None,
                    name="admin (bootstrap)",
                    is_active=True,
                )
                session.add(admin_key)
                await session.commit()
                logger.info("Admin API key bootstrapped from ADMIN_API_KEY env var")

    yield

    # Shutdown
    from sandcastle.models.db import engine

    if settings.scheduler_enabled:
        from sandcastle.queue.scheduler import stop_scheduler

        await stop_scheduler()
    await engine.dispose()
    logger.info("Sandcastle shut down")


app = FastAPI(
    title="Sandcastle",
    description="Production-ready workflow orchestrator built on Sandstorm",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Auth (added first = inner middleware)
app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

# CORS (added second = outer middleware, wraps everything including auth)
_cors_origins = [
    settings.dashboard_origin,
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:5176",
    "http://localhost:5177",
    "http://localhost:5178",
    "http://localhost:5179",
    "http://localhost:5180",
]
# Wildcard + credentials is invalid per CORS spec - filter it out
_cors_origins = [o for o in _cors_origins if o != "*"]
if settings.dashboard_origin == "*":
    logger.warning(
        "DASHBOARD_ORIGIN='*' is invalid with allow_credentials=True. "
        "Set it to your actual dashboard URL."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# ---------------------------------------------------------------------------
# Dashboard static files (served from the same port)
# ---------------------------------------------------------------------------

# Look for pre-built dashboard in known locations
_DASHBOARD_CANDIDATES = [
    Path(__file__).parent.parent.parent / "dashboard" / "dist",  # repo dev
    Path(__file__).parent / "dashboard",                          # installed pkg
]
_dashboard_dir: Path | None = next(
    (p for p in _DASHBOARD_CANDIDATES if (p / "index.html").exists()), None
)

if _dashboard_dir:
    logger.info(f"Serving dashboard from {_dashboard_dir}")
    app.mount("/assets", StaticFiles(directory=_dashboard_dir / "assets"), name="dashboard-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        """Serve dashboard SPA - static files or fallback to index.html."""
        # Don't intercept /api paths - let FastAPI return 404 for unknown API routes
        if path.startswith("api/") or path == "api":
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        file = _dashboard_dir / path
        if file.exists() and file.is_file() and ".." not in path:
            return FileResponse(file)
        return FileResponse(_dashboard_dir / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "sandcastle.main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
    )
