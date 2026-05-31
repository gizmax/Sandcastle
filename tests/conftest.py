"""Shared test fixtures - use in-memory SQLite to avoid polluting production DB.

IMPORTANT: DATABASE_URL is set at module level, BEFORE any sandcastle
module is imported during test collection.
"""

import asyncio
import os

# Force in-memory SQLite for all tests
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import pytest  # noqa: E402


def _run_async(coro):
    """Run a coroutine on a fresh event loop.

    Used by autouse session/module fixtures that run outside any
    pytest-asyncio test - they must not borrow the asyncio.get_event_loop()
    that pytest-asyncio uses for individual tests.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="session", autouse=True)
def _create_test_tables():
    """Create all DB tables in the in-memory SQLite database.

    Also registers a ``connect`` listener that recreates the schema on EVERY
    new connection. The test DB is in-memory SQLite with StaticPool (a single
    shared connection); if that connection is ever disposed, invalidated, or
    recreated for any reason, the replacement connection sees an empty database
    and unrelated later tests fail with ``no such table``. Running
    ``CREATE TABLE IF NOT EXISTS`` on connect guarantees the schema exists on
    whatever connection a session ends up using, healing the wipe at its source
    regardless of cause (dispose, pool invalidation, module reload).
    """
    from sqlalchemy import event
    from sqlalchemy.dialects import sqlite as _sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    from sandcastle.models.db import Base, engine

    # Full schema DDL: tables first, then indexes (incl. UNIQUE indexes from
    # unique=True columns, which CreateTable does not render - omitting them
    # would silently drop uniqueness constraints after a heal).
    _dialect = _sqlite.dialect()
    _create_ddl = []
    for table in Base.metadata.sorted_tables:
        _create_ddl.append(str(CreateTable(table, if_not_exists=True).compile(dialect=_dialect)))
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            _create_ddl.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=_dialect)))

    def _ensure_schema(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            for stmt in _create_ddl:
                cursor.execute(stmt)
        finally:
            cursor.close()

    event.listen(engine.sync_engine, "connect", _ensure_schema)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _run_async(_create())
    yield
    event.remove(engine.sync_engine, "connect", _ensure_schema)


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """Clear in-memory module caches between tests.

    Several routes.py module-level dicts (_hub_cache, _batch_store,
    _stats_cache) plus the rate-limiter window store accumulate state
    that bleeds across unrelated tests. Cheap to reset (microseconds),
    eliminates a whole class of order-dependent failures.
    """
    try:
        from sandcastle.api import rate_limit as _rl
        from sandcastle.api import routes as _routes

        for name in ("_hub_cache", "_batch_store", "_stats_cache"):
            cache = getattr(_routes, name, None)
            if isinstance(cache, dict):
                cache.clear()

        backend = getattr(_rl.execution_limiter, "_backend", None)
        windows = getattr(backend, "_windows", None)
        if hasattr(windows, "clear"):
            windows.clear()
        if hasattr(backend, "_call_count"):
            backend._call_count = 0
    except Exception:
        # Never let cleanup itself break tests
        pass

    # Memory client cache: a cached (real or failed) Mem0 client leaks across
    # tests and is returned by _get_client() before per-test backend mocks can
    # take effect, so a later test's mock is silently bypassed. Reset the graph
    # client flag too.
    try:
        from sandcastle.engine import memory as _mem

        with _mem._clients_lock:
            _mem._clients.clear()
        _mem._graph_client = None
        _mem._graph_client_initialized = False
    except Exception:
        pass

    # OpenTelemetry module tracer: init_otel() sets a process-global tracer that
    # otherwise bleeds into tests asserting telemetry is disabled.
    try:
        from sandcastle.engine import otel as _otel

        _otel._tracer = None
    except Exception:
        pass

    yield
