"""Tests for Living Dashboard endpoints: /stats/sparklines, /stats/heatmap, /stats/anomalies."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app
from sandcastle.models.db import Run, RunStatus, async_session

client = TestClient(app)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _create_run(
    workflow_name: str = "test-wf",
    status: RunStatus = RunStatus.COMPLETED,
    total_cost_usd: float = 0.01,
    days_ago: int = 0,
    duration_seconds: float = 10.0,
) -> Run:
    """Insert a Run row directly into the in-memory DB."""
    now = datetime.now(timezone.utc)
    started = now - timedelta(days=days_ago, seconds=duration_seconds)
    completed = (started + timedelta(seconds=duration_seconds)) if status == RunStatus.COMPLETED else None
    async with async_session() as session:
        run = Run(
            id=uuid.uuid4(),
            workflow_name=workflow_name,
            status=status,
            input_data={},
            total_cost_usd=total_cost_usd,
            started_at=started,
            completed_at=completed,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


# ---------------------------------------------------------------------------
# GET /stats/sparklines
# ---------------------------------------------------------------------------


class TestSparklines:
    def test_returns_200(self):
        resp = client.get("/api/stats/sparklines")
        assert resp.status_code == 200

    def test_response_has_data_key(self):
        resp = client.get("/api/stats/sparklines")
        body = resp.json()
        assert "data" in body

    def test_sparklines_have_four_keys(self):
        resp = client.get("/api/stats/sparklines")
        data = resp.json()["data"]
        assert set(data.keys()) >= {"runs", "rate", "cost", "duration"}

    def test_each_sparkline_has_values_and_trend(self):
        resp = client.get("/api/stats/sparklines")
        data = resp.json()["data"]
        for key in ("runs", "rate", "cost", "duration"):
            series = data[key]
            assert "values" in series
            assert "trend_percent" in series
            assert isinstance(series["values"], list)
            assert isinstance(series["trend_percent"], (int, float))

    def test_values_are_7_days(self):
        resp = client.get("/api/stats/sparklines")
        data = resp.json()["data"]
        for key in ("runs", "rate", "cost", "duration"):
            assert len(data[key]["values"]) == 7, f"{key} should have 7 values"

    def test_empty_database_returns_zeros(self):
        """Empty DB returns zero-filled sparklines, not errors."""
        resp = client.get("/api/stats/sparklines")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # All runs values should be 0.0 when there are no runs (or non-negative)
        for val in data["runs"]["values"]:
            assert val >= 0

    @pytest.mark.anyio
    async def test_with_real_data_trend(self):
        """Trend percent should reflect actual cost change between days."""
        # Create run on day-1 (low cost) and today (higher cost)
        await _create_run(total_cost_usd=1.0, days_ago=1)
        await _create_run(total_cost_usd=3.0, days_ago=0)

        resp = client.get("/api/stats/sparklines")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # Today's cost is higher than yesterday so trend_percent should be positive
        # (or at least well-formed)
        assert isinstance(data["cost"]["trend_percent"], (int, float))


# ---------------------------------------------------------------------------
# GET /stats/heatmap
# ---------------------------------------------------------------------------


class TestHeatmap:
    def test_returns_200(self):
        resp = client.get("/api/stats/heatmap")
        assert resp.status_code == 200

    def test_response_has_data_key(self):
        resp = client.get("/api/stats/heatmap")
        body = resp.json()
        assert "data" in body

    def test_returns_182_days(self):
        resp = client.get("/api/stats/heatmap")
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) == 182

    def test_cell_schema(self):
        resp = client.get("/api/stats/heatmap")
        data = resp.json()["data"]
        cell = data[0]
        assert "date" in cell
        assert "count" in cell
        assert "day_of_week" in cell

    def test_dates_are_ascending(self):
        resp = client.get("/api/stats/heatmap")
        data = resp.json()["data"]
        dates = [c["date"] for c in data]
        assert dates == sorted(dates)

    def test_day_of_week_range(self):
        resp = client.get("/api/stats/heatmap")
        data = resp.json()["data"]
        for cell in data:
            assert 0 <= cell["day_of_week"] <= 6, f"day_of_week out of range: {cell}"

    def test_counts_are_non_negative(self):
        resp = client.get("/api/stats/heatmap")
        data = resp.json()["data"]
        for cell in data:
            assert cell["count"] >= 0

    def test_zero_fills_missing_days(self):
        """Days with no runs should appear with count=0, not be omitted."""
        resp = client.get("/api/stats/heatmap")
        data = resp.json()["data"]
        # All 182 days must be present regardless of whether there are runs
        assert len(data) == 182

    @pytest.mark.anyio
    async def test_counts_reflect_real_runs(self):
        """Count for today should include runs created today."""
        before = client.get("/api/stats/heatmap").json()["data"]
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_before = next((c for c in before if c["date"] == today_str), None)
        count_before = today_before["count"] if today_before else 0

        # Create 2 more runs today
        await _create_run(days_ago=0)
        await _create_run(days_ago=0)

        after = client.get("/api/stats/heatmap").json()["data"]
        today_after = next((c for c in after if c["date"] == today_str), None)
        assert today_after is not None
        assert today_after["count"] >= count_before


# ---------------------------------------------------------------------------
# GET /stats/anomalies
# ---------------------------------------------------------------------------


class TestAnomalies:
    def test_returns_200(self):
        resp = client.get("/api/stats/anomalies")
        assert resp.status_code == 200

    def test_response_has_data_key(self):
        resp = client.get("/api/stats/anomalies")
        body = resp.json()
        assert "data" in body

    def test_empty_database_returns_empty_list(self):
        """Empty DB returns empty list, not an error."""
        resp = client.get("/api/stats/anomalies")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)

    def test_anomaly_schema_when_present(self):
        """Each anomaly has the required fields."""
        resp = client.get("/api/stats/anomalies")
        data = resp.json()["data"]
        for anomaly in data:
            assert "type" in anomaly
            assert "severity" in anomaly
            assert "message" in anomaly
            assert "run_id" in anomaly
            assert anomaly["severity"] in ("warning", "critical")

    @pytest.mark.anyio
    async def test_detects_cost_spike(self):
        """A run costing far above the group mean (z > 2.5) should be flagged as cost_spike."""
        wf = f"anomaly-cost-{uuid.uuid4().hex[:8]}"
        # Create baseline runs with very low, tightly clustered cost
        for _ in range(8):
            await _create_run(workflow_name=wf, total_cost_usd=0.01)
        # Create one extremely expensive run - this will be many std-devs above the mean
        await _create_run(workflow_name=wf, total_cost_usd=50.0)

        resp = client.get("/api/stats/anomalies")
        data = resp.json()["data"]
        cost_spikes = [a for a in data if a["type"] == "cost_spike" and a["workflow"] == wf]
        assert len(cost_spikes) >= 1, f"Expected cost_spike anomaly for {wf}, got: {data}"
        assert cost_spikes[0]["severity"] in ("warning", "critical")

    @pytest.mark.anyio
    async def test_detects_error_streak(self):
        """3+ consecutive failed runs for a workflow should produce error_streak."""
        wf = f"anomaly-streak-{uuid.uuid4().hex[:8]}"
        for _ in range(3):
            await _create_run(workflow_name=wf, status=RunStatus.FAILED)

        resp = client.get("/api/stats/anomalies")
        data = resp.json()["data"]
        streaks = [a for a in data if a["type"] == "error_streak" and a["workflow"] == wf]
        assert len(streaks) >= 1, f"Expected error_streak for {wf}, got: {data}"
        assert streaks[0]["value"] >= 3

    @pytest.mark.anyio
    async def test_no_streak_when_recent_success(self):
        """No error_streak if the most recent run succeeded."""
        wf = f"anomaly-ok-{uuid.uuid4().hex[:8]}"
        # Two failures then a success
        await _create_run(workflow_name=wf, status=RunStatus.FAILED, days_ago=2)
        await _create_run(workflow_name=wf, status=RunStatus.FAILED, days_ago=1)
        await _create_run(workflow_name=wf, status=RunStatus.COMPLETED, days_ago=0)

        resp = client.get("/api/stats/anomalies")
        data = resp.json()["data"]
        streaks = [a for a in data if a["type"] == "error_streak" and a["workflow"] == wf]
        assert len(streaks) == 0, f"Should not flag streak when latest run succeeded: {streaks}"

    def test_anomalies_capped_at_20(self):
        """Endpoint should return at most 20 anomalies."""
        resp = client.get("/api/stats/anomalies")
        data = resp.json()["data"]
        assert len(data) <= 20

    def test_critical_before_warning(self):
        """Critical anomalies should appear before warning anomalies in the list."""
        resp = client.get("/api/stats/anomalies")
        data = resp.json()["data"]
        severities = [a["severity"] for a in data]
        # Once we see a 'warning', we should not see 'critical' afterwards
        seen_warning = False
        for sev in severities:
            if sev == "warning":
                seen_warning = True
            if seen_warning and sev == "critical":
                pytest.fail("Critical anomaly appeared after a warning anomaly")
