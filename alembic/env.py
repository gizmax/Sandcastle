"""Alembic environment configuration for async PostgreSQL."""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from sandcastle.config import settings
from sandcastle.models.db import Base, _build_engine_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
database_url = settings.database_url or _build_engine_url()

# Guard: skip state-changing Alembic commands for SQLite (tables are created via
# create_all). Informational commands still need the environment to load so they
# can inspect metadata and report the local revision.
_command = getattr(getattr(config, "cmd_opts", None), "cmd", None)
_command_name = _command[0].__name__ if isinstance(_command, tuple) and _command else None
if settings.is_local_mode and _command_name in {"upgrade", "downgrade"}:
    print("Skipping Alembic migrations in local mode (SQLite uses create_all).")
    sys.exit(0)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Run migrations with a connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = database_url
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
