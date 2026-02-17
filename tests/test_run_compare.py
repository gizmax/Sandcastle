"""Tests for the Run Compare (Replay Studio) endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from sandcastle.main import app
from sandcastle.models.db import Run, RunStatus, RunStep, StepStatus, async_session

client = TestClient(app)


async def _create_test_run(
    workflow_name: str = "test-wf",
    status: RunStatus = RunStatus.COMPLETED,
    cost: float = 1.0,
    tenant_id: str | None = None,
    parent_run_id: uuid.UUID | None = None,
    steps: list[dict] | None = None,
) -> uuid.UUID:
    """Helper to create a test run with steps in the DB."""
    run_id = uuid.uuid4()
    async with async_session() as session:
        run = Run(
            id=run_id,
            workflow_name=workflow_name,
            status=status,
            total_cost_usd=cost,
            tenant_id=tenant_id,
            parent_run_id=parent_run_id,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        session.add(run)

        for s in (steps or []):
            step = RunStep(
                run_id=run_id,
                step_id=s.get("step_id", "step1"),
                parallel_index=s.get("parallel_index"),
                status=StepStatus(s.get("status", "completed")),
                output_data=s.get("output_data"),
                cost_usd=s.get("cost_usd", 0.1),
                duration_seconds=s.get("duration_seconds", 1.0),
            )
            session.add(step)

        await session.commit()
    return run_id


class TestCompareBasic:
    """Test comparing two runs with the same workflow."""

    def test_compare_two_runs(self):
        import asyncio

        loop = asyncio.new_event_loop()
        steps_a = [{
            "step_id": "greet", "status": "completed",
            "cost_usd": 0.5, "duration_seconds": 5.0,
            "output_data": {"msg": "hello"},
        }]
        steps_b = [{
            "step_id": "greet", "status": "completed",
            "cost_usd": 0.3, "duration_seconds": 3.0,
            "output_data": {"msg": "hi"},
        }]
        run_a = loop.run_until_complete(_create_test_run(steps=steps_a, cost=0.5))
        run_b = loop.run_until_complete(_create_test_run(steps=steps_b, cost=0.3))
        loop.close()

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            mock_settings.is_local_mode = True
            mock_settings.workflows_dir = "/tmp/nonexistent"

            response = client.get(
                f"/api/runs/compare?run_a={run_a}&run_b={run_b}"
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["same_workflow"] is True
        assert len(data["steps"]) == 1
        assert data["steps"][0]["step_id"] == "greet"
        assert data["steps"][0]["presence"] == "both"
        assert data["total_cost_delta"] < 0  # run_b is cheaper


class TestCompareDifferentWorkflows:
    """Test comparing runs from different workflows."""

    def test_same_workflow_false(self):
        import asyncio

        loop = asyncio.new_event_loop()
        run_a = loop.run_until_complete(_create_test_run(workflow_name="wf-a"))
        run_b = loop.run_until_complete(_create_test_run(workflow_name="wf-b"))
        loop.close()

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            mock_settings.is_local_mode = True
            mock_settings.workflows_dir = "/tmp/nonexistent"

            response = client.get(
                f"/api/runs/compare?run_a={run_a}&run_b={run_b}"
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["same_workflow"] is False


class TestCompareStepMatching:
    """Test step matching by step_id + parallel_index."""

    def test_parallel_steps_matched(self):
        import asyncio

        loop = asyncio.new_event_loop()
        steps_a = [
            {"step_id": "analyze", "parallel_index": 0, "status": "completed", "cost_usd": 0.1},
            {"step_id": "analyze", "parallel_index": 1, "status": "completed", "cost_usd": 0.2},
        ]
        steps_b = [
            {"step_id": "analyze", "parallel_index": 0, "status": "completed", "cost_usd": 0.15},
            {"step_id": "analyze", "parallel_index": 1, "status": "failed", "cost_usd": 0.05},
            {"step_id": "analyze", "parallel_index": 2, "status": "completed", "cost_usd": 0.1},
        ]
        run_a = loop.run_until_complete(_create_test_run(steps=steps_a, cost=0.3))
        run_b = loop.run_until_complete(_create_test_run(steps=steps_b, cost=0.3))
        loop.close()

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            mock_settings.is_local_mode = True
            mock_settings.workflows_dir = "/tmp/nonexistent"

            response = client.get(
                f"/api/runs/compare?run_a={run_a}&run_b={run_b}"
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["steps"]) == 3
        # parallel_index=2 should be "only_b"
        only_b = [s for s in data["steps"] if s["presence"] == "only_b"]
        assert len(only_b) == 1
        assert only_b[0]["parallel_index"] == 2


class TestCompareTenantIsolation:
    """Tenant cannot compare runs belonging to another tenant."""

    def test_tenant_isolation(self):
        import asyncio

        loop = asyncio.new_event_loop()
        run_a = loop.run_until_complete(_create_test_run(tenant_id="tenant-a"))
        run_b = loop.run_until_complete(_create_test_run(tenant_id="tenant-b"))
        loop.close()

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = True
            mock_settings.is_local_mode = False
            mock_settings.workflows_dir = "/tmp/nonexistent"

            with patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-a"):
                response = client.get(
                    f"/api/runs/compare?run_a={run_a}&run_b={run_b}"
                )

        # run_b belongs to tenant-b, so should get 404
        assert response.status_code == 404


class TestCompareNotFound:
    """Test 404 for non-existent runs."""

    def test_run_not_found(self):
        fake_id = str(uuid.uuid4())
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            mock_settings.is_local_mode = True
            mock_settings.workflows_dir = "/tmp/nonexistent"

            response = client.get(
                f"/api/runs/compare?run_a={fake_id}&run_b={fake_id}"
            )

        assert response.status_code == 404

    def test_invalid_uuid(self):
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            mock_settings.is_local_mode = True

            response = client.get(
                "/api/runs/compare?run_a=not-a-uuid&run_b=also-not"
            )

        assert response.status_code == 400
