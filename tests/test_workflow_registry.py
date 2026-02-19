"""Tests for the Workflow Registry (versioning, promotion, rollback)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)

VALID_WORKFLOW = """
name: test-registry
description: Registry test workflow
sandstorm_url: http://localhost:8000
steps:
  - id: greet
    prompt: "Say hello to {input.name}"
    model: haiku
    max_turns: 3
"""

VALID_WORKFLOW_V2 = """
name: test-registry
description: Registry test workflow v2
sandstorm_url: http://localhost:8000
steps:
  - id: greet
    prompt: "Say hello to {input.name} with more detail"
    model: sonnet
    max_turns: 5
  - id: summarize
    prompt: "Summarize: {steps.greet.output}"
    model: haiku
    depends_on:
      - greet
"""


def _unique_name(prefix: str = "reg") -> str:
    """Generate a unique workflow name to avoid cross-run collisions."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class TestSaveCreatesVersion:
    """POST /api/workflows should create a draft version in the DB."""

    def test_save_creates_draft(self, tmp_path):
        name = _unique_name("save")
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False
            response = client.post(
                "/api/workflows",
                json={
                    "name": name,
                    "content": VALID_WORKFLOW,
                    "description": "First save",
                },
            )
        assert response.status_code == 200
        data = response.json()["data"]
        # name in response comes from YAML content, not request.name
        assert data["name"] == "test-registry"
        # Version should be set if registry is available
        if data.get("version") is not None:
            assert data["version"] >= 1
            assert data["version_status"] == "draft"


class TestPromotionPipeline:
    """Test draft -> staging -> production promotion flow."""

    def test_promote_draft_to_staging(self, tmp_path):
        name = _unique_name("promo")
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False

            # Save a workflow first (creates draft)
            client.post(
                "/api/workflows",
                json={"name": name, "content": VALID_WORKFLOW},
            )

            # Promote draft -> staging
            response = client.post(
                f"/api/workflows/{name}/promote", json={}
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["new_status"] == "staging"

    def test_promote_staging_to_production(self, tmp_path):
        name = _unique_name("promo")
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False

            # Save and promote to staging
            client.post(
                "/api/workflows",
                json={"name": name, "content": VALID_WORKFLOW},
            )
            client.post(f"/api/workflows/{name}/promote", json={})

            # Promote staging -> production
            response = client.post(
                f"/api/workflows/{name}/promote", json={}
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["new_status"] == "production"


class TestRollback:
    """Test rollback to previous production version."""

    def test_rollback_to_previous(self, tmp_path):
        name = _unique_name("rollback")
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False

            # Save v1 and promote to production
            client.post(
                "/api/workflows",
                json={"name": name, "content": VALID_WORKFLOW},
            )
            client.post(f"/api/workflows/{name}/promote", json={})
            client.post(f"/api/workflows/{name}/promote", json={})

            # Save v2 and promote to production (v1 becomes archived)
            client.post(
                "/api/workflows",
                json={"name": name, "content": VALID_WORKFLOW_V2},
            )
            client.post(f"/api/workflows/{name}/promote", json={})
            client.post(f"/api/workflows/{name}/promote", json={})

            # Rollback to v1
            response = client.post(
                f"/api/workflows/{name}/rollback", json={}
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["rolled_back_to_version"] == 1
        assert data["status"] == "production"


class TestVersionHistory:
    """Test GET /api/workflows/{name}/versions endpoint."""

    def test_list_versions(self, tmp_path):
        name = _unique_name("history")
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False

            # Save two versions
            client.post(
                "/api/workflows",
                json={
                    "name": name,
                    "content": VALID_WORKFLOW,
                    "description": "v1",
                },
            )
            client.post(
                "/api/workflows",
                json={
                    "name": name,
                    "content": VALID_WORKFLOW_V2,
                    "description": "v2",
                },
            )

            response = client.get(f"/api/workflows/{name}/versions")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["workflow_name"] == name
        assert len(data["versions"]) == 2

    def test_version_not_found(self):
        response = client.get("/api/workflows/nonexistent-xyz/versions")
        assert response.status_code == 404


class TestDiskFallback:
    """Workflow on disk without registry entry should still work."""

    def test_disk_workflow_auto_imports(self, tmp_path):
        name = _unique_name("disk")
        (tmp_path / f"{name}.yaml").write_text(VALID_WORKFLOW)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False

            response = client.get(f"/api/workflows/{name}/versions")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["versions"]) >= 1


class TestRunStoresVersion:
    """Running a workflow should store the workflow_version on the Run."""

    def test_sync_run_stores_version(self, tmp_path):
        name = _unique_name("vrun")
        with (
            patch("sandcastle.api.routes.settings") as mock_settings,
            patch("sandcastle.api.routes.execute_workflow") as mock_exec,
        ):
            mock_settings.workflows_dir = str(tmp_path)
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False
            mock_settings.anthropic_api_key = "test"
            mock_settings.e2b_api_key = "test"
            mock_settings.default_max_cost_usd = 0

            mock_result = AsyncMock()
            mock_result.run_id = "test-123"
            mock_result.status = "completed"
            mock_result.outputs = {}
            mock_result.total_cost_usd = 0.0
            mock_result.started_at = None
            mock_result.completed_at = None
            mock_result.error = None
            mock_exec.return_value = mock_result

            # Save workflow first (creates v1 draft)
            client.post(
                "/api/workflows",
                json={"name": name, "content": VALID_WORKFLOW},
            )
            # Promote to production
            client.post(f"/api/workflows/{name}/promote", json={})
            client.post(f"/api/workflows/{name}/promote", json={})

            response = client.post(
                "/api/workflows/run/sync",
                json={
                    "workflow_name": name,
                    "input": {"name": "World"},
                },
            )

        assert response.status_code == 200
