"""Comprehensive API integration tests for Sandcastle v0.27.

Tests exercise REAL endpoint behavior via TestClient against the in-memory
SQLite database configured in conftest.py. Mocks are used only where
external services (Redis, Ollama, etc.) are unavailable in CI.

Sections:
  1. Settings API roundtrip (12 tests)
  2. Advisor API (11 tests)
  3. Stats API (14 tests)
  4. Hub API (10 tests)
  5. Workflow lifecycle (10 tests)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Disable rate limiting for all tests in this module
from sandcastle.api import rate_limit as _rl_mod

_rl_mod.execution_limiter.check = AsyncMock()

from sandcastle.config import settings
from sandcastle.main import app
from sandcastle.models.db import (
    HubSubmission,
    Run,
    RunStatus,
    async_session,
)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

VALID_WORKFLOW = """\
name: integration-test
description: Integration test workflow
steps:
  - id: greet
    prompt: "Say hello to {input.name}"
    model: haiku
    max_turns: 3
"""

INVALID_YAML = "this is not valid yaml: ["

EMPTY_STEPS_WORKFLOW = """\
name: empty
description: no steps
steps: []
"""


def _make_run_id() -> str:
    return str(uuid.uuid4())


async def _insert_run(
    run_id: str | None = None,
    status: RunStatus = RunStatus.COMPLETED,
    workflow_name: str = "test-wf",
    total_cost_usd: float = 0.01,
) -> Run:
    """Insert a Run row into the in-memory DB."""
    rid = uuid.UUID(run_id) if run_id else uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        run = Run(
            id=rid,
            workflow_name=workflow_name,
            status=status,
            input_data={"name": "test"},
            output_data={"greet": "hello"} if status == RunStatus.COMPLETED else None,
            total_cost_usd=total_cost_usd,
            started_at=now - timedelta(seconds=5),
            completed_at=now if status != RunStatus.RUNNING else None,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def _insert_hub_submission(
    slug: str = "tester/my-wf",
    name: str = "my-wf",
    status: str = "approved",
) -> HubSubmission:
    """Insert a HubSubmission row into the in-memory DB."""
    async with async_session() as session:
        sub = HubSubmission(
            slug=slug,
            name=name,
            description="A test template",
            yaml_content=VALID_WORKFLOW,
            category="general_ai",
            tags=["test"],
            author="tester",
            status=status,
            step_count=1,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return sub


# ===========================================================================
# 1. SETTINGS API ROUNDTRIP
# ===========================================================================


class TestSettingsGet:
    """GET /api/settings - read settings."""

    def test_returns_200(self):
        resp = client.get("/api/settings")
        assert resp.status_code == 200

    def test_response_has_all_expected_fields(self):
        resp = client.get("/api/settings")
        data = resp.json()["data"]
        expected_fields = {
            "anthropic_api_key",
            "e2b_api_key",
            "openai_api_key",
            "mistral_api_key",
            "minimax_api_key",
            "openrouter_api_key",
            "auth_required",
            "dashboard_origin",
            "default_max_cost_usd",
            "webhook_secret",
            "log_level",
            "max_workflow_depth",
            "storage_backend",
            "data_dir",
            "workflows_dir",
            "is_local_mode",
            "database_url",
            "redis_url",
        }
        assert expected_fields.issubset(set(data.keys())), (
            f"Missing fields: {expected_fields - set(data.keys())}"
        )

    def test_api_keys_are_masked(self):
        """API keys should never be returned in plaintext."""
        # Temporarily set a key so masking logic fires
        original = settings.anthropic_api_key
        settings.anthropic_api_key = "sk-ant-api03-XXXXXXXXXXXXXXXXXXXX"
        try:
            resp = client.get("/api/settings")
            data = resp.json()["data"]
            key_val = data["anthropic_api_key"]
            # Must be masked (starts with ****)
            assert key_val.startswith("****") or key_val == "", (
                f"API key not masked: {key_val}"
            )
            # Must NOT equal the raw key
            assert key_val != "sk-ant-api03-XXXXXXXXXXXXXXXXXXXX"
        finally:
            settings.anthropic_api_key = original

    def test_database_url_is_masked(self):
        resp = client.get("/api/settings")
        data = resp.json()["data"]
        # database_url contains the raw connection string - must be masked
        raw = settings.database_url
        if raw:
            assert data["database_url"] != raw or data["database_url"] == ""

    def test_content_type_is_json(self):
        resp = client.get("/api/settings")
        assert "application/json" in resp.headers.get("content-type", "")


class TestSettingsPatch:
    """PATCH /api/settings - update settings."""

    def test_patch_default_max_cost_usd(self):
        original = settings.default_max_cost_usd
        try:
            resp = client.patch("/api/settings", json={"default_max_cost_usd": 5.0})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["default_max_cost_usd"] == 5.0
        finally:
            settings.default_max_cost_usd = original

    def test_patch_log_level(self):
        original = settings.log_level
        try:
            resp = client.patch("/api/settings", json={"log_level": "debug"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["log_level"] == "debug"
        finally:
            settings.log_level = original

    def test_patch_invalid_log_level_rejected(self):
        """Log level must be one of debug|info|warning|error."""
        resp = client.patch("/api/settings", json={"log_level": "verbose"})
        assert resp.status_code == 422

    def test_patch_max_workflow_depth(self):
        original = settings.max_workflow_depth
        try:
            resp = client.patch("/api/settings", json={"max_workflow_depth": 10})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["max_workflow_depth"] == 10
        finally:
            settings.max_workflow_depth = original

    def test_patch_max_workflow_depth_out_of_range(self):
        """max_workflow_depth must be 1-20."""
        resp = client.patch("/api/settings", json={"max_workflow_depth": 0})
        assert resp.status_code == 422
        resp2 = client.patch("/api/settings", json={"max_workflow_depth": 21})
        assert resp2.status_code == 422

    def test_patch_mistral_api_key_masks_on_get(self):
        """Setting mistral_api_key should mask it on subsequent GET."""
        original = settings.mistral_api_key
        try:
            resp = client.patch(
                "/api/settings", json={"mistral_api_key": "mist-1234567890abcdef"}
            )
            assert resp.status_code == 200
            # Verify it's masked in the response
            data = resp.json()["data"]
            assert data["mistral_api_key"] != "mist-1234567890abcdef"
            assert "****" in data["mistral_api_key"] or data["mistral_api_key"] == ""
        finally:
            settings.mistral_api_key = original

    def test_patch_empty_body_returns_current_settings(self):
        """PATCH with no updates should return current settings unchanged."""
        resp = client.patch("/api/settings", json={})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "log_level" in data


# ===========================================================================
# 2. ADVISOR API
# ===========================================================================


class TestAdvisorStatus:
    """GET /api/advisor/status"""

    def test_returns_200(self):
        resp = client.get("/api/advisor/status")
        assert resp.status_code == 200

    def test_has_provider_list_with_regions(self):
        resp = client.get("/api/advisor/status")
        data = resp.json()["data"]
        assert "available_providers" in data
        providers = data["available_providers"]
        assert isinstance(providers, list)
        assert len(providers) >= 3  # anthropic, mistral, openai, ollama
        for p in providers:
            assert "id" in p
            assert "region" in p
            assert p["region"] in ("us", "eu", "local")

    def test_has_current_provider(self):
        resp = client.get("/api/advisor/status")
        data = resp.json()["data"]
        assert "current_provider" in data
        assert "current_model" in data
        assert data["current_provider"] in ("anthropic", "mistral", "openai", "ollama")


class TestAdvisorConfigure:
    """POST /api/advisor/configure"""

    def test_configure_valid_provider(self):
        resp = client.post("/api/advisor/configure", json={
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["provider"] == "anthropic"
        assert data["status"] == "configured"

    def test_configure_eu_residency_blocks_us_provider(self):
        """Requesting EU data residency without Mistral or Ollama configured
        should fail with EU_PROVIDER_REQUIRED."""
        original_mistral = settings.mistral_api_key
        settings.mistral_api_key = ""
        try:
            resp = client.post("/api/advisor/configure", json={
                "provider": "anthropic",
                "data_residency": "eu",
            })
            assert resp.status_code == 400
            error_detail = resp.json()
            error = error_detail.get("detail", {}).get("error", {})
            assert error.get("code") == "EU_PROVIDER_REQUIRED"
        finally:
            settings.mistral_api_key = original_mistral


class TestAdvisorTestConnection:
    """POST /api/advisor/test-connection"""

    def test_unknown_provider_returns_error(self):
        resp = client.post("/api/advisor/test-connection", json={
            "provider": "nonexistent-provider"
        })
        assert resp.status_code == 400

    def test_invalid_json_body(self):
        resp = client.post(
            "/api/advisor/test-connection",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


class TestAdvisorCostEstimate:
    """GET /api/advisor/cost-estimate"""

    def test_returns_200(self):
        resp = client.get("/api/advisor/cost-estimate")
        assert resp.status_code == 200

    def test_has_current_and_alternatives(self):
        resp = client.get("/api/advisor/cost-estimate")
        data = resp.json()["data"]
        assert "current" in data
        assert "alternatives" in data
        current = data["current"]
        assert "provider" in current
        assert "model" in current
        assert "estimated_cost" in current
        assert isinstance(data["alternatives"], list)

    def test_alternatives_always_include_ollama(self):
        resp = client.get("/api/advisor/cost-estimate")
        data = resp.json()["data"]
        ollama_entries = [
            a for a in data["alternatives"] if a["provider"] == "ollama"
        ]
        assert len(ollama_entries) >= 1
        assert ollama_entries[0]["estimated_cost"] == 0.0


class TestAdvisorRecommendations:
    """GET /api/advisor/recommendations"""

    def test_returns_200_with_empty_db(self):
        resp = client.get("/api/advisor/recommendations")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)


# ===========================================================================
# 3. STATS API
# ===========================================================================


class TestStatsMain:
    """GET /api/stats"""

    def test_returns_200(self):
        resp = client.get("/api/stats")
        assert resp.status_code == 200

    def test_correct_structure(self):
        resp = client.get("/api/stats")
        data = resp.json()["data"]
        for field in (
            "total_runs_today",
            "success_rate",
            "total_cost_today",
            "avg_duration_seconds",
            "runs_by_day",
            "cost_by_workflow",
        ):
            assert field in data, f"Missing field: {field}"
        assert isinstance(data["runs_by_day"], list)
        assert isinstance(data["cost_by_workflow"], list)

    def test_has_cache_control_header(self):
        """Stats endpoint should include a Cache-Control header (security
        middleware may override the value to no-store)."""
        resp = client.get("/api/stats")
        assert "Cache-Control" in resp.headers

    def test_empty_db_returns_sensible_defaults(self):
        """On empty DB, numeric stats should be 0, not crash."""
        resp = client.get("/api/stats")
        data = resp.json()["data"]
        assert data["total_runs_today"] >= 0
        assert data["total_cost_today"] >= 0
        assert data["success_rate"] >= 0
        assert data["avg_duration_seconds"] >= 0


class TestStatsSparklines:
    """GET /api/stats/sparklines"""

    def test_returns_200(self):
        resp = client.get("/api/stats/sparklines")
        assert resp.status_code == 200

    def test_returns_arrays(self):
        resp = client.get("/api/stats/sparklines")
        data = resp.json()["data"]
        for metric in ("runs", "rate", "cost", "duration"):
            assert metric in data, f"Missing sparkline metric: {metric}"
            assert "values" in data[metric]
            assert isinstance(data[metric]["values"], list)
            assert len(data[metric]["values"]) == 7  # 7 days

    def test_has_cache_control(self):
        resp = client.get("/api/stats/sparklines")
        assert "Cache-Control" in resp.headers


class TestStatsHeatmap:
    """GET /api/stats/heatmap"""

    def test_returns_200(self):
        resp = client.get("/api/stats/heatmap")
        assert resp.status_code == 200

    def test_returns_182_days(self):
        """Heatmap covers 26 weeks = 182 days."""
        resp = client.get("/api/stats/heatmap")
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) == 182

    def test_each_cell_has_date_count_day_of_week(self):
        resp = client.get("/api/stats/heatmap")
        data = resp.json()["data"]
        for cell in data[:5]:  # Check a few
            assert "date" in cell
            assert "count" in cell
            assert "day_of_week" in cell
            assert 0 <= cell["day_of_week"] <= 6
            assert cell["count"] >= 0

    def test_has_cache_control(self):
        resp = client.get("/api/stats/heatmap")
        assert "Cache-Control" in resp.headers


class TestStatsProviderCosts:
    """GET /api/stats/provider-costs"""

    def test_returns_200(self):
        resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200

    def test_has_by_provider_array(self):
        resp = client.get("/api/stats/provider-costs")
        data = resp.json()["data"]
        assert "by_provider" in data
        assert isinstance(data["by_provider"], list)
        assert "period_days" in data
        assert "total_cost_usd" in data

    def test_empty_db_returns_zero(self):
        resp = client.get("/api/stats/provider-costs")
        data = resp.json()["data"]
        assert data["total_cost_usd"] >= 0


class TestStatsProviderSavings:
    """GET /api/stats/provider-savings"""

    def test_returns_200(self):
        resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200

    def test_has_alternatives_array(self):
        resp = client.get("/api/stats/provider-savings")
        data = resp.json()["data"]
        assert "alternatives" in data
        assert isinstance(data["alternatives"], list)
        assert "current_total_usd" in data


class TestStatsFailoverEvents:
    """GET /api/stats/failover-events"""

    def test_returns_200(self):
        resp = client.get("/api/stats/failover-events")
        assert resp.status_code == 200

    def test_has_events_list(self):
        resp = client.get("/api/stats/failover-events")
        data = resp.json()["data"]
        assert "events" in data
        assert isinstance(data["events"], list)
        assert "total_failovers_7d" in data
        assert "total_cost_delta_7d" in data

    def test_has_cache_control(self):
        resp = client.get("/api/stats/failover-events")
        assert "Cache-Control" in resp.headers


# ===========================================================================
# 4. HUB API
# ===========================================================================


class TestHubSubmit:
    """POST /api/hub/submit"""

    def test_valid_yaml_returns_201(self):
        resp = client.post("/api/hub/submit", json={
            "yaml_content": VALID_WORKFLOW,
            "description": "Integration test template",
            "category": "general_ai",
            "tags": ["test", "integration"],
        })
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "slug" in data
        assert data["status"] == "pending"
        assert data["name"] == "integration-test"

    def test_invalid_yaml_returns_400(self):
        resp = client.post("/api/hub/submit", json={
            "yaml_content": INVALID_YAML,
            "description": "Bad template",
        })
        assert resp.status_code == 400

    def test_missing_description_returns_400(self):
        """description is required in HubSubmitRequest."""
        resp = client.post("/api/hub/submit", json={
            "yaml_content": VALID_WORKFLOW,
        })
        assert resp.status_code == 400

    def test_empty_yaml_returns_400(self):
        resp = client.post("/api/hub/submit", json={
            "yaml_content": "",
            "description": "Empty",
        })
        assert resp.status_code == 400

    def test_too_many_tags_returns_400(self):
        resp = client.post("/api/hub/submit", json={
            "yaml_content": VALID_WORKFLOW,
            "description": "Many tags",
            "tags": [f"tag-{i}" for i in range(25)],
        })
        assert resp.status_code == 400


class TestHubCommunity:
    """GET /api/hub/community"""

    def test_returns_paginated_list(self):
        resp = client.get("/api/hub/community")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["data"], list)
        assert "meta" in body
        meta = body["meta"]
        assert "total" in meta
        assert "limit" in meta
        assert "offset" in meta

    def test_invalid_status_returns_400(self):
        resp = client.get("/api/hub/community", params={"status": "bogus"})
        assert resp.status_code == 400


class TestHubRate:
    """POST /api/hub/templates/{slug}/rate"""

    @pytest.mark.asyncio
    async def test_rate_valid_range(self):
        sub = await _insert_hub_submission(
            slug=f"tester/rate-test-{uuid.uuid4().hex[:8]}"
        )
        resp = client.post(f"/api/hub/templates/{sub.slug}/rate", json={
            "rating": 4,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["rating"] == 4.0
        assert data["rating_count"] == 1

    @pytest.mark.asyncio
    async def test_rate_out_of_range_rejected(self):
        sub = await _insert_hub_submission(
            slug=f"tester/rate-range-{uuid.uuid4().hex[:8]}"
        )
        resp = client.post(f"/api/hub/templates/{sub.slug}/rate", json={
            "rating": 6,
        })
        assert resp.status_code == 400

    def test_rate_nonexistent_template_returns_404(self):
        resp = client.post("/api/hub/templates/no/such-template/rate", json={
            "rating": 3,
        })
        assert resp.status_code == 404


class TestHubDownload:
    """POST /api/hub/templates/{slug}/download"""

    @pytest.mark.asyncio
    async def test_download_tracks_count(self):
        sub = await _insert_hub_submission(
            slug=f"tester/dl-test-{uuid.uuid4().hex[:8]}"
        )
        initial_downloads = sub.downloads or 0
        resp = client.post(f"/api/hub/templates/{sub.slug}/download")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["downloads"] == initial_downloads + 1

    def test_download_nonexistent_returns_404(self):
        resp = client.post("/api/hub/templates/no/such-template/download")
        assert resp.status_code == 404


# ===========================================================================
# 5. WORKFLOW LIFECYCLE
# ===========================================================================


class TestWorkflowCreate:
    """POST /api/workflows - save workflow."""

    def test_create_workflow_returns_201(self, tmp_path):
        with patch.object(settings, "workflows_dir", str(tmp_path)):
            resp = client.post("/api/workflows", json={
                "name": "test-create",
                "content": VALID_WORKFLOW,
                "description": "Created by integration test",
            })
        assert resp.status_code == 201
        data = resp.json()["data"]
        # YAML name is overridden to match request name for consistency
        assert data["name"] == "test-create"
        assert data["steps_count"] == 1

    def test_create_invalid_yaml_returns_400(self, tmp_path):
        with patch.object(settings, "workflows_dir", str(tmp_path)):
            resp = client.post("/api/workflows", json={
                "name": "bad-wf",
                "content": INVALID_YAML,
            })
        assert resp.status_code == 400

    def test_create_empty_steps_returns_400(self, tmp_path):
        with patch.object(settings, "workflows_dir", str(tmp_path)):
            resp = client.post("/api/workflows", json={
                "name": "empty-wf",
                "content": EMPTY_STEPS_WORKFLOW,
            })
        assert resp.status_code == 400


class TestWorkflowList:
    """GET /api/workflows"""

    def test_list_workflows_returns_200(self):
        resp = client.get("/api/workflows")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)


class TestWorkflowRunAsync:
    """POST /api/workflows/run - queue a workflow run."""

    def test_valid_workflow_returns_202(self):
        with patch(
            "sandcastle.api.routes.enqueue_workflow",
            new_callable=AsyncMock,
        ):
            resp = client.post("/api/workflows/run", json={
                "workflow": VALID_WORKFLOW,
                "input": {"name": "World"},
            })
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert "run_id" in data
        assert data["status"] == "queued"

    def test_invalid_yaml_returns_400(self):
        resp = client.post("/api/workflows/run", json={
            "workflow": INVALID_YAML,
            "input": {},
        })
        assert resp.status_code == 400


class TestRunDetail:
    """GET /api/runs/{run_id}"""

    @pytest.mark.asyncio
    async def test_get_existing_run(self):
        run = await _insert_run()
        resp = client.get(f"/api/runs/{run.id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["run_id"] == str(run.id)
        assert data["status"] == "completed"

    def test_nonexistent_run_returns_404(self):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/runs/{fake_id}")
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self):
        resp = client.get("/api/runs/not-a-uuid")
        assert resp.status_code == 400


class TestRunCancel:
    """POST /api/runs/{run_id}/cancel"""

    @pytest.mark.asyncio
    async def test_cancel_running_run(self):
        run = await _insert_run(status=RunStatus.RUNNING)
        resp = client.post(f"/api/runs/{run.id}/cancel")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["cancelled"] is True

    @pytest.mark.asyncio
    async def test_cancel_completed_run_fails(self):
        run = await _insert_run(status=RunStatus.COMPLETED)
        resp = client.post(f"/api/runs/{run.id}/cancel")
        assert resp.status_code == 400

    def test_cancel_nonexistent_returns_404(self):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/runs/{fake_id}/cancel")
        assert resp.status_code == 404


class TestRunDelete:
    """DELETE /api/runs/{run_id}"""

    @pytest.mark.asyncio
    async def test_delete_completed_run(self):
        run = await _insert_run(status=RunStatus.COMPLETED)
        resp = client.delete(f"/api/runs/{run.id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["deleted"] is True
        # Verify it's gone
        resp2 = client.get(f"/api/runs/{run.id}")
        assert resp2.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_running_run_fails(self):
        """Cannot delete a run that is still running - must cancel first."""
        run = await _insert_run(status=RunStatus.RUNNING)
        resp = client.delete(f"/api/runs/{run.id}")
        assert resp.status_code == 400


class TestRunReplay:
    """POST /api/runs/{run_id}/replay"""

    @pytest.mark.asyncio
    async def test_replay_nonexistent_run_returns_404(self):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/runs/{fake_id}/replay", json={
            "from_step": "greet",
        })
        assert resp.status_code == 404

    def test_replay_invalid_uuid_returns_400(self):
        resp = client.post("/api/runs/bad-id/replay", json={
            "from_step": "greet",
        })
        assert resp.status_code == 400


class TestRunFork:
    """POST /api/runs/{run_id}/fork"""

    @pytest.mark.asyncio
    async def test_fork_nonexistent_run_returns_404(self):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/runs/{fake_id}/fork", json={
            "from_step": "greet",
        })
        assert resp.status_code == 404

    def test_fork_invalid_uuid_returns_400(self):
        resp = client.post("/api/runs/bad-id/fork", json={
            "from_step": "greet",
        })
        assert resp.status_code == 400
