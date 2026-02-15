"""FastAPI application entrypoint for Sandcastle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
    # DB engine is created on import; nothing else needed for startup
    yield
    # Shutdown: dispose engine
    from sandcastle.models.db import engine

    await engine.dispose()
    logger.info("Sandcastle shut down")


app = FastAPI(
    title="Sandcastle",
    description="Production-ready workflow orchestrator built on Sandstorm",
    version="0.1.0",
    lifespan=lifespan,
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
