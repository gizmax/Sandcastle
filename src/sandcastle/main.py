"""FastAPI application entrypoint for Sandcastle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

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
    from sqlalchemy import select as sa_select

    from sandcastle.models.db import Setting, async_session

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

    # Start the cron scheduler
    from sandcastle.queue.scheduler import restore_schedules, start_scheduler

    await start_scheduler()
    await restore_schedules()

    yield

    # Shutdown
    from sandcastle.models.db import engine
    from sandcastle.queue.scheduler import stop_scheduler

    await stop_scheduler()
    await engine.dispose()
    logger.info("Sandcastle shut down")


app = FastAPI(
    title="Sandcastle",
    description="Production-ready workflow orchestrator built on Sandstorm",
    version="0.5.0",
    lifespan=lifespan,
)

# Auth (added first = inner middleware)
app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

# CORS (added second = outer middleware, wraps everything including auth)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.dashboard_origin,
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5177",
        "http://localhost:5178",
        "http://localhost:5179",
        "http://localhost:5180",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "sandcastle.main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
    )
