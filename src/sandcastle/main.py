"""FastAPI application entrypoint for Sandcastle."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from sandcastle.api.routes import router
from sandcastle.config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Sandcastle",
    description="Production-ready AI agent workflow orchestrator built on Sandstorm",
    version="0.1.0",
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
