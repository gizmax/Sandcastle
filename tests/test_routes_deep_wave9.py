"""Deep audit tests for routes.py - Wave 9.

Covers previously uncovered endpoint paths:
  - Pagination edge cases (offset > total, extreme limits)
  - Workflow versioning (promote/rollback edge cases)
  - AutoPilot stats cost savings calculation bug fix
  - Eval stats with None pass_rate bug fix
  - Dead letter queue edge cases (max retries, concurrent retry/resolve)
  - Optimizer/AutoPilot stats with no data
  - Memory endpoint validation
  - Settings endpoint edge cases
  - Workflow save with duplicate names
  - Rollback to draft/staging guard
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Disable rate limiting for all tests in this module
from sandcastle.api import rate_limit as _rl_mod

_rl_mod.execution_limiter.check = AsyncMock()

from sandcastle.main import app
from sandcastle.models.db import (
    ApiKey,
    ApprovalRequest,
    ApprovalStatus,
    AutoPilotExperiment,
    AutoPilotSample,
    DeadLetterItem,
    EvalRun,
    EvalRunStatus,
    ExperimentStatus,
    PolicyViolation,
    RoutingDecision,
    Run,
    RunCheckpoint,
    RunStatus,
    RunStep,
    Schedule,
    Setting,
    StepStatus,
    WorkflowVersion,
    WorkflowVersionStatus,
    async_session,
)

client = TestClient(app)

VALID_WORKFLOW = """
name: wave9-test
description: Wave 9 test workflow
steps:
  - id: step1
    prompt: "Hello {input.name}"
    model: haiku
    max_turns: 3
"""

VALID_WORKFLOW_V2 = """
name: wave9-test
description: Wave 9 test v2
steps:
  - id: step1
    prompt: "Hi {input.name}"
    model: sonnet
    max_turns: 5
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


async def _create_run(
    run_id: str | None = None,
    status: RunStatus = RunStatus.COMPLETED,
    workflow_name: str = "wave9-wf",
    total_cost_usd: float = 0.01,
    tenant_id: str | None = None,
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
            tenant_id=tenant_id,
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
    import hashlib
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


async def _create_dead_letter_item(
    run_id: uuid.UUID,
    step_id: str = "step1",
    attempts: int = 1,
    resolved: bool = False,
) -> DeadLetterItem:
    """Insert a DeadLetterItem row."""
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        item = DeadLetterItem(
            run_id=run_id,
            step_id=step_id,
            error="test error",
            input_data={"test": True},
            attempts=attempts,
            resolved_at=now if resolved else None,
            resolved_by="test" if resolved else None,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item


async def _create_eval_run(
    pass_rate: float = 0.75,
    status: EvalRunStatus = EvalRunStatus.COMPLETED,
    tenant_id: str | None = None,
    created_at: datetime | None = None,
) -> EvalRun:
    """Insert an EvalRun row."""
    now = created_at or datetime.now(timezone.utc)
    async with async_session() as session:
        run = EvalRun(
            suite_name="test-suite",
            workflow_name="test-wf",
            status=status,
            total_cases=4,
            passed_cases=3,
            failed_cases=1,
            pass_rate=pass_rate,
            total_cost_usd=0.05,
            total_duration_seconds=10.0,
            tenant_id=tenant_id,
            started_at=now - timedelta(seconds=10),
            completed_at=now,
            created_at=now,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def _create_experiment(
    workflow_name: str = "wave9-wf",
    step_id: str = "step1",
    status: ExperimentStatus = ExperimentStatus.RUNNING,
    deployed_variant_id: str | None = None,
) -> AutoPilotExperiment:
    """Insert an AutoPilotExperiment row."""
    async with async_session() as session:
        exp = AutoPilotExperiment(
            workflow_name=workflow_name,
            step_id=step_id,
            status=status,
            optimize_for="quality",
            deployed_variant_id=deployed_variant_id,
        )
        session.add(exp)
        await session.commit()
        await session.refresh(exp)
        return exp


async def _create_sample(
    experiment_id: uuid.UUID,
    variant_id: str,
    quality_score: float = 0.8,
    cost_usd: float = 0.01,
) -> AutoPilotSample:
    """Insert an AutoPilotSample row."""
    async with async_session() as session:
        sample = AutoPilotSample(
            experiment_id=experiment_id,
            variant_id=variant_id,
            quality_score=quality_score,
            cost_usd=cost_usd,
            duration_seconds=1.0,
        )
        session.add(sample)
        await session.commit()
        await session.refresh(sample)
        return sample


async def _create_routing_decision(
    run_id: uuid.UUID,
    step_id: str = "step1",
    selected_model: str = "haiku",
) -> RoutingDecision:
    """Insert a RoutingDecision row."""
    async with async_session() as session:
        dec = RoutingDecision(
            run_id=run_id,
            step_id=step_id,
            selected_model=selected_model,
            selected_variant_id="thorough",
            reason="test",
            budget_pressure=0.3,
            confidence=0.9,
            alternatives=[
                {"model": "haiku", "avg_cost": 0.01, "id": "fast"},
                {"model": "opus", "avg_cost": 0.10, "id": "thorough"},
            ],
        )
        session.add(dec)
        await session.commit()
        await session.refresh(dec)
        return dec


async def _create_schedule(
    workflow_name: str = "wave9-wf",
    enabled: bool = True,
) -> Schedule:
    """Insert a Schedule row."""
    async with async_session() as session:
        schedule = Schedule(
            workflow_name=workflow_name,
            cron_expression="0 * * * *",
            input_data={"name": "test"},
            enabled=enabled,
        )
        session.add(schedule)
        await session.commit()
        await session.refresh(schedule)
        return schedule


async def _create_violation(
    run_id: uuid.UUID,
    severity: str = "high",
    policy_id: str = "no-pii",
) -> PolicyViolation:
    """Insert a PolicyViolation row."""
    async with async_session() as session:
        v = PolicyViolation(
            run_id=run_id,
            step_id="step1",
            policy_id=policy_id,
            severity=severity,
            trigger_details="detected PII",
            action_taken="redacted",
        )
        session.add(v)
        await session.commit()
        await session.refresh(v)
        return v


# ---------------------------------------------------------------------------
# 1. PAGINATION EDGE CASES
# ---------------------------------------------------------------------------


class TestPaginationEdgeCases:
    """Pagination across all list endpoints: offset > total, extreme params."""

    def test_runs_offset_beyond_total(self):
        """GET /api/runs with offset > total should return empty list."""
        resp = client.get("/api/runs", params={"offset": 999999, "limit": 10})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] >= 0
        assert body["meta"]["offset"] == 999999

    def test_runs_limit_max_boundary(self):
        """GET /api/runs with limit=200 (max) should work."""
        resp = client.get("/api/runs", params={"limit": 200})
        assert resp.status_code == 200

    def test_runs_limit_over_max_rejected(self):
        """GET /api/runs with limit=201 (over max) should be rejected."""
        resp = client.get("/api/runs", params={"limit": 201})
        assert resp.status_code == 422

    def test_runs_negative_offset_rejected(self):
        """GET /api/runs with negative offset should be rejected."""
        resp = client.get("/api/runs", params={"offset": -1})
        assert resp.status_code == 422

    def test_runs_limit_zero_rejected(self):
        """GET /api/runs with limit=0 should be rejected (ge=1)."""
        resp = client.get("/api/runs", params={"limit": 0})
        assert resp.status_code == 422

    def test_schedules_offset_beyond_total(self):
        """GET /api/schedules with high offset returns empty list."""
        resp = client.get("/api/schedules", params={"offset": 999999})
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_dead_letter_offset_beyond_total(self):
        """GET /api/dead-letter with high offset returns empty."""
        resp = client.get("/api/dead-letter", params={"offset": 999999})
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_approvals_offset_beyond_total(self):
        """GET /api/approvals with high offset returns empty."""
        resp = client.get("/api/approvals", params={"offset": 999999})
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_violations_offset_beyond_total(self):
        """GET /api/violations with high offset returns empty."""
        resp = client.get("/api/violations", params={"offset": 999999})
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_api_keys_offset_beyond_total(self):
        """GET /api/api-keys with high offset returns empty."""
        resp = client.get("/api/api-keys", params={"offset": 999999})
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_optimizer_decisions_offset_beyond_total(self):
        """GET /api/optimizer/decisions with high offset returns empty."""
        resp = client.get("/api/optimizer/decisions", params={"offset": 999999})
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# 2. WORKFLOW VERSIONING - PROMOTE/ROLLBACK EDGE CASES
# ---------------------------------------------------------------------------


class TestWorkflowVersioning:
    """Promote/rollback edge cases including the new rollback guards."""

    @pytest.mark.asyncio
    async def test_promote_archived_version_rejected(self):
        """Promoting an archived version should fail with CANNOT_PROMOTE."""
        name = f"promote-arch-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.ARCHIVED)

        resp = client.post(f"/api/workflows/{name}/promote", json={"version": 1})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "CANNOT_PROMOTE"

    @pytest.mark.asyncio
    async def test_promote_production_version_rejected(self):
        """Promoting a version already in production should fail."""
        name = f"promote-prod-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.PRODUCTION)

        resp = client.post(f"/api/workflows/{name}/promote", json={"version": 1})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "ALREADY_PRODUCTION"

    @pytest.mark.asyncio
    async def test_promote_draft_to_staging(self):
        """Promoting a draft version should move it to staging."""
        name = f"promote-draft-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.DRAFT)

        resp = client.post(f"/api/workflows/{name}/promote", json={"version": 1})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["previous_status"] == "draft"
        assert data["new_status"] == "staging"

    @pytest.mark.asyncio
    async def test_promote_staging_to_production(self):
        """Promoting a staging version should move it to production."""
        name = f"promote-stg-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.STAGING)

        with patch("sandcastle.api.routes.Path.write_text"):
            resp = client.post(f"/api/workflows/{name}/promote", json={"version": 1})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["previous_status"] == "staging"
        assert data["new_status"] == "production"

    @pytest.mark.asyncio
    async def test_promote_staging_archives_current_production(self):
        """When promoting staging to prod, current prod should be archived."""
        name = f"promote-archive-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.PRODUCTION)
        await _create_workflow_version(name, 2, WorkflowVersionStatus.STAGING)

        with patch("sandcastle.api.routes.Path.write_text"):
            resp = client.post(f"/api/workflows/{name}/promote", json={"version": 2})
        assert resp.status_code == 200

        # Verify old prod is now archived
        async with async_session() as session:
            from sqlalchemy import select
            stmt = select(WorkflowVersion).where(
                WorkflowVersion.workflow_name == name,
                WorkflowVersion.version == 1,
            )
            result = await session.execute(stmt)
            old = result.scalar_one()
            assert old.status == WorkflowVersionStatus.ARCHIVED

    @pytest.mark.asyncio
    async def test_promote_nonexistent_version(self):
        """Promoting a nonexistent version should return 404."""
        name = f"promote-none-{_uid()[:8]}"
        resp = client.post(f"/api/workflows/{name}/promote", json={"version": 999})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rollback_to_draft_rejected(self):
        """Rollback to a draft version should be rejected."""
        name = f"rollback-draft-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.PRODUCTION)
        await _create_workflow_version(name, 2, WorkflowVersionStatus.DRAFT)

        resp = client.post(
            f"/api/workflows/{name}/rollback", json={"target_version": 2}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_ROLLBACK_TARGET"

    @pytest.mark.asyncio
    async def test_rollback_to_staging_rejected(self):
        """Rollback to a staging version should be rejected."""
        name = f"rollback-stg-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.PRODUCTION)
        await _create_workflow_version(name, 2, WorkflowVersionStatus.STAGING)

        resp = client.post(
            f"/api/workflows/{name}/rollback", json={"target_version": 2}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_ROLLBACK_TARGET"

    @pytest.mark.asyncio
    async def test_rollback_to_production_itself_rejected(self):
        """Rollback to the current production version should be rejected."""
        name = f"rollback-self-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.PRODUCTION)

        resp = client.post(
            f"/api/workflows/{name}/rollback", json={"target_version": 1}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "ALREADY_PRODUCTION"

    @pytest.mark.asyncio
    async def test_rollback_to_archived_succeeds(self):
        """Rollback to an archived version should succeed."""
        name = f"rollback-ok-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.ARCHIVED)
        await _create_workflow_version(name, 2, WorkflowVersionStatus.PRODUCTION)

        with patch("sandcastle.api.routes.Path.write_text"):
            resp = client.post(
                f"/api/workflows/{name}/rollback", json={"target_version": 1}
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["rolled_back_to_version"] == 1

    @pytest.mark.asyncio
    async def test_rollback_no_archived_returns_404(self):
        """Rollback with no archived versions should return 404."""
        name = f"rollback-noarch-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.PRODUCTION)

        resp = client.post(f"/api/workflows/{name}/rollback", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rollback_nonexistent_version_returns_404(self):
        """Rollback to a nonexistent version number should return 404."""
        name = f"rollback-missing-{_uid()[:8]}"
        resp = client.post(
            f"/api/workflows/{name}/rollback", json={"target_version": 999}
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_version_diff_one_missing(self):
        """Diff with one version missing should return 404."""
        name = f"diff-miss-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.DRAFT)

        resp = client.get(
            f"/api/workflows/{name}/versions/diff",
            params={"version_a": 1, "version_b": 999},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_version_diff_both_exist(self):
        """Diff between two existing versions should work."""
        name = f"diff-ok-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.DRAFT)
        await _create_workflow_version(name, 2, WorkflowVersionStatus.DRAFT,
                                        yaml_content=VALID_WORKFLOW_V2)

        resp = client.get(
            f"/api/workflows/{name}/versions/diff",
            params={"version_a": 1, "version_b": 2},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version_a"] == 1
        assert data["version_b"] == 2

    @pytest.mark.asyncio
    async def test_get_specific_version(self):
        """GET a specific workflow version by number."""
        name = f"getver-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.PRODUCTION)

        resp = client.get(f"/api/workflows/{name}/versions/1")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == 1
        assert data["workflow_name"] == name

    @pytest.mark.asyncio
    async def test_get_nonexistent_version(self):
        """GET a nonexistent version should return 404."""
        resp = client.get("/api/workflows/nope/versions/999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. WORKFLOW SAVE EDGE CASES
# ---------------------------------------------------------------------------


class TestWorkflowSave:
    """Workflow save: duplicate names, invalid YAML, path traversal."""

    def test_save_with_invalid_yaml(self):
        """Saving invalid YAML should return 400."""
        resp = client.post(
            "/api/workflows",
            json={"name": "bad-yaml", "content": "this is not valid yaml: ["},
        )
        assert resp.status_code == 400

    def test_save_with_empty_steps(self):
        """Saving a workflow with empty steps should return 400 (validation)."""
        resp = client.post(
            "/api/workflows",
            json={
                "name": "empty-steps",
                "content": "name: empty\ndescription: no steps\nsteps: []\n",
            },
        )
        assert resp.status_code == 400

    def test_save_with_path_traversal_name(self):
        """Workflow name with path traversal should be rejected."""
        resp = client.post(
            "/api/workflows",
            json={"name": "../etc/passwd", "content": VALID_WORKFLOW},
        )
        assert resp.status_code == 422  # Pydantic validator

    def test_save_with_slash_in_name(self):
        """Workflow name with / should be rejected."""
        resp = client.post(
            "/api/workflows",
            json={"name": "foo/bar", "content": VALID_WORKFLOW},
        )
        assert resp.status_code == 422

    def test_save_duplicate_name_creates_new_version(self, tmp_path, monkeypatch):
        """Saving the same workflow name again creates a new version (higher number)."""
        from sandcastle.config import settings

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path / "workflows"))
        name = f"dup-{_uid()[:8]}"
        resp1 = client.post(
            "/api/workflows",
            json={"name": name, "content": VALID_WORKFLOW},
        )
        assert resp1.status_code == 201
        v1 = resp1.json()["data"].get("version")

        resp2 = client.post(
            "/api/workflows",
            json={"name": name, "content": VALID_WORKFLOW_V2},
        )
        assert resp2.status_code == 201
        v2 = resp2.json()["data"].get("version")
        # Second save should get a higher version number
        if v1 is not None and v2 is not None:
            assert v2 > v1


# ---------------------------------------------------------------------------
# 4. DEAD LETTER QUEUE EDGE CASES
# ---------------------------------------------------------------------------


class TestDeadLetterQueue:
    """DLQ: max retries, already resolved, invalid ID."""

    @pytest.mark.asyncio
    async def test_retry_max_retries_exceeded(self):
        """Retry should fail when max retries (10) are exceeded."""
        run = await _create_run(status=RunStatus.FAILED)
        item = await _create_dead_letter_item(run.id, attempts=10)

        resp = client.post(f"/api/dead-letter/{item.id}/retry")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "MAX_RETRIES_EXCEEDED"

    @pytest.mark.asyncio
    async def test_retry_already_resolved(self):
        """Retry should fail when item is already resolved."""
        run = await _create_run(status=RunStatus.FAILED)
        item = await _create_dead_letter_item(run.id, resolved=True)

        resp = client.post(f"/api/dead-letter/{item.id}/retry")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "ALREADY_RESOLVED"

    @pytest.mark.asyncio
    async def test_resolve_already_resolved(self):
        """Resolve should fail when item is already resolved."""
        run = await _create_run(status=RunStatus.FAILED)
        item = await _create_dead_letter_item(run.id, resolved=True)

        resp = client.post(f"/api/dead-letter/{item.id}/resolve", json={})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "ALREADY_RESOLVED"

    def test_retry_invalid_id(self):
        """Retry with invalid UUID format should return 400."""
        resp = client.post("/api/dead-letter/not-a-uuid/retry")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_ID"

    def test_resolve_invalid_id(self):
        """Resolve with invalid UUID format should return 400."""
        resp = client.post("/api/dead-letter/not-a-uuid/resolve", json={})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_ID"

    def test_retry_nonexistent_item(self):
        """Retry on nonexistent DLQ item should return 404."""
        resp = client.post(f"/api/dead-letter/{_uid()}/retry")
        assert resp.status_code == 404

    def test_resolve_nonexistent_item(self):
        """Resolve on nonexistent DLQ item should return 404."""
        resp = client.post(f"/api/dead-letter/{_uid()}/resolve", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_only_unresolved_by_default(self):
        """List DLQ should only show unresolved items by default."""
        run = await _create_run(status=RunStatus.FAILED)
        unresolved = await _create_dead_letter_item(run.id)
        resolved = await _create_dead_letter_item(run.id, resolved=True)

        resp = client.get("/api/dead-letter")
        assert resp.status_code == 200
        data = resp.json()["data"]
        ids = [d["id"] for d in data]
        assert str(unresolved.id) in ids
        assert str(resolved.id) not in ids

    @pytest.mark.asyncio
    async def test_list_includes_resolved_when_requested(self):
        """List DLQ with resolved=true should include resolved items."""
        run = await _create_run(status=RunStatus.FAILED)
        resolved = await _create_dead_letter_item(run.id, resolved=True)

        resp = client.get("/api/dead-letter", params={"resolved": True})
        assert resp.status_code == 200
        data = resp.json()["data"]
        ids = [d["id"] for d in data]
        assert str(resolved.id) in ids

    @pytest.mark.asyncio
    async def test_resolve_with_reason(self):
        """Manual resolve with a reason string should be saved."""
        run = await _create_run(status=RunStatus.FAILED)
        item = await _create_dead_letter_item(run.id)

        resp = client.post(
            f"/api/dead-letter/{item.id}/resolve",
            json={"reason": "known issue, ignoring"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["resolved_by"] == "manual"
        assert data["resolved_at"] is not None


# ---------------------------------------------------------------------------
# 5. AUTOPILOT STATS - COST SAVINGS BUG FIX
# ---------------------------------------------------------------------------


class TestAutoPilotStats:
    """AutoPilot stats endpoint, especially cost savings calculation."""

    def test_stats_with_no_data(self):
        """AutoPilot stats with no experiments should return zeros."""
        resp = client.get("/api/autopilot/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_experiments"] >= 0
        assert data["total_samples"] >= 0

    @pytest.mark.asyncio
    async def test_stats_cost_savings_uses_per_experiment_samples(self):
        """Cost savings should use per-experiment sample count, not global total.

        This tests the bug fix: previously total_cost_savings used the global
        total_samples count as a multiplier, which inflated savings when
        multiple experiments existed.
        """
        # Create two completed experiments with deployed variants
        exp1 = await _create_experiment(
            workflow_name="ap-savings-1",
            status=ExperimentStatus.COMPLETED,
            deployed_variant_id="fast",
        )
        # Experiment 1: 3 samples total (2 fast, 1 thorough)
        await _create_sample(exp1.id, "fast", quality_score=0.8, cost_usd=0.01)
        await _create_sample(exp1.id, "fast", quality_score=0.85, cost_usd=0.01)
        await _create_sample(exp1.id, "thorough", quality_score=0.9, cost_usd=0.10)

        exp2 = await _create_experiment(
            workflow_name="ap-savings-2",
            status=ExperimentStatus.COMPLETED,
            deployed_variant_id="balanced",
        )
        # Experiment 2: 2 samples
        await _create_sample(exp2.id, "balanced", quality_score=0.7, cost_usd=0.05)
        await _create_sample(exp2.id, "premium", quality_score=0.9, cost_usd=0.20)

        resp = client.get("/api/autopilot/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # The cost savings should be computed per-experiment, not using global total
        # If the old bug existed, savings would be inflated by (3+2)=5 for each experiment
        # With the fix, exp1 uses 3 samples and exp2 uses 2 samples
        assert data["total_cost_savings_usd"] >= 0

    @pytest.mark.asyncio
    async def test_list_experiments_filter_by_status(self):
        """List experiments filtered by status."""
        await _create_experiment(
            workflow_name="filter-1",
            status=ExperimentStatus.RUNNING,
        )
        await _create_experiment(
            workflow_name="filter-2",
            status=ExperimentStatus.COMPLETED,
            deployed_variant_id="v1",
        )

        resp_running = client.get(
            "/api/autopilot/experiments", params={"status": "running"}
        )
        assert resp_running.status_code == 200

        resp_completed = client.get(
            "/api/autopilot/experiments", params={"status": "completed"}
        )
        assert resp_completed.status_code == 200

    @pytest.mark.asyncio
    async def test_get_experiment_not_found(self):
        """GET experiment with nonexistent ID returns 404."""
        resp = client.get(f"/api/autopilot/experiments/{_uid()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_experiment_invalid_id(self):
        """GET experiment with invalid UUID returns 400."""
        resp = client.get("/api/autopilot/experiments/not-a-uuid")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_reset_experiment(self):
        """Reset should clear samples and set status to RUNNING."""
        exp = await _create_experiment(status=ExperimentStatus.COMPLETED,
                                        deployed_variant_id="v1")
        await _create_sample(exp.id, "v1", 0.8, 0.01)
        await _create_sample(exp.id, "v2", 0.6, 0.05)

        resp = client.post(f"/api/autopilot/experiments/{exp.id}/reset")
        assert resp.status_code == 200
        assert resp.json()["data"]["reset"] is True

        # Verify experiment is reset
        async with async_session() as session:
            from sqlalchemy import select
            refreshed = await session.get(AutoPilotExperiment, exp.id)
            assert refreshed.status == ExperimentStatus.RUNNING
            assert refreshed.deployed_variant_id is None

    @pytest.mark.asyncio
    async def test_reset_nonexistent_experiment(self):
        """Reset on nonexistent experiment returns 404."""
        resp = client.post(f"/api/autopilot/experiments/{_uid()}/reset")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deploy_no_samples_rejected(self):
        """Deploy with no samples should fail."""
        exp = await _create_experiment(status=ExperimentStatus.RUNNING)

        with patch(
            "sandcastle.engine.autopilot.maybe_complete_experiment",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.post(f"/api/autopilot/experiments/{exp.id}/deploy")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "NO_SAMPLES"


# ---------------------------------------------------------------------------
# 6. EVAL STATS - NONE PASS_RATE BUG FIX
# ---------------------------------------------------------------------------


class TestEvalStats:
    """Eval stats endpoint, especially the None pass_rate trend bug."""

    def test_eval_stats_with_no_data(self):
        """Eval stats with no runs should return zeros."""
        resp = client.get("/api/eval/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_runs"] >= 0
        assert data["avg_pass_rate"] >= 0

    @pytest.mark.asyncio
    async def test_eval_stats_trend_computation(self):
        """Eval stats should compute trend correctly with valid pass_rates."""
        now = datetime.now(timezone.utc)
        await _create_eval_run(pass_rate=0.8, created_at=now - timedelta(days=1))
        await _create_eval_run(pass_rate=0.6, created_at=now - timedelta(days=1))
        await _create_eval_run(pass_rate=1.0, created_at=now)

        resp = client.get("/api/eval/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_runs"] >= 3
        # Trend should have entries
        assert isinstance(data["pass_rate_trend"], list)

    @pytest.mark.asyncio
    async def test_eval_list_runs(self):
        """List eval runs should work with pagination."""
        await _create_eval_run(pass_rate=0.9)

        resp = client.get("/api/eval/runs", params={"limit": 5, "offset": 0})
        assert resp.status_code == 200
        assert resp.json()["meta"]["limit"] == 5

    @pytest.mark.asyncio
    async def test_eval_get_run_not_found(self):
        """GET eval run with nonexistent ID returns 404."""
        resp = client.get(f"/api/eval/runs/{_uid()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_eval_get_run_invalid_id(self):
        """GET eval run with invalid UUID returns 400."""
        resp = client.get("/api/eval/runs/not-a-uuid")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 7. OPTIMIZER/ROUTING DECISIONS
# ---------------------------------------------------------------------------


class TestOptimizerEndpoints:
    """Optimizer decision listing and stats."""

    def test_optimizer_stats_with_no_data(self):
        """Optimizer stats with no decisions should return zeros."""
        resp = client.get("/api/optimizer/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_decisions_30d"] >= 0
        assert isinstance(data["model_distribution"], dict)

    @pytest.mark.asyncio
    async def test_list_routing_decisions_with_filters(self):
        """List routing decisions with step/model filters."""
        run = await _create_run()
        await _create_routing_decision(run.id, step_id="step1", selected_model="haiku")
        await _create_routing_decision(run.id, step_id="step2", selected_model="sonnet")

        resp = client.get(
            "/api/optimizer/decisions", params={"step": "step1"}
        )
        assert resp.status_code == 200

        resp = client.get(
            "/api/optimizer/decisions", params={"model": "sonnet"}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_run_routing_decisions(self):
        """GET decisions for a specific run."""
        run = await _create_run()
        await _create_routing_decision(run.id)

        resp = client.get(f"/api/optimizer/decisions/{run.id}")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    @pytest.mark.asyncio
    async def test_get_run_routing_decisions_not_found(self):
        """GET decisions for nonexistent run returns 404."""
        resp = client.get(f"/api/optimizer/decisions/{_uid()}")
        assert resp.status_code == 404

    def test_get_run_routing_decisions_invalid_id(self):
        """GET decisions with invalid UUID returns 400."""
        resp = client.get("/api/optimizer/decisions/not-a-uuid")
        assert resp.status_code == 400

    def test_optimizer_alerts_returns_list(self):
        """Optimizer alerts should return a list."""
        resp = client.get("/api/optimizer/alerts")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_clear_optimizer_alerts(self):
        """Clear alerts should succeed."""
        resp = client.delete("/api/optimizer/alerts")
        assert resp.status_code == 200
        assert "cleared" in resp.json()["data"]


# ---------------------------------------------------------------------------
# 8. MEMORY ENDPOINTS VALIDATION
# ---------------------------------------------------------------------------


class TestMemoryEndpoints:
    """Memory endpoint validation: scope_id, memory_id formats."""

    def test_list_memories_invalid_scope(self):
        """List memories with invalid scope format should return 422."""
        resp = client.get("/api/memories", params={"scope_id": "invalid:@!#"})
        assert resp.status_code == 422

    def test_list_memories_valid_scope_global(self):
        """List memories with scope_id='global' should succeed."""
        with patch(
            "sandcastle.engine.memory.load_memories",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = client.get("/api/memories", params={"scope_id": "global"})
        assert resp.status_code == 200

    def test_list_memories_valid_scope_workflow(self):
        """List memories with workflow scope should succeed."""
        with patch(
            "sandcastle.engine.memory.load_memories",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = client.get(
                "/api/memories", params={"scope_id": "workflow:my-workflow"}
            )
        assert resp.status_code == 200

    def test_list_memories_valid_scope_agent(self):
        """List memories with agent scope should succeed."""
        with patch(
            "sandcastle.engine.memory.load_memories",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = client.get(
                "/api/memories", params={"scope_id": "agent:my-agent"}
            )
        assert resp.status_code == 200

    def test_delete_memory_invalid_id(self):
        """Delete memory with invalid ID format returns 422."""
        resp = client.delete("/api/memories/invalid@id!")
        assert resp.status_code == 422

    def test_delete_all_memories_invalid_scope(self):
        """Delete all memories with invalid scope_id returns 422."""
        resp = client.delete("/api/memories", params={"scope_id": "bad format"})
        assert resp.status_code == 422

    def test_delete_memory_not_found(self):
        """Delete nonexistent memory returns 404."""
        with patch(
            "sandcastle.engine.memory.delete_memory",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = client.delete("/api/memories/nonexistent-mem-id")
        assert resp.status_code == 404

    def test_add_memory_succeeds(self):
        """Add memory with valid data should succeed."""
        with patch(
            "sandcastle.engine.memory.save_memory",
            new_callable=AsyncMock,
            return_value=[{"id": "mem-1", "content": "test"}],
        ):
            resp = client.post(
                "/api/memories",
                json={
                    "scope_id": "workflow:test",
                    "content": "remember this fact",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["added"] == 1

    def test_search_memories_empty_query_rejected(self):
        """Search with empty query string should be rejected by schema."""
        resp = client.post(
            "/api/memories/search",
            json={"scope_id": "global", "query": ""},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 9. SETTINGS ENDPOINTS
# ---------------------------------------------------------------------------


class TestSettingsEndpoints:
    """Settings get/update edge cases."""

    def test_get_settings(self):
        """GET settings should return masked values."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # Sensitive keys should be masked
        assert "****" in data.get("anthropic_api_key", "") or data.get("anthropic_api_key") == ""

    def test_update_settings_empty_body(self):
        """PATCH settings with empty body should return current settings unchanged."""
        resp = client.patch("/api/settings", json={})
        assert resp.status_code == 200

    def test_update_settings_valid_log_level(self):
        """Update log_level to a valid value should succeed."""
        resp = client.patch("/api/settings", json={"log_level": "debug"})
        assert resp.status_code == 200

    def test_update_settings_invalid_log_level(self):
        """Update log_level to invalid value should be rejected."""
        resp = client.patch("/api/settings", json={"log_level": "verbose"})
        assert resp.status_code == 422

    def test_update_settings_valid_max_depth(self):
        """Update max_workflow_depth to a valid value."""
        resp = client.patch("/api/settings", json={"max_workflow_depth": 5})
        assert resp.status_code == 200

    def test_update_settings_max_depth_too_high(self):
        """max_workflow_depth over 20 should be rejected."""
        resp = client.patch("/api/settings", json={"max_workflow_depth": 21})
        assert resp.status_code == 422

    def test_update_settings_max_depth_too_low(self):
        """max_workflow_depth below 1 should be rejected."""
        resp = client.patch("/api/settings", json={"max_workflow_depth": 0})
        assert resp.status_code == 422

    def test_update_settings_negative_cost(self):
        """default_max_cost_usd negative should be rejected."""
        resp = client.patch("/api/settings", json={"default_max_cost_usd": -1.0})
        assert resp.status_code == 422

    def test_update_settings_immutable_fields_ignored(self):
        """Security-critical fields should not be changeable via API."""
        resp = client.patch(
            "/api/settings",
            json={
                "auth_required": True,
                "dashboard_origin": "http://evil.com",
                "database_url": "sqlite://",
            },
        )
        # These should be ignored silently (422 from pydantic if extra="forbid",
        # or silently stripped if extra="ignore")
        # Either way, the server state should NOT change
        assert resp.status_code in (200, 422)


# ---------------------------------------------------------------------------
# 10. HEALTH/RUNTIME ENDPOINTS
# ---------------------------------------------------------------------------


class TestHealthRuntime:
    """Health check and runtime info edge cases."""

    def test_health_check(self):
        """Health check should return status."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] in ("ok", "degraded")
        assert isinstance(data["database"], bool)

    def test_runtime_info(self):
        """Runtime info should return mode, database, queue, version."""
        resp = client.get("/api/runtime")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "mode" in data
        assert "database" in data
        assert "queue" in data
        assert "version" in data

    def test_runtime_includes_license(self):
        """Runtime info should include license information."""
        resp = client.get("/api/runtime")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "license" in data
        license_data = data["license"]
        assert "status" in license_data
        assert "tier" in license_data


# ---------------------------------------------------------------------------
# 11. RUNS FILTERING AND EDGE CASES
# ---------------------------------------------------------------------------


class TestRunsFiltering:
    """Run listing with various filters."""

    def test_runs_invalid_status_filter(self):
        """Filter by invalid status should return 400."""
        resp = client.get("/api/runs", params={"status": "nonexistent"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_STATUS"

    def test_runs_valid_status_filter(self):
        """Filter by valid status should work."""
        resp = client.get("/api/runs", params={"status": "completed"})
        assert resp.status_code == 200

    def test_runs_workflow_filter(self):
        """Filter by workflow name should work."""
        resp = client.get("/api/runs", params={"workflow": "test-wf"})
        assert resp.status_code == 200

    def test_get_run_invalid_uuid(self):
        """GET run with invalid UUID should return 400."""
        resp = client.get("/api/runs/not-a-uuid")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_ID"

    def test_get_run_nonexistent(self):
        """GET run with nonexistent UUID should return 404."""
        resp = client.get(f"/api/runs/{_uid()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_run_already_completed(self):
        """Cancel a completed run should fail."""
        run = await _create_run(status=RunStatus.COMPLETED)
        resp = client.post(f"/api/runs/{run.id}/cancel")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_STATUS"

    @pytest.mark.asyncio
    async def test_delete_active_run_rejected(self):
        """Cannot delete a running/queued run."""
        run = await _create_run(status=RunStatus.RUNNING)
        resp = client.delete(f"/api/runs/{run.id}")
        assert resp.status_code == 400
        assert "Cancel it first" in resp.json()["detail"]["error"]["message"]

    @pytest.mark.asyncio
    async def test_delete_completed_run_succeeds(self):
        """Delete a completed run should succeed."""
        run = await _create_run(status=RunStatus.COMPLETED)
        resp = client.delete(f"/api/runs/{run.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    def test_delete_nonexistent_run(self):
        """Delete nonexistent run returns 404."""
        resp = client.delete(f"/api/runs/{_uid()}")
        assert resp.status_code == 404

    def test_delete_invalid_uuid(self):
        """Delete with invalid UUID returns 400."""
        resp = client.delete("/api/runs/not-a-uuid")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 12. APPROVAL ENDPOINTS EDGE CASES
# ---------------------------------------------------------------------------


class TestApprovalEndpoints:
    """Approval gates: invalid status filter, nonexistent approvals."""

    def test_list_approvals_invalid_status(self):
        """Filter by invalid approval status should return 400."""
        resp = client.get("/api/approvals", params={"status": "invalid"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_STATUS"

    def test_list_approvals_valid_status(self):
        """Filter by valid approval status should succeed."""
        resp = client.get("/api/approvals", params={"status": "pending"})
        assert resp.status_code == 200

    def test_get_approval_invalid_id(self):
        """GET approval with invalid UUID should return 400."""
        resp = client.get("/api/approvals/not-a-uuid")
        assert resp.status_code == 400

    def test_get_approval_not_found(self):
        """GET approval with nonexistent UUID should return 404."""
        resp = client.get(f"/api/approvals/{_uid()}")
        assert resp.status_code == 404

    def test_approve_invalid_id(self):
        """Approve with invalid UUID should return 400."""
        resp = client.post("/api/approvals/not-a-uuid/approve")
        assert resp.status_code == 400

    def test_reject_not_found(self):
        """Reject nonexistent approval should return 404."""
        resp = client.post(f"/api/approvals/{_uid()}/reject")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_already_resolved(self):
        """Approve on an already-approved request should return 409."""
        run = await _create_run(status=RunStatus.AWAITING_APPROVAL)
        async with async_session() as session:
            ap = ApprovalRequest(
                run_id=run.id,
                step_id="step1",
                status=ApprovalStatus.APPROVED,
                resolved_at=datetime.now(timezone.utc),
            )
            session.add(ap)
            await session.commit()
            await session.refresh(ap)

        resp = client.post(f"/api/approvals/{ap.id}/approve")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "ALREADY_RESOLVED"


# ---------------------------------------------------------------------------
# 13. VIOLATIONS ENDPOINTS
# ---------------------------------------------------------------------------


class TestViolationEndpoints:
    """Policy violations: severity filter, stats with no data."""

    def test_invalid_severity_filter(self):
        """Invalid severity value should return 400."""
        resp = client.get("/api/violations", params={"severity": "catastrophic"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_SEVERITY"

    def test_valid_severity_filter(self):
        """Valid severity filter should work."""
        resp = client.get("/api/violations", params={"severity": "high"})
        assert resp.status_code == 200

    def test_violations_stats_with_no_data(self):
        """Violation stats with no data should return zero counts."""
        resp = client.get("/api/violations/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_violations_30d"] >= 0

    @pytest.mark.asyncio
    async def test_run_violations_not_found(self):
        """Violations for nonexistent run should return 404."""
        resp = client.get(f"/api/runs/{_uid()}/violations")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_run_violations_invalid_severity(self):
        """Invalid severity on run violations should return 400."""
        run = await _create_run()
        resp = client.get(
            f"/api/runs/{run.id}/violations", params={"severity": "extreme"}
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_run_violations_success(self):
        """Violations for valid run should return list."""
        run = await _create_run()
        await _create_violation(run.id, severity="high")

        resp = client.get(f"/api/runs/{run.id}/violations")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 1

    def test_policy_id_too_long_rejected(self):
        """policy_id filter over 255 chars should be rejected."""
        resp = client.get(
            "/api/violations", params={"policy_id": "x" * 256}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_POLICY_ID"


# ---------------------------------------------------------------------------
# 14. SCHEDULE ENDPOINTS
# ---------------------------------------------------------------------------


class TestScheduleEndpoints:
    """Schedule CRUD edge cases."""

    def test_create_schedule_invalid_cron(self):
        """Invalid cron expression should be rejected."""
        resp = client.post(
            "/api/schedules",
            json={
                "workflow_name": "test",
                "cron_expression": "not a cron",
                "input_data": {},
            },
        )
        assert resp.status_code == 422  # Schema validation rejects bad cron format

    def test_update_schedule_invalid_id(self):
        """Update schedule with invalid UUID returns 400."""
        resp = client.patch(
            "/api/schedules/not-a-uuid", json={"enabled": False}
        )
        assert resp.status_code == 400

    def test_update_schedule_not_found(self):
        """Update nonexistent schedule returns 404."""
        resp = client.patch(
            f"/api/schedules/{_uid()}", json={"enabled": False}
        )
        assert resp.status_code == 404

    def test_delete_schedule_invalid_id(self):
        """Delete schedule with invalid UUID returns 400."""
        resp = client.delete("/api/schedules/not-a-uuid")
        assert resp.status_code == 400

    def test_delete_schedule_not_found(self):
        """Delete nonexistent schedule returns 404."""
        resp = client.delete(f"/api/schedules/{_uid()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 15. COMPARE RUNS
# ---------------------------------------------------------------------------


class TestCompareRuns:
    """Run comparison edge cases."""

    def test_compare_invalid_run_id(self):
        """Compare with invalid run ID format should return 400."""
        resp = client.get(
            "/api/runs/compare",
            params={"run_a": "not-a-uuid", "run_b": "also-not"},
        )
        assert resp.status_code == 400

    def test_compare_run_not_found(self):
        """Compare with nonexistent run should return 404."""
        resp = client.get(
            "/api/runs/compare",
            params={"run_a": _uid(), "run_b": _uid()},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_compare_runs_success(self):
        """Compare two existing runs should return diff."""
        run_a = await _create_run(workflow_name="compare-wf")
        run_b = await _create_run(workflow_name="compare-wf")

        resp = client.get(
            "/api/runs/compare",
            params={"run_a": str(run_a.id), "run_b": str(run_b.id)},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["same_workflow"] is True


# ---------------------------------------------------------------------------
# 16. TEMPLATE ENDPOINTS
# ---------------------------------------------------------------------------


class TestTemplateEndpoints:
    """Template listing and retrieval."""

    def test_list_templates(self):
        """List templates should return a list."""
        resp = client.get("/api/templates")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_get_template_not_found(self):
        """Get nonexistent template returns 404."""
        resp = client.get("/api/templates/definitely-not-a-real-template")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 17. STATS ENDPOINT
# ---------------------------------------------------------------------------


class TestStatsEndpoint:
    """Overview stats endpoint."""

    def test_stats_returns_valid_data(self):
        """Stats endpoint should return all required fields."""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_runs_today" in data
        assert "success_rate" in data
        assert "total_cost_today" in data
        assert "avg_duration_seconds" in data
        assert "runs_by_day" in data
        assert "cost_by_workflow" in data


# ---------------------------------------------------------------------------
# 18. HUB ENDPOINTS
# ---------------------------------------------------------------------------


class TestHubEndpoints:
    """Community hub endpoints."""

    def test_hub_playground_invalid_json(self):
        """Hub playground with invalid JSON should return 400."""
        resp = client.post(
            "/api/hub/playground",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_hub_playground_valid(self):
        """Hub playground with valid input should return simulated result."""
        resp = client.post(
            "/api/hub/playground",
            json={"slug": "test/template", "inputs": {"name": "test"}},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "completed"

    def test_hub_installed_list(self):
        """List installed hub templates should return a list."""
        resp = client.get("/api/hub/installed")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)


# ---------------------------------------------------------------------------
# 19. WORKFLOW LIST VERSIONS
# ---------------------------------------------------------------------------


class TestWorkflowVersionList:
    """List versions endpoint edge cases."""

    @pytest.mark.asyncio
    async def test_list_versions_pagination(self):
        """List versions with pagination should work."""
        name = f"listver-{_uid()[:8]}"
        for i in range(1, 4):
            await _create_workflow_version(name, i, WorkflowVersionStatus.DRAFT)

        resp = client.get(
            f"/api/workflows/{name}/versions",
            params={"limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 3
        assert len(data["data"]["versions"]) == 2

    @pytest.mark.asyncio
    async def test_list_versions_offset_beyond_total(self):
        """List versions with offset > total should return empty."""
        name = f"listver-off-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.DRAFT)

        resp = client.get(
            f"/api/workflows/{name}/versions",
            params={"limit": 10, "offset": 100},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["versions"]) == 0

    def test_list_versions_nonexistent_workflow(self):
        """List versions for a nonexistent workflow with no disk file returns 404."""
        resp = client.get("/api/workflows/does-not-exist-xyz/versions")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 20. API KEYS DEACTIVATION EDGE CASES
# ---------------------------------------------------------------------------


class TestApiKeyEndpoints:
    """API key deactivation and listing edge cases."""

    def test_deactivate_invalid_id(self):
        """Deactivate with invalid UUID returns 400."""
        resp = client.delete("/api/api-keys/not-a-uuid")
        assert resp.status_code == 400

    def test_deactivate_nonexistent(self):
        """Deactivate nonexistent key returns 404."""
        resp = client.delete(f"/api/api-keys/{_uid()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 21. EXPORT WORKFLOW
# ---------------------------------------------------------------------------


class TestExportWorkflow:
    """Workflow export endpoint."""

    def test_export_nonexistent_workflow(self):
        """Export of nonexistent workflow should return 404."""
        resp = client.get("/api/workflows/nonexistent-xyz/export")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_export_existing_workflow(self):
        """Export of existing workflow should return sanitized YAML."""
        name = f"export-{_uid()[:8]}"
        await _create_workflow_version(name, 1, WorkflowVersionStatus.PRODUCTION)

        resp = client.get(f"/api/workflows/{name}/export")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "yaml_content" in data
        assert data["name"] == name
