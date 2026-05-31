"""Shared test fixtures - use in-memory SQLite to avoid polluting production DB.

IMPORTANT: DATABASE_URL is set at module level, BEFORE any sandcastle
module is imported during test collection.
"""

import asyncio
import atexit
import os
import tempfile

# Use a temp-file SQLite for the whole test session (not in-memory). In-memory
# SQLite needs a single StaticPool connection shared across every session; that
# connection is bound to one event loop and serializes all operations, so a step
# that holds a transaction while concurrently writing on the same connection
# (e.g. race-step branches) dead-locks and hangs. A file-backed DB lets each
# session use its own connection (WAL serializes writers), and the schema/data
# persist across connections and loops. The file is unique per process and
# removed at exit.
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".sqlite", prefix="sandcastle-test-")
os.close(_test_db_fd)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_test_db_path}"


@atexit.register
def _cleanup_test_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(_test_db_path + suffix)
        except OSError:
            pass

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
    """Create all DB tables in the file-backed test SQLite database.

    Also registers a ``connect`` listener that runs ``CREATE TABLE/INDEX IF NOT
    EXISTS`` on every new connection. With the file-backed DB the schema already
    persists across connections, so this is just a cheap safety net that
    guarantees the schema exists on whatever connection a session ends up using
    (e.g. if the engine is disposed and a fresh file connection is opened).
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
