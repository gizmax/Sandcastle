"""Tests for the per-workflow stats endpoint (v0.30.3).

Covers:
- GET /api/workflows/stats with empty DB returns [].
- GET /api/workflows/stats aggregates run counts, success rate, avg cost.
- GET /api/workflows/{name}/stats returns single-workflow stats.
- Unknown workflow returns zero-filled stats (not 404 — graceful UI fallback).
- Cache: two consecutive calls reuse the in-process cache.
- Validation: blank name returns 400.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from sandcastle.api import rate_limit as _rl_mod
from sandcastle.api import routes as _routes_mod

_rl_mod.execution_limiter.check = AsyncMock()

from sandcastle.main import app
from sandcastle.models.db import Run, RunStatus, async_session


client = TestClient(app)


async def _seed_run(
    workflow_name: str,
    status: RunStatus,
    cost: float,
    tenant_id: str | None = None,
    started_minutes_ago: int = 5,
) -> None:
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=started_minutes_ago)
    completed = None if status in (RunStatus.RUNNING, RunStatus.QUEUED) else now
    async with async_session() as session:
        session.add(
            Run(
                id=uuid.uuid4(),
                workflow_name=workflow_name,
                status=status,
                input_data={},
                output_data={"ok": True} if status == RunStatus.COMPLETED else None,
                total_cost_usd=cost,
                started_at=started,
                completed_at=completed,
                tenant_id=tenant_id,
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
def _clear_stats_cache():
    """Ensure each test sees a fresh in-process cache."""
    _routes_mod._stats_cache.clear()
    yield
    _routes_mod._stats_cache.clear()


class TestListWorkflowStats:
    def test_empty_response_shape(self):
        """Endpoint returns a 200 with a list (possibly empty)."""
        resp = client.get("/api/workflows/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)

    @pytest.mark.asyncio
    async def test_aggregates_runs(self):
        wf = f"stats-test-{uuid.uuid4().hex[:8]}"
        await _seed_run(wf, RunStatus.COMPLETED, cost=0.10)
        await _seed_run(wf, RunStatus.COMPLETED, cost=0.20)
        await _seed_run(wf, RunStatus.FAILED, cost=0.05)

        resp = client.get("/api/workflows/stats")
        assert resp.status_code == 200
        rows = {row["name"]: row for row in resp.json()["data"]}
        assert wf in rows
        entry = rows[wf]
        assert entry["total_runs"] == 3
        # 2 of 3 completed = 66.7%
        assert entry["success_rate"] == pytest.approx(66.7, abs=0.1)
        # avg(0.10, 0.20, 0.05) ≈ 0.1167
        assert entry["avg_cost_usd"] == pytest.approx(0.1167, abs=0.001)
        assert entry["last_run_status"] in (
            "completed",
            "failed",
            "running",
        )
        assert entry["last_run_ago"] is not None

    def test_cache_reuses_within_ttl(self):
        """Two back-to-back calls should hit the cache (size grows then stays)."""
        _routes_mod._stats_cache.clear()
        client.get("/api/workflows/stats")
        size_after_first = len(_routes_mod._stats_cache)
        client.get("/api/workflows/stats")
        size_after_second = len(_routes_mod._stats_cache)
        assert size_after_first == 1
        assert size_after_second == 1  # same key, still 1 entry


class TestGetWorkflowStats:
    def test_unknown_workflow_returns_zero_filled(self):
        """Graceful: missing workflow → zeros, not 404."""
        resp = client.get(f"/api/workflows/nope-{uuid.uuid4().hex[:6]}/stats")
        assert resp.status_code == 200
        entry = resp.json()["data"]
        assert entry["total_runs"] == 0
        assert entry["success_rate"] == 0.0
        assert entry["last_run_status"] is None
        assert entry["last_run_ago"] is None

    def test_blank_name_returns_400(self):
        resp = client.get("/api/workflows/%20%20/stats")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_single_workflow_aggregation(self):
        wf = f"stats-one-{uuid.uuid4().hex[:8]}"
        await _seed_run(wf, RunStatus.COMPLETED, cost=0.30)
        await _seed_run(wf, RunStatus.COMPLETED, cost=0.30)

        resp = client.get(f"/api/workflows/{wf}/stats")
        assert resp.status_code == 200
        entry = resp.json()["data"]
        assert entry["name"] == wf
        assert entry["total_runs"] == 2
        assert entry["success_rate"] == 100.0
        assert entry["avg_cost_usd"] == pytest.approx(0.30, abs=0.001)
        assert entry["last_run_status"] == "completed"

    def test_cache_per_workflow(self):
        """Per-workflow cache keys are distinct from the bulk listing."""
        _routes_mod._stats_cache.clear()
        client.get("/api/workflows/foo-cache-test/stats")
        client.get("/api/workflows/stats")
        # Two distinct cache keys present
        assert len(_routes_mod._stats_cache) >= 2
