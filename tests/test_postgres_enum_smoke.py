"""Live PostgreSQL smoke coverage for native enum persistence.

The migration CI job supplies ``POSTGRES_MIGRATIONS_DATABASE_URL``. Local test
runs exercise the same row construction with a mocked session instead.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sandcastle.models.db import (
    ApprovalRequest,
    ApprovalStatus,
    AutoPilotExperiment,
    ExperimentStatus,
    Run,
    RunStatus,
    RunStep,
    StepStatus,
    WorkflowVersion,
    WorkflowVersionStatus,
)


async def _write_and_read_enum_rows(session_factory: Callable):
    """Persist representative enum rows and refresh them from the database."""
    run_id = uuid.uuid4()
    workflow_name = f"enum-smoke-{uuid.uuid4()}"
    run = Run(id=run_id, workflow_name=workflow_name, status=RunStatus.QUEUED)
    step = RunStep(
        id=uuid.uuid4(),
        run_id=run_id,
        step_id="step",
        status=StepStatus.PENDING,
    )
    approval = ApprovalRequest(
        id=uuid.uuid4(),
        run_id=run_id,
        step_id="approval",
        status=ApprovalStatus.PENDING,
    )
    experiment = AutoPilotExperiment(
        id=uuid.uuid4(),
        workflow_name=workflow_name,
        step_id="experiment",
        status=ExperimentStatus.DEPLOYING,
    )
    workflow_version = WorkflowVersion(
        id=uuid.uuid4(),
        workflow_name=workflow_name,
        version=1,
        status=WorkflowVersionStatus.DRAFT,
        yaml_content="name: enum-smoke\nsteps: []",
        checksum="0" * 64,
    )
    rows = (run, step, approval, experiment, workflow_version)

    async with session_factory() as session:
        session.add_all(rows)
        await session.commit()
        for row in rows:
            await session.refresh(row)

    return tuple(row.status for row in rows)


@pytest.mark.asyncio
async def test_enum_smoke_helper_exercises_every_enum_backed_model():
    """Keep the CI smoke row set covered when no live PostgreSQL is available."""
    session = MagicMock()
    session.add_all = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    statuses = await _write_and_read_enum_rows(session_factory)

    assert statuses == (
        RunStatus.QUEUED,
        StepStatus.PENDING,
        ApprovalStatus.PENDING,
        ExperimentStatus.DEPLOYING,
        WorkflowVersionStatus.DRAFT,
    )
    assert len(session.add_all.call_args.args[0]) == 5
    assert session.refresh.await_count == 5


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("POSTGRES_MIGRATIONS_DATABASE_URL", "").startswith("postgresql"),
    reason="requires POSTGRES_MIGRATIONS_DATABASE_URL from the PostgreSQL migration job",
)
async def test_postgresql_enum_rows_round_trip():
    """PostgreSQL accepts ORM enum member names and returns their enum values."""
    engine = create_async_engine(os.environ["POSTGRES_MIGRATIONS_DATABASE_URL"])
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        statuses = await _write_and_read_enum_rows(session_factory)
    finally:
        await engine.dispose()

    assert statuses == (
        RunStatus.QUEUED,
        StepStatus.PENDING,
        ApprovalStatus.PENDING,
        ExperimentStatus.DEPLOYING,
        WorkflowVersionStatus.DRAFT,
    )
