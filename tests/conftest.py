"""Shared test fixtures - use in-memory SQLite to avoid polluting production DB.

IMPORTANT: DATABASE_URL is set at module level, BEFORE any sandcastle
module is imported during test collection.
"""

import asyncio
import os

# Force in-memory SQLite for all tests
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_test_tables():
    """Create all DB tables in the in-memory SQLite database."""
    from sandcastle.models.db import Base, engine

    loop = asyncio.new_event_loop()

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    loop.run_until_complete(_create())
    loop.close()
