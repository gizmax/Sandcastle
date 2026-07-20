"""Regression tests for persistence migration drift and startup recovery."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def _migration_module():
    path = Path(__file__).parents[1] / "alembic/versions/015_persistence_drift.py"
    spec = importlib.util.spec_from_file_location("migration_015", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline_schema(connection) -> None:
    metadata = sa.MetaData()
    for table_name in (
        "runs",
        "api_keys",
        "workflow_versions",
        "autopilot_experiments",
        "run_steps",
    ):
        sa.Table(table_name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    metadata.create_all(connection)


def _run_revision(module, connection, direction: str) -> None:
    operations = Operations(MigrationContext.configure(connection))
    with patch.object(module, "op", operations):
        getattr(module, direction)()


def test_persistence_drift_revision_upgrades_and_downgrades_sqlite(tmp_path):
    """The corrective revision is reversible on a scratch SQLite baseline."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    module = _migration_module()
    new_tables = {
        "step_cache",
        "tool_connections",
        "eval_runs",
        "eval_case_results",
        "golden_datasets",
        "golden_cases",
        "audit_events",
        "hub_submissions",
        "workflow_evolutions",
        "evolution_iterations",
    }

    from sandcastle.models.db import Base

    with engine.begin() as connection:
        _baseline_schema(connection)
        _run_revision(module, connection, "upgrade")

        inspector = sa.inspect(connection)
        assert new_tables <= set(inspector.get_table_names())
        for table_name in new_tables:
            columns = inspector.get_columns(table_name)
            model_table = Base.metadata.tables[table_name]
            assert {column["name"] for column in columns} == set(model_table.columns.keys())
            assert {column["name"] for column in columns if not column["nullable"]} == {
                column.name for column in model_table.columns if not column.nullable
            }
            assert {index["name"] for index in inspector.get_indexes(table_name)} >= {
                index.name for index in model_table.indexes
            }
        foreign_keys = {
            table_name: {
                (foreign_key["constrained_columns"][0], foreign_key["referred_table"])
                for foreign_key in inspector.get_foreign_keys(table_name)
            }
            for table_name in (
                "eval_case_results",
                "golden_cases",
                "audit_events",
                "evolution_iterations",
            )
        }
        assert foreign_keys == {
            "eval_case_results": {("eval_run_id", "eval_runs"), ("run_id", "runs")},
            "golden_cases": {("dataset_id", "golden_datasets")},
            "audit_events": {("run_id", "runs")},
            "evolution_iterations": {("evolution_id", "workflow_evolutions")},
        }
        assert {column["name"] for column in inspector.get_columns("runs")} >= {
            "risk_level",
            "api_key_id",
        }
        assert {column["name"] for column in inspector.get_columns("api_keys")} >= {
            "expires_at",
            "allowed_cidrs",
            "allowed_workflows",
            "rotated_from_id",
        }
        assert {column["name"] for column in inspector.get_columns("workflow_versions")} >= {
            "is_public"
        }
        assert {column["name"] for column in inspector.get_columns("autopilot_experiments")} >= {
            "rollout_stage"
        }
        assert {column["name"] for column in inspector.get_columns("run_steps")} >= {"model"}
        assert "ix_run_steps_model" in {
            index["name"] for index in inspector.get_indexes("run_steps")
        }

        _run_revision(module, connection, "downgrade")
        inspector = sa.inspect(connection)
        assert not (new_tables & set(inspector.get_table_names()))
        assert "risk_level" not in {column["name"] for column in inspector.get_columns("runs")}
        assert "model" not in {column["name"] for column in inspector.get_columns("run_steps")}


def test_persistence_drift_adds_deploying_to_postgresql_enum():
    """The new experiment status is emitted only for PostgreSQL."""
    module = _migration_module()
    operations = MagicMock()
    operations.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    with patch.object(module, "op", operations):
        module._add_deploying_experimentstatus()

    operations.execute.assert_called_once_with(
        "ALTER TYPE experimentstatus ADD VALUE IF NOT EXISTS 'deploying'"
    )


@pytest.mark.asyncio
async def test_startup_cleanup_reenqueues_queued_redis_runs_and_fails_running():
    """An API restart preserves durable Redis work while failing interrupted work."""
    from sandcastle.main import _cleanup_orphaned_runs
    from sandcastle.models.db import Run, RunStatus, async_session
    from sandcastle.queue import worker

    queued_id = uuid.uuid4()
    running_id = uuid.uuid4()
    async with async_session() as session:
        session.add_all(
            [
                Run(id=queued_id, workflow_name="queued-recovery", input_data={}),
                Run(
                    id=running_id,
                    workflow_name="running-recovery",
                    input_data={},
                    status=RunStatus.RUNNING,
                    started_at=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

    with (
        patch.object(worker.settings, "redis_url", "redis://queue.example:6379"),
        patch(
            "sandcastle.api.routes._load_workflow_yaml",
            return_value="name: queued-recovery\nsteps: []",
        ),
        patch("sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock) as enqueue,
    ):
        failed_count, reenqueued_count = await _cleanup_orphaned_runs()

    assert failed_count >= 1
    assert reenqueued_count >= 1
    queued_call = next(call for call in enqueue.await_args_list if call.args[2] == str(queued_id))
    assert queued_call.kwargs["mark_failed_on_error"] is False
    async with async_session() as session:
        queued = await session.get(Run, queued_id)
        running = await session.get(Run, running_id)
        assert queued.status == RunStatus.QUEUED
        assert running.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_startup_cleanup_fails_queued_and_running_runs_in_local_mode():
    """In-process jobs cannot survive an API restart."""
    from sandcastle.main import _cleanup_orphaned_runs
    from sandcastle.models.db import Run, RunStatus, async_session
    from sandcastle.queue import worker

    queued_id = uuid.uuid4()
    running_id = uuid.uuid4()
    async with async_session() as session:
        session.add_all(
            [
                Run(id=queued_id, workflow_name="queued-local", input_data={}),
                Run(
                    id=running_id,
                    workflow_name="running-local",
                    input_data={},
                    status=RunStatus.RUNNING,
                    started_at=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

    with patch.object(worker.settings, "redis_url", ""):
        failed_count, reenqueued_count = await _cleanup_orphaned_runs()

    assert failed_count >= 2
    assert reenqueued_count == 0
    async with async_session() as session:
        queued = await session.get(Run, queued_id)
        running = await session.get(Run, running_id)
        assert queued.status == RunStatus.FAILED
        assert running.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_recover_stuck_runs_leaves_queued_backlog_and_fails_stale_running():
    """Only work whose executor was running at crash time is recovered."""
    from sandcastle.models.db import Run, RunStatus, async_session
    from sandcastle.queue.worker import _recover_stuck_runs

    old_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    queued_id = uuid.uuid4()
    running_id = uuid.uuid4()
    async with async_session() as session:
        session.add_all(
            [
                Run(
                    id=queued_id,
                    workflow_name="backlogged-queue",
                    input_data={},
                    created_at=old_time,
                ),
                Run(
                    id=running_id,
                    workflow_name="stale-running",
                    input_data={},
                    status=RunStatus.RUNNING,
                    started_at=old_time,
                ),
            ]
        )
        await session.commit()

    await _recover_stuck_runs()

    async with async_session() as session:
        queued = await session.get(Run, queued_id)
        running = await session.get(Run, running_id)
        assert queued.status == RunStatus.QUEUED
        assert running.status == RunStatus.FAILED
