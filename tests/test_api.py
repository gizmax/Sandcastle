"""Tests for the API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


VALID_WORKFLOW = """
name: test-api
description: API test workflow
steps:
  - id: greet
    prompt: "Say hello to {input.name}"
    model: haiku
    max_turns: 3
"""

INVALID_WORKFLOW = "this is not valid yaml: ["


# --- Tests: Health ---


class TestHealth:
    def test_health_endpoint(self):
        with patch(
            "sandcastle.api.routes.SandshoreRuntime"
        ) as MockClient:
            mock = AsyncMock()
            mock.health.return_value = False
            mock.close = AsyncMock()
            MockClient.return_value = mock

            response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] in ("ok", "degraded")

    def test_health_response_format(self):
        with patch(
            "sandcastle.api.routes.SandshoreRuntime"
        ) as MockClient:
            mock = AsyncMock()
            mock.health.return_value = False
            mock.close = AsyncMock()
            MockClient.return_value = mock

            response = client.get("/api/health")

        data = response.json()
        assert "data" in data
        health = data["data"]
        assert "runtime" in health
        assert "redis" in health
        assert "database" in health


# --- Tests: Sync workflow execution ---


class TestSyncRun:
    def test_invalid_yaml(self):
        with patch("sandcastle.api.routes.async_session") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock()
            mock_session.return_value.__aexit__ = AsyncMock()

            response = client.post(
                "/api/workflows/run/sync",
                json={"workflow": INVALID_WORKFLOW, "input": {}},
            )

        assert response.status_code == 400

    def test_empty_workflow(self):
        empty_yaml = """
name: empty
description: no steps
steps: []
"""
        response = client.post(
            "/api/workflows/run/sync",
            json={"workflow": empty_yaml, "input": {}},
        )
        assert response.status_code == 400

    def test_successful_sync_run(self):
        from datetime import datetime, timezone

        from sandcastle.engine.executor import WorkflowResult

        mock_result = WorkflowResult(
            run_id="test-123",
            outputs={"greet": "Hello World"},
            total_cost_usd=0.001,
            status="completed",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

        with (
            patch(
                "sandcastle.api.routes.execute_workflow",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("sandcastle.api.routes.async_session") as mock_session_ctx,
        ):
            # Mock the DB session context manager
            mock_session = AsyncMock()
            # session.add() is sync in SQLAlchemy - use MagicMock to avoid unawaited coroutine
            mock_session.add = MagicMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            response = client.post(
                "/api/workflows/run/sync",
                json={
                    "workflow": VALID_WORKFLOW,
                    "input": {"name": "World"},
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "completed"
        assert data["data"]["outputs"]["greet"] == "Hello World"
        assert data["error"] is None


# --- Tests: Response format ---


class TestResponseFormat:
    def test_api_response_wrapper(self):
        """All responses should use the {data, error} wrapper."""
        with patch(
            "sandcastle.api.routes.SandshoreRuntime"
        ) as MockClient:
            mock = AsyncMock()
            mock.health.return_value = True
            mock.close = AsyncMock()
            MockClient.return_value = mock

            response = client.get("/api/health")

        data = response.json()
        assert "data" in data
        assert "error" in data

    def test_404_on_unknown_api_route(self):
        response = client.get("/api/nonexistent")
        assert response.status_code == 404


# --- Tests: Request validation ---


class TestRequestValidation:
    def test_missing_workflow_field(self):
        response = client.post(
            "/api/workflows/run/sync",
            json={"input": {"name": "test"}},
        )
        # Both workflow and workflow_name are optional, but one must be provided
        assert response.status_code == 400

    def test_workflow_with_cycle(self):
        cycle_yaml = """
name: cycle
description: cyclic
steps:
  - id: a
    depends_on: [b]
    prompt: "A"
  - id: b
    depends_on: [a]
    prompt: "B"
"""
        response = client.post(
            "/api/workflows/run/sync",
            json={"workflow": cycle_yaml, "input": {}},
        )
        # Should fail on validation or plan building
        assert response.status_code == 400


# --- Tests: Browse endpoint ---


class TestBrowse:
    def test_browse_home(self):
        response = client.get("/api/browse", params={"path": "~"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert "current" in data
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_browse_default(self):
        response = client.get("/api/browse")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["current"] is not None

    def test_browse_nonexistent(self):
        response = client.get("/api/browse", params={"path": "/nonexistent_dir_xyz"})
        assert response.status_code == 404

    def test_browse_entries_structure(self, tmp_path):
        # Create a temp structure
        (tmp_path / "subdir").mkdir()
        (tmp_path / "file.txt").write_text("hello")
        response = client.get("/api/browse", params={"path": str(tmp_path)})
        assert response.status_code == 200
        data = response.json()["data"]
        names = {e["name"] for e in data["entries"]}
        assert "subdir" in names
        assert "file.txt" in names
        # Dirs come first
        dir_entry = next(e for e in data["entries"] if e["name"] == "subdir")
        assert dir_entry["is_dir"] is True
        file_entry = next(e for e in data["entries"] if e["name"] == "file.txt")
        assert file_entry["is_dir"] is False

    def test_browse_parent_link(self, tmp_path):
        response = client.get("/api/browse", params={"path": str(tmp_path)})
        data = response.json()["data"]
        assert data["parent"] == str(tmp_path.parent)
