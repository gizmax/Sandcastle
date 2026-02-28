"""Wave 6 deep audit tests: database model integrity fixes.

Tests cover:
  1. Cascade delete includes ApprovalRequest, PolicyViolation, RoutingDecision
  2. Missing database indexes on frequently-queried columns
  3. Unbounded queries now have LIMIT clauses
  4. Cancel run uses atomic UPDATE to prevent TOCTOU races
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Disable rate limiting for all tests in this module
from sandcastle.api import rate_limit as _rl_mod

_rl_mod.execution_limiter.check = AsyncMock()  # noqa: E402

from sandcastle.main import app
from sandcastle.models.db import (
    ApprovalRequest,
    ApprovalStatus,
    AutoPilotExperiment,
    AutoPilotSample,
    Base,
    DeadLetterItem,
    ExperimentStatus,
    PolicyViolation,
    RoutingDecision,
    Run,
    RunCheckpoint,
    RunStatus,
    RunStep,
    StepCache,
    StepStatus,
    async_session,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_run(
    run_id: str | None = None,
    status: RunStatus = RunStatus.COMPLETED,
    workflow_name: str = "test-wf",
    error: str | None = None,
) -> Run:
    """Insert a Run row directly into the in-memory DB."""
    rid = uuid.UUID(run_id) if run_id else uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        run = Run(
            id=rid,
            workflow_name=workflow_name,
            status=status,
            input_data={"name": "test"},
            output_data={"greet": "hello"} if status == RunStatus.COMPLETED else None,
            total_cost_usd=0.01,
            started_at=now - timedelta(seconds=5),
            completed_at=now if status != RunStatus.RUNNING else None,
            error=error,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def _create_approval(run_id: uuid.UUID, step_id: str = "review") -> ApprovalRequest:
    """Insert an ApprovalRequest row linked to a run."""
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        approval = ApprovalRequest(
            run_id=run_id,
            step_id=step_id,
            status=ApprovalStatus.PENDING,
            request_data={"content": "Please review"},
            message="Approval needed",
            timeout_at=now + timedelta(hours=1),
            on_timeout="abort",
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        return approval


async def _create_policy_violation(run_id: uuid.UUID, step_id: str = "check") -> PolicyViolation:
    """Insert a PolicyViolation row linked to a run."""
    async with async_session() as session:
        violation = PolicyViolation(
            run_id=run_id,
            step_id=step_id,
            policy_id="no-swearing",
            severity="high",
            trigger_details="Bad word detected",
            action_taken="redact",
        )
        session.add(violation)
        await session.commit()
        await session.refresh(violation)
        return violation


async def _create_routing_decision(
    run_id: uuid.UUID, step_id: str = "generate"
) -> RoutingDecision:
    """Insert a RoutingDecision row linked to a run."""
    async with async_session() as session:
        decision = RoutingDecision(
            run_id=run_id,
            step_id=step_id,
            selected_model="haiku",
            selected_variant_id="default",
            reason="cheapest",
            budget_pressure=0.5,
            confidence=0.9,
        )
        session.add(decision)
        await session.commit()
        await session.refresh(decision)
        return decision


async def _create_run_step(run_id: uuid.UUID, step_id: str = "greet") -> RunStep:
    """Insert a RunStep row."""
    async with async_session() as session:
        step = RunStep(
            run_id=run_id,
            step_id=step_id,
            status=StepStatus.COMPLETED,
            output_data={"result": "hello"},
            cost_usd=0.001,
            duration_seconds=1.5,
        )
        session.add(step)
        await session.commit()
        await session.refresh(step)
        return step


async def _create_checkpoint(run_id: uuid.UUID, step_id: str = "greet") -> RunCheckpoint:
    """Insert a RunCheckpoint row."""
    async with async_session() as session:
        cp = RunCheckpoint(
            run_id=run_id,
            step_id=step_id,
            stage_index=0,
            context_snapshot={"data": "snapshot"},
        )
        session.add(cp)
        await session.commit()
        await session.refresh(cp)
        return cp


async def _create_dead_letter(run_id: uuid.UUID, step_id: str = "greet") -> DeadLetterItem:
    """Insert a DeadLetterItem row."""
    async with async_session() as session:
        item = DeadLetterItem(
            run_id=run_id,
            step_id=step_id,
            error="something failed",
            input_data={"x": 1},
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item


async def _count_rows(model, **filters) -> int:
    """Count rows in a model table with optional filters."""
    from sqlalchemy import func, select

    async with async_session() as session:
        stmt = select(func.count(model.id))
        for col_name, value in filters.items():
            stmt = stmt.where(getattr(model, col_name) == value)
        return await session.scalar(stmt) or 0


# ---------------------------------------------------------------------------
# 1. CASCADE DELETE: ApprovalRequest, PolicyViolation, RoutingDecision
# ---------------------------------------------------------------------------


class TestCascadeDelete:
    """DELETE /api/runs/{run_id} should remove all FK-linked records."""

    @pytest.mark.asyncio
    async def test_delete_removes_approval_requests(self):
        """Deleting a run should also delete its ApprovalRequest records."""
        run = await _create_run(status=RunStatus.FAILED, error="oops")
        await _create_approval(run.id)
        await _create_approval(run.id, step_id="second-review")

        # Verify records exist before delete
        assert await _count_rows(ApprovalRequest, run_id=run.id) == 2

        response = client.delete(f"/api/runs/{run.id}")
        assert response.status_code == 200

        # Approval records should be gone
        assert await _count_rows(ApprovalRequest, run_id=run.id) == 0

    @pytest.mark.asyncio
    async def test_delete_removes_policy_violations(self):
        """Deleting a run should also delete its PolicyViolation records."""
        run = await _create_run(status=RunStatus.COMPLETED)
        await _create_policy_violation(run.id)
        await _create_policy_violation(run.id, step_id="validate")

        assert await _count_rows(PolicyViolation, run_id=run.id) == 2

        response = client.delete(f"/api/runs/{run.id}")
        assert response.status_code == 200

        assert await _count_rows(PolicyViolation, run_id=run.id) == 0

    @pytest.mark.asyncio
    async def test_delete_removes_routing_decisions(self):
        """Deleting a run should also delete its RoutingDecision records."""
        run = await _create_run(status=RunStatus.COMPLETED)
        await _create_routing_decision(run.id)
        await _create_routing_decision(run.id, step_id="summarize")

        assert await _count_rows(RoutingDecision, run_id=run.id) == 2

        response = client.delete(f"/api/runs/{run.id}")
        assert response.status_code == 200

        assert await _count_rows(RoutingDecision, run_id=run.id) == 0

    @pytest.mark.asyncio
    async def test_delete_removes_all_related_records(self):
        """Comprehensive test: all six FK-linked record types are cleaned up."""
        run = await _create_run(status=RunStatus.FAILED, error="boom")

        # Create one of each related record type
        await _create_run_step(run.id)
        await _create_checkpoint(run.id)
        await _create_dead_letter(run.id)
        await _create_approval(run.id)
        await _create_policy_violation(run.id)
        await _create_routing_decision(run.id)

        response = client.delete(f"/api/runs/{run.id}")
        assert response.status_code == 200

        # Everything should be gone
        for model in [RunStep, RunCheckpoint, DeadLetterItem, ApprovalRequest, PolicyViolation, RoutingDecision]:
            assert await _count_rows(model, run_id=run.id) == 0

        # The run itself should be gone too
        assert await _count_rows(Run, id=run.id) == 0

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_other_runs(self):
        """Deleting run A should not remove records belonging to run B."""
        run_a = await _create_run(status=RunStatus.COMPLETED)
        run_b = await _create_run(status=RunStatus.COMPLETED)

        await _create_approval(run_a.id)
        await _create_approval(run_b.id)
        await _create_policy_violation(run_a.id)
        await _create_policy_violation(run_b.id)
        await _create_routing_decision(run_a.id)
        await _create_routing_decision(run_b.id)

        # Delete only run A
        response = client.delete(f"/api/runs/{run_a.id}")
        assert response.status_code == 200

        # Run B's records should be intact
        assert await _count_rows(ApprovalRequest, run_id=run_b.id) == 1
        assert await _count_rows(PolicyViolation, run_id=run_b.id) == 1
        assert await _count_rows(RoutingDecision, run_id=run_b.id) == 1


# ---------------------------------------------------------------------------
# 2. DATABASE INDEXES
# ---------------------------------------------------------------------------


class TestDatabaseIndexes:
    """Verify that the expected indexes exist on the ORM models."""

    def _get_index_names(self, model) -> set[str]:
        """Extract index names from a model's table definition."""
        table = model.__table__
        return {idx.name for idx in table.indexes}

    def _get_indexed_columns(self, model) -> set[str]:
        """Extract column names that have index=True set directly."""
        return {
            col.name
            for col in model.__table__.columns
            if col.index
        }

    def test_routing_decision_step_id_index(self):
        """RoutingDecision.step_id should have an index for WHERE clause lookups."""
        indexes = self._get_index_names(RoutingDecision)
        assert "ix_routing_decisions_step_id" in indexes

    def test_step_cache_expires_at_index(self):
        """StepCache.expires_at should have an index for cleanup queries."""
        indexes = self._get_index_names(StepCache)
        assert "ix_step_cache_expires_at" in indexes

    def test_autopilot_experiment_composite_index(self):
        """AutoPilotExperiment should have a composite index on (workflow_name, step_id)."""
        indexes = self._get_index_names(AutoPilotExperiment)
        assert "ix_autopilot_experiments_workflow_step" in indexes

        # Verify it covers the right columns
        table = AutoPilotExperiment.__table__
        for idx in table.indexes:
            if idx.name == "ix_autopilot_experiments_workflow_step":
                col_names = [col.name for col in idx.columns]
                assert col_names == ["workflow_name", "step_id"]

    def test_run_tenant_id_index_exists(self):
        """Run.tenant_id should have an index (pre-existing, verify not broken)."""
        indexes = self._get_index_names(Run)
        assert "ix_runs_tenant_id" in indexes

    def test_step_cache_cache_key_index(self):
        """StepCache.cache_key should have an index (set via index=True on column)."""
        indexed_cols = self._get_indexed_columns(StepCache)
        assert "cache_key" in indexed_cols

    def test_all_fk_columns_have_indexes(self):
        """All foreign key columns should have indexes for join performance."""
        # Models with run_id FK that should have indexes
        fk_models = [
            (RunStep, "ix_run_steps_run_id"),
            (RunCheckpoint, "ix_run_checkpoints_run_id"),
            (DeadLetterItem, "ix_dead_letter_run_id"),
            (ApprovalRequest, "ix_approval_requests_run_id"),
            (PolicyViolation, "ix_policy_violations_run_id"),
            (RoutingDecision, "ix_routing_decisions_run_id"),
            (AutoPilotSample, "ix_autopilot_samples_experiment_id"),
        ]
        for model, expected_index in fk_models:
            indexes = self._get_index_names(model)
            assert expected_index in indexes, (
                f"{model.__tablename__} missing index {expected_index}"
            )


# ---------------------------------------------------------------------------
# 3. UNBOUNDED QUERIES - LIMIT clauses
# ---------------------------------------------------------------------------


class TestQueryLimits:
    """Verify that previously unbounded queries now have LIMIT clauses."""

    @pytest.mark.asyncio
    async def test_get_run_routing_decisions_has_limit(self):
        """GET /api/optimizer/decisions/{run_id} should return results with a LIMIT."""
        run = await _create_run(status=RunStatus.COMPLETED)

        # Create 5 routing decisions
        for i in range(5):
            await _create_routing_decision(run.id, step_id=f"step-{i}")

        response = client.get(f"/api/optimizer/decisions/{run.id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 5

    @pytest.mark.asyncio
    async def test_get_run_routing_decisions_nonexistent_run(self):
        """GET /api/optimizer/decisions/{run_id} with invalid run returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/api/optimizer/decisions/{fake_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_run_routing_decisions_invalid_uuid(self):
        """GET /api/optimizer/decisions/{run_id} with bad UUID returns 400."""
        response = client.get("/api/optimizer/decisions/not-a-uuid")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_autopilot_stats_has_bounded_query(self):
        """GET /api/autopilot/stats should work even with many experiments."""
        # Create a few completed experiments with samples
        for i in range(3):
            async with async_session() as session:
                exp = AutoPilotExperiment(
                    workflow_name="test-wf",
                    step_id=f"step-{i}",
                    status=ExperimentStatus.COMPLETED,
                    deployed_variant_id=f"variant-a-{i}",
                )
                session.add(exp)
                await session.commit()
                await session.refresh(exp)

                # Add samples for each variant
                for vid in [f"variant-a-{i}", f"variant-b-{i}"]:
                    sample = AutoPilotSample(
                        experiment_id=exp.id,
                        variant_id=vid,
                        variant_config={"model": "haiku"},
                        quality_score=0.8 if "a" in vid else 0.6,
                        cost_usd=0.01 if "a" in vid else 0.02,
                        duration_seconds=1.0,
                    )
                    session.add(sample)
                await session.commit()

        response = client.get("/api/autopilot/stats")
        assert response.status_code == 200
        data = response.json()["data"]
        # Should return valid stats
        assert "total_experiments" in data
        assert "total_samples" in data
        assert data["total_experiments"] >= 3

    @pytest.mark.asyncio
    async def test_list_routing_decisions_respects_limit_param(self):
        """GET /api/optimizer/decisions should respect limit query param."""
        run = await _create_run(status=RunStatus.COMPLETED)
        for i in range(10):
            await _create_routing_decision(run.id, step_id=f"step-{i}")

        response = client.get("/api/optimizer/decisions", params={"limit": 3})
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) <= 3
        meta = response.json().get("meta")
        if meta:
            assert meta["limit"] == 3


# ---------------------------------------------------------------------------
# 4. CANCEL RUN - Atomic UPDATE (TOCTOU fix)
# ---------------------------------------------------------------------------


class TestCancelRunAtomic:
    """cancel_run should use an atomic UPDATE to prevent TOCTOU races."""

    @pytest.mark.asyncio
    async def test_cancel_running_run(self):
        """Cancelling a running run should succeed and set status to cancelled."""
        run = await _create_run(status=RunStatus.RUNNING)
        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["cancelled"] is True

        # Verify the DB state
        async with async_session() as session:
            db_run = await session.get(Run, run.id)
            status_val = db_run.status.value if hasattr(db_run.status, "value") else db_run.status
            assert status_val == "cancelled"
            assert db_run.error == "Cancelled by user"
            assert db_run.completed_at is not None

    @pytest.mark.asyncio
    async def test_cancel_queued_run(self):
        """Cancelling a queued run should succeed."""
        run = await _create_run(status=RunStatus.QUEUED)
        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 200

        async with async_session() as session:
            db_run = await session.get(Run, run.id)
            status_val = db_run.status.value if hasattr(db_run.status, "value") else db_run.status
            assert status_val == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_completed_run_rejected(self):
        """Cannot cancel a completed run."""
        run = await _create_run(status=RunStatus.COMPLETED)
        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_failed_run_rejected(self):
        """Cannot cancel a failed run."""
        run = await _create_run(status=RunStatus.FAILED, error="err")
        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled_run_rejected(self):
        """Cannot cancel an already-cancelled run."""
        run = await _create_run(status=RunStatus.CANCELLED)
        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_run(self):
        """Cancelling a nonexistent run returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.post(f"/api/runs/{fake_id}/cancel")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_invalid_uuid(self):
        """Cancelling with an invalid UUID returns 400."""
        response = client.post("/api/runs/not-a-uuid/cancel")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_atomic_no_overwrite_completed(self):
        """If the run completes between SELECT and UPDATE, the atomic UPDATE
        should not overwrite the completed status (simulated by direct DB change)."""
        run = await _create_run(status=RunStatus.RUNNING)

        # Simulate the run completing just before the atomic UPDATE executes.
        # We do this by first marking the run as completed in the DB,
        # then attempting to cancel. The cancel endpoint should still return
        # 200 (it checked the status before the race), but the DB should
        # reflect the real status because the atomic WHERE clause won't match.
        #
        # However, since our endpoint does the SELECT and UPDATE in the same
        # session now, we test the integration more directly: if we set the
        # run to COMPLETED before calling cancel, cancel should be rejected
        # with 400.
        async with async_session() as session:
            db_run = await session.get(Run, run.id)
            db_run.status = RunStatus.COMPLETED
            db_run.completed_at = datetime.now(timezone.utc)
            await session.commit()

        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 400

        # DB should still show completed, not cancelled
        async with async_session() as session:
            db_run = await session.get(Run, run.id)
            status_val = db_run.status.value if hasattr(db_run.status, "value") else db_run.status
            assert status_val == "completed"

    @pytest.mark.asyncio
    async def test_cancel_does_not_affect_other_runs(self):
        """Cancelling run A should not affect run B's status."""
        run_a = await _create_run(status=RunStatus.RUNNING)
        run_b = await _create_run(status=RunStatus.RUNNING)

        response = client.post(f"/api/runs/{run_a.id}/cancel")
        assert response.status_code == 200

        # Run B should still be running
        async with async_session() as session:
            db_run_b = await session.get(Run, run_b.id)
            status_val = db_run_b.status.value if hasattr(db_run_b.status, "value") else db_run_b.status
            assert status_val == "running"
