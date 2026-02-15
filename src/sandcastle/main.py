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
    logger.info("Sandcastle starting up")

    # Start the cron scheduler
    from sandcastle.queue.scheduler import start_scheduler, restore_schedules

    await start_scheduler()
    await restore_schedules()

    yield

    # Shutdown
    from sandcastle.queue.scheduler import stop_scheduler
    from sandcastle.models.db import engine

    await stop_scheduler()
    await engine.dispose()
    logger.info("Sandcastle shut down")


app = FastAPI(
    title="Sandcastle",
    description="Production-ready workflow orchestrator built on Sandstorm",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.dashboard_origin, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth
app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "sandcastle.main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
    )
