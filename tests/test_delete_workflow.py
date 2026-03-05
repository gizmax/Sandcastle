"""Tests for DELETE /api/workflows/{name} endpoint.

Covers:
  - Successful deletion (YAML file + WorkflowVersion records)
  - NOT_FOUND when workflow does not exist (404)
  - INVALID_NAME for path traversal and bad characters (400)
  - ACTIVE_RUNS conflict when queued/running runs exist (409)
  - Edge cases: multiple versions, case sensitivity
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Disable rate limiting for all tests in this module
from sandcastle.api import rate_limit as _rl_mod

_rl_mod.execution_limiter.check = AsyncMock()

from sandcastle.main import app
from sandcastle.models.db import (
    Run,
    RunStatus,
    WorkflowVersion,
    WorkflowVersionStatus,
    async_session,
)
from sqlalchemy import func, select

client = TestClient(app)

VALID_WORKFLOW = """
name: delete-test
description: Delete test workflow
steps:
  - id: step1
    prompt: "Hello {input.name}"
    model: haiku
    max_turns: 3
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


async def _create_run(
    run_id: str | None = None,
    status: RunStatus = RunStatus.COMPLETED,
    workflow_name: str = "delete-test",
    total_cost_usd: float = 0.01,
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
            output_data={"result": "ok"} if status == RunStatus.COMPLETED else None,
            total_cost_usd=total_cost_usd,
            started_at=now - timedelta(seconds=5),
            completed_at=now if status not in (RunStatus.RUNNING, RunStatus.QUEUED) else None,
            error=error,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def _create_workflow_version(
    workflow_name: str,
    version: int,
    status: WorkflowVersionStatus = WorkflowVersionStatus.DRAFT,
    yaml_content: str | None = None,
) -> WorkflowVersion:
    """Insert a WorkflowVersion row."""
    content = yaml_content or VALID_WORKFLOW
    async with async_session() as session:
        wv = WorkflowVersion(
            workflow_name=workflow_name,
            version=version,
            status=status,
            yaml_content=content,
            description=f"v{version} {status.value}",
            steps_count=1,
            checksum=hashlib.sha256(content.encode()).hexdigest(),
        )
        session.add(wv)
        await session.commit()
        await session.refresh(wv)
        return wv


async def _count_workflow_versions(workflow_name: str) -> int:
    """Count WorkflowVersion rows for a given workflow name."""
    async with async_session() as session:
        stmt = select(func.count(WorkflowVersion.id)).where(
            WorkflowVersion.workflow_name == workflow_name
        )
        return (await session.execute(stmt)).scalar() or 0


# ---------------------------------------------------------------------------
# 1. SUCCESSFUL DELETION
# ---------------------------------------------------------------------------


class TestDeleteWorkflowSuccess:
    """DELETE /api/workflows/{name} - successful cases."""

    @pytest.mark.asyncio
    async def test_delete_existing_workflow(self, tmp_path: Path):
        """Deleting an existing workflow removes the YAML file and version records."""
        wf_name = f"del-ok-{_uid()[:8]}"

        # Create YAML file on disk
        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        # Create version records in DB
        await _create_workflow_version(wf_name, 1, WorkflowVersionStatus.DRAFT)
        assert await _count_workflow_versions(wf_name) == 1

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["deleted"] is True
        assert data["workflow_name"] == wf_name

        # YAML file should be removed
        assert not yaml_file.exists()

        # All version records should be deleted
        assert await _count_workflow_versions(wf_name) == 0

    @pytest.mark.asyncio
    async def test_delete_workflow_no_versions_but_file_exists(self, tmp_path: Path):
        """Workflow with file on disk but no version records is still deletable."""
        wf_name = f"del-novers-{_uid()[:8]}"

        # Create YAML file only (no version records)
        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["deleted"] is True

        # File removed
        assert not yaml_file.exists()


# ---------------------------------------------------------------------------
# 2. NOT FOUND (404)
# ---------------------------------------------------------------------------


class TestDeleteWorkflowNotFound:
    """DELETE /api/workflows/{name} - workflow not found."""

    def test_delete_nonexistent_workflow(self, tmp_path: Path):
        """Deleting a workflow that does not exist returns 404 NOT_FOUND."""
        wf_name = f"missing-{_uid()[:8]}"

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 404
        error = resp.json()["detail"]["error"]
        assert error["code"] == "NOT_FOUND"
        assert wf_name in error["message"]


# ---------------------------------------------------------------------------
# 3. INVALID NAME (400)
# ---------------------------------------------------------------------------


class TestDeleteWorkflowInvalidName:
    """DELETE /api/workflows/{name} - invalid/dangerous names."""

    def test_path_traversal_dots_in_name(self, tmp_path: Path):
        """Names containing dots (like '..passwd') are sanitized and rejected."""
        # Note: literal '../' in the URL path gets collapsed by Starlette routing
        # before reaching the handler. We test dot-containing names that DO reach
        # the handler but fail the safe_name != name validation.
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete("/api/workflows/..passwd")

        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "INVALID_NAME"

    def test_dots_only_name(self, tmp_path: Path):
        """A name consisting only of dots should be rejected."""
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            # Single dot passes through Starlette routing fine
            resp = client.delete("/api/workflows/...")

        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "INVALID_NAME"

    def test_name_with_special_characters(self, tmp_path: Path):
        """Names with special chars (spaces, slashes) are sanitized and rejected."""
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            # Name with spaces gets sanitized to underscores, which won't match
            resp = client.delete("/api/workflows/bad name here")

        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "INVALID_NAME"

    def test_empty_like_name(self, tmp_path: Path):
        """Names that sanitize to empty or all-underscores are rejected."""
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete("/api/workflows/___")

        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "INVALID_NAME"


# ---------------------------------------------------------------------------
# 4. ACTIVE RUNS (409)
# ---------------------------------------------------------------------------


class TestDeleteWorkflowActiveRuns:
    """DELETE /api/workflows/{name} - conflict with active runs."""

    @pytest.mark.asyncio
    async def test_delete_blocked_by_queued_run(self, tmp_path: Path):
        """Cannot delete a workflow that has a QUEUED run."""
        wf_name = f"del-queued-{_uid()[:8]}"

        # Create YAML file
        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        # Create an active (QUEUED) run
        await _create_run(workflow_name=wf_name, status=RunStatus.QUEUED)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 409
        error = resp.json()["detail"]["error"]
        assert error["code"] == "ACTIVE_RUNS"
        assert "1 active run" in error["message"]

        # File and versions should NOT be deleted
        assert yaml_file.exists()

    @pytest.mark.asyncio
    async def test_delete_blocked_by_running_run(self, tmp_path: Path):
        """Cannot delete a workflow that has a RUNNING run."""
        wf_name = f"del-running-{_uid()[:8]}"

        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        await _create_run(workflow_name=wf_name, status=RunStatus.RUNNING)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 409
        error = resp.json()["detail"]["error"]
        assert error["code"] == "ACTIVE_RUNS"

        # File should still exist
        assert yaml_file.exists()

    @pytest.mark.asyncio
    async def test_delete_blocked_by_multiple_active_runs(self, tmp_path: Path):
        """Cannot delete when multiple runs are active; message includes count."""
        wf_name = f"del-multi-{_uid()[:8]}"

        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        # Create 3 active runs (mix of QUEUED and RUNNING)
        await _create_run(workflow_name=wf_name, status=RunStatus.QUEUED)
        await _create_run(workflow_name=wf_name, status=RunStatus.RUNNING)
        await _create_run(workflow_name=wf_name, status=RunStatus.QUEUED)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 409
        error = resp.json()["detail"]["error"]
        assert error["code"] == "ACTIVE_RUNS"
        assert "3 active run" in error["message"]

    @pytest.mark.asyncio
    async def test_delete_allowed_when_only_completed_runs(self, tmp_path: Path):
        """Completed/failed/cancelled runs should NOT block deletion."""
        wf_name = f"del-done-{_uid()[:8]}"

        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        # Create completed and failed runs (not active)
        await _create_run(workflow_name=wf_name, status=RunStatus.COMPLETED)
        await _create_run(workflow_name=wf_name, status=RunStatus.FAILED)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 200
        assert not yaml_file.exists()


# ---------------------------------------------------------------------------
# 5. EDGE CASES
# ---------------------------------------------------------------------------


class TestDeleteWorkflowEdgeCases:
    """Edge cases for workflow deletion."""

    @pytest.mark.asyncio
    async def test_delete_workflow_with_multiple_versions(self, tmp_path: Path):
        """All version records are deleted, not just the latest."""
        wf_name = f"del-multivers-{_uid()[:8]}"

        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        # Create multiple version records
        await _create_workflow_version(wf_name, 1, WorkflowVersionStatus.ARCHIVED)
        await _create_workflow_version(wf_name, 2, WorkflowVersionStatus.PRODUCTION)
        await _create_workflow_version(wf_name, 3, WorkflowVersionStatus.DRAFT)
        assert await _count_workflow_versions(wf_name) == 3

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 200
        assert not yaml_file.exists()
        assert await _count_workflow_versions(wf_name) == 0

    def test_case_preserving_name(self, tmp_path: Path):
        """Workflow name with mixed case is preserved in response."""
        wf_name = "MyWorkflow"
        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # The response should preserve the exact name passed in
        assert data["workflow_name"] == wf_name
        assert data["deleted"] is True

    def test_nonexistent_different_name(self, tmp_path: Path):
        """Deleting a name that doesn't match any file returns 404."""
        wf_name = "ExistingWorkflow"
        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            # Completely different name - should 404
            resp = client.delete("/api/workflows/TotallyDifferent")

        assert resp.status_code == 404
        # Original file untouched
        assert yaml_file.exists()

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_other_workflows(self, tmp_path: Path):
        """Deleting one workflow must not affect another's file or versions."""
        wf_a = f"wf-a-{_uid()[:8]}"
        wf_b = f"wf-b-{_uid()[:8]}"

        file_a = tmp_path / f"{wf_a}.yaml"
        file_b = tmp_path / f"{wf_b}.yaml"
        file_a.write_text(VALID_WORKFLOW)
        file_b.write_text(VALID_WORKFLOW)

        await _create_workflow_version(wf_a, 1)
        await _create_workflow_version(wf_b, 1)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_a}")

        assert resp.status_code == 200
        assert not file_a.exists()
        assert await _count_workflow_versions(wf_a) == 0

        # wf_b should be untouched
        assert file_b.exists()
        assert await _count_workflow_versions(wf_b) == 1

    def test_delete_with_hyphen_and_underscore_names(self, tmp_path: Path):
        """Workflow names with hyphens and underscores are valid."""
        wf_name = "my-workflow_v2"
        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["deleted"] is True
        assert data["workflow_name"] == wf_name
        assert not yaml_file.exists()

    @pytest.mark.asyncio
    async def test_response_format(self, tmp_path: Path):
        """Response body should have standard ApiResponse format with data dict."""
        wf_name = f"del-fmt-{_uid()[:8]}"

        yaml_file = tmp_path / f"{wf_name}.yaml"
        yaml_file.write_text(VALID_WORKFLOW)

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)

            resp = client.delete(f"/api/workflows/{wf_name}")

        assert resp.status_code == 200
        body = resp.json()
        # Standard ApiResponse wraps result under "data"
        assert "data" in body
        assert body["data"]["deleted"] is True
        assert body["data"]["workflow_name"] == wf_name
        # No error key in success response
        assert body.get("error") is None
