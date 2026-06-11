"""Tests for the Self-Healing Workflows engine and API."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.config import settings
from sandcastle.engine.healer import (
    _make_diff,
    _parse_llm_response,
    check_heal_resolutions,
    heal_item,
    run_healer_pass,
)

BROKEN_YAML = """
name: {name}
description: Workflow that fails at summarize
steps:
  - id: fetch
    prompt: "Fetch the document at {{input.url}}"
  - id: summarize
    prompt: "Summarize {{steps.fetch.output}}"
    depends_on: [fetch]
"""

PATCHED_YAML = """
name: {name}
description: Workflow that fails at summarize
steps:
  - id: fetch
    prompt: "Fetch the document at {{input.url}} and return plain text"
  - id: summarize
    prompt: "Summarize the following text: {{steps.fetch.output}}"
    depends_on: [fetch]
"""


def _llm_response(patched_yaml: str, confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "diagnosis": "The summarize step received empty output; clarified the fetch prompt.",
            "confidence": confidence,
            "patched_yaml": patched_yaml,
        }
    )


async def _make_failure(workflow_name: str, with_version: bool = True):
    """Create a failed run + unresolved dead-letter item (+ v1 production version)."""
    from sandcastle.models.db import (
        DeadLetterItem,
        Run,
        RunStatus,
        WorkflowVersion,
        WorkflowVersionStatus,
        async_session,
    )

    run_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            Run(
                id=run_id,
                workflow_name=workflow_name,
                status=RunStatus.FAILED,
                error="step summarize failed",
            )
        )
        await session.commit()

    async with async_session() as session:
        if with_version:
            session.add(
                WorkflowVersion(
                    workflow_name=workflow_name,
                    version=1,
                    status=WorkflowVersionStatus.PRODUCTION,
                    yaml_content=BROKEN_YAML.format(name=workflow_name),
                    description="initial",
                    steps_count=2,
                    checksum="x" * 64,
                )
            )
        item = DeadLetterItem(
            run_id=run_id,
            step_id="summarize",
            error="LLM returned empty output after 3 attempts",
            attempts=3,
        )
        session.add(item)
        await session.commit()
        return run_id, item.id


# --- LLM response parsing ---


class TestParseLlmResponse:
    def test_parses_plain_json(self):
        diagnosis, confidence, patched = _parse_llm_response(_llm_response("name: x", 0.7))
        assert "summarize step" in diagnosis
        assert confidence == 0.7
        assert patched == "name: x"

    def test_strips_markdown_fences(self):
        raw = "```json\n" + _llm_response("name: x") + "\n```"
        _, confidence, patched = _parse_llm_response(raw)
        assert confidence == 0.9
        assert patched == "name: x"

    def test_clamps_confidence(self):
        _, confidence, _ = _parse_llm_response(_llm_response("name: x", 7.5))
        assert confidence == 1.0

    def test_rejects_non_json(self):
        with pytest.raises(ValueError):
            _parse_llm_response("here is the patch: ...")

    def test_rejects_missing_keys(self):
        with pytest.raises(ValueError):
            _parse_llm_response(json.dumps({"diagnosis": "x"}))


def test_make_diff_contains_change():
    diff = _make_diff("a: 1\nb: 2\n", "a: 1\nb: 3\n", "wf")
    assert "-b: 2" in diff
    assert "+b: 3" in diff


# --- heal_item: propose / auto-apply / reject / limits ---


class TestHealItem:
    @pytest.mark.asyncio
    async def test_patch_proposed_and_approval_created(self, monkeypatch, tmp_path):
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            HealAttempt,
            WorkflowVersion,
            WorkflowVersionStatus,
            async_session,
        )

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        name = f"heal-propose-{uuid.uuid4().hex[:8]}"
        run_id, item_id = await _make_failure(name)

        with patch(
            "sandcastle.engine.healer._call_llm",
            new=AsyncMock(return_value=_llm_response(PATCHED_YAML.format(name=name))),
        ):
            result = await heal_item(item_id)

        assert result["status"] == "proposed"
        assert result["to_version"] == 2

        from sqlalchemy import select

        async with async_session() as session:
            attempt = (
                await session.execute(
                    select(HealAttempt).where(HealAttempt.dead_letter_id == item_id)
                )
            ).scalar_one()
            assert attempt.status == "proposed"
            assert attempt.from_version == 1
            assert attempt.to_version == 2
            assert attempt.confidence == 0.9
            assert "summarize step" in attempt.diagnosis
            assert attempt.diff and "+" in attempt.diff

            wv = (
                await session.execute(
                    select(WorkflowVersion).where(
                        WorkflowVersion.workflow_name == name, WorkflowVersion.version == 2
                    )
                )
            ).scalar_one()
            assert wv.status == WorkflowVersionStatus.DRAFT
            assert wv.created_by == "healer"

            approval = await session.get(ApprovalRequest, attempt.approval_id)
            assert approval.status == ApprovalStatus.PENDING
            assert approval.request_data["type"] == "healer"
            assert approval.request_data["to_version"] == 2
            assert "Diagnosis" in approval.message

    @pytest.mark.asyncio
    async def test_auto_apply_publishes_and_auto_approves(self, monkeypatch, tmp_path):
        from sandcastle.models.db import (
            ApprovalRequest,
            ApprovalStatus,
            HealAttempt,
            WorkflowVersion,
            WorkflowVersionStatus,
            async_session,
        )

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        monkeypatch.setattr(settings, "healer_auto_apply", True)
        name = f"heal-auto-{uuid.uuid4().hex[:8]}"
        run_id, item_id = await _make_failure(name)

        with patch(
            "sandcastle.engine.healer._call_llm",
            new=AsyncMock(return_value=_llm_response(PATCHED_YAML.format(name=name), 0.95)),
        ):
            result = await heal_item(item_id)

        assert result["status"] == "auto_applied"

        from sqlalchemy import select

        async with async_session() as session:
            attempt = (
                await session.execute(
                    select(HealAttempt).where(HealAttempt.dead_letter_id == item_id)
                )
            ).scalar_one()
            assert attempt.status == "auto_applied"
            assert attempt.applied_at is not None

            v2 = (
                await session.execute(
                    select(WorkflowVersion).where(
                        WorkflowVersion.workflow_name == name, WorkflowVersion.version == 2
                    )
                )
            ).scalar_one()
            assert v2.status == WorkflowVersionStatus.PRODUCTION
            assert v2.promoted_by == "healer"

            v1 = (
                await session.execute(
                    select(WorkflowVersion).where(
                        WorkflowVersion.workflow_name == name, WorkflowVersion.version == 1
                    )
                )
            ).scalar_one()
            assert v1.status == WorkflowVersionStatus.ARCHIVED

            approval = await session.get(ApprovalRequest, attempt.approval_id)
            assert approval.status == ApprovalStatus.APPROVED
            assert approval.reviewer_id == "healer"

        # Disk copy synced for backward compat
        assert (tmp_path / f"{name}.yaml").read_text() == PATCHED_YAML.format(name=name)

    @pytest.mark.asyncio
    async def test_low_confidence_not_auto_applied(self, monkeypatch, tmp_path):
        from sandcastle.models.db import HealAttempt, async_session

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        monkeypatch.setattr(settings, "healer_auto_apply", True)
        name = f"heal-lowconf-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name)

        with patch(
            "sandcastle.engine.healer._call_llm",
            new=AsyncMock(return_value=_llm_response(PATCHED_YAML.format(name=name), 0.4)),
        ):
            result = await heal_item(item_id)

        assert result["status"] == "proposed"

        from sqlalchemy import select

        async with async_session() as session:
            attempt = (
                await session.execute(
                    select(HealAttempt).where(HealAttempt.dead_letter_id == item_id)
                )
            ).scalar_one()
            assert attempt.status == "proposed"
            assert attempt.applied_at is None

    @pytest.mark.asyncio
    async def test_unparseable_patch_rejected(self, monkeypatch, tmp_path):
        from sandcastle.models.db import HealAttempt, WorkflowVersion, async_session

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        name = f"heal-reject-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name)

        with patch(
            "sandcastle.engine.healer._call_llm",
            new=AsyncMock(return_value=_llm_response("steps: [this is not: a workflow")),
        ):
            result = await heal_item(item_id)

        assert result["status"] == "rejected"

        from sqlalchemy import select

        async with async_session() as session:
            attempt = (
                await session.execute(
                    select(HealAttempt).where(HealAttempt.dead_letter_id == item_id)
                )
            ).scalar_one()
            assert attempt.status == "rejected"
            assert attempt.to_version is None
            # No draft version was filed
            max_version = (
                await session.execute(
                    select(WorkflowVersion.version)
                    .where(WorkflowVersion.workflow_name == name)
                    .order_by(WorkflowVersion.version.desc())
                    .limit(1)
                )
            ).scalar_one()
            assert max_version == 1

    @pytest.mark.asyncio
    async def test_max_attempts_respected(self, monkeypatch, tmp_path):
        from sandcastle.models.db import HealAttempt, async_session

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        name = f"heal-max-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name)

        async with async_session() as session:
            for _ in range(settings.healer_max_attempts):
                session.add(
                    HealAttempt(
                        dead_letter_id=item_id,
                        workflow_name=name,
                        step_id="summarize",
                        diagnosis="previous try",
                        status="regressed",
                    )
                )
            await session.commit()

        llm = AsyncMock(return_value=_llm_response(PATCHED_YAML.format(name=name)))
        with patch("sandcastle.engine.healer._call_llm", new=llm):
            result = await heal_item(item_id)

        assert result["status"] == "skipped"
        assert "max heal attempts" in result["reason"]
        llm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_in_flight_attempt_not_duplicated(self, monkeypatch, tmp_path):
        from sandcastle.models.db import HealAttempt, async_session

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        name = f"heal-dup-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name)

        async with async_session() as session:
            session.add(
                HealAttempt(
                    dead_letter_id=item_id,
                    workflow_name=name,
                    step_id="summarize",
                    diagnosis="pending review",
                    status="proposed",
                )
            )
            await session.commit()

        llm = AsyncMock(return_value=_llm_response(PATCHED_YAML.format(name=name)))
        with patch("sandcastle.engine.healer._call_llm", new=llm):
            result = await heal_item(item_id)

        assert result["status"] == "skipped"
        llm.assert_not_awaited()


# --- Resolution loop ---


class TestResolutionLoop:
    @pytest.mark.asyncio
    async def test_success_marks_item_resolved_by_healer(self):
        from sandcastle.models.db import (
            DeadLetterItem,
            HealAttempt,
            Run,
            RunStatus,
            async_session,
        )

        name = f"heal-resolve-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name, with_version=False)
        applied_at = datetime.now(timezone.utc) - timedelta(hours=2)

        async with async_session() as session:
            attempt = HealAttempt(
                dead_letter_id=item_id,
                workflow_name=name,
                step_id="summarize",
                diagnosis="fixed prompt",
                status="auto_applied",
                to_version=2,
                applied_at=applied_at,
            )
            session.add(attempt)
            # A successful run after the patch was applied
            session.add(Run(workflow_name=name, status=RunStatus.COMPLETED))
            await session.commit()
            attempt_id = attempt.id

        summary = await check_heal_resolutions()
        assert summary["resolved"] >= 1

        async with async_session() as session:
            item = await session.get(DeadLetterItem, item_id)
            assert item.resolved_at is not None
            assert item.resolved_by == "healer"
            attempt = await session.get(HealAttempt, attempt_id)
            assert attempt.status == "succeeded"

    @pytest.mark.asyncio
    async def test_failure_marks_attempt_regressed(self):
        from sandcastle.models.db import (
            DeadLetterItem,
            HealAttempt,
            Run,
            RunStatus,
            async_session,
        )

        name = f"heal-regress-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name, with_version=False)
        applied_at = datetime.now(timezone.utc) - timedelta(hours=2)

        async with async_session() as session:
            attempt = HealAttempt(
                dead_letter_id=item_id,
                workflow_name=name,
                step_id="summarize",
                diagnosis="fixed prompt",
                status="applied",
                to_version=2,
                applied_at=applied_at,
            )
            session.add(attempt)
            session.add(Run(workflow_name=name, status=RunStatus.FAILED))
            await session.commit()
            attempt_id = attempt.id

        summary = await check_heal_resolutions()
        assert summary["regressed"] >= 1

        async with async_session() as session:
            item = await session.get(DeadLetterItem, item_id)
            assert item.resolved_at is None  # stays open for another heal
            attempt = await session.get(HealAttempt, attempt_id)
            assert attempt.status == "regressed"


# --- Full pass ---


class TestHealerPass:
    @pytest.mark.asyncio
    async def test_pass_heals_unresolved_items(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        name = f"heal-pass-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name)

        with patch(
            "sandcastle.engine.healer._call_llm",
            new=AsyncMock(return_value=_llm_response(PATCHED_YAML.format(name=name))),
        ):
            summary = await run_healer_pass(lookback_hours=1)

        assert summary["scanned"] >= 1
        assert summary["proposed"] >= 1

    @pytest.mark.asyncio
    async def test_pass_lookback_excludes_old_items(self, monkeypatch, tmp_path):
        from sandcastle.models.db import DeadLetterItem, async_session

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        name = f"heal-old-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name)
        async with async_session() as session:
            item = await session.get(DeadLetterItem, item_id)
            item.created_at = datetime.now(timezone.utc) - timedelta(days=30)
            await session.commit()

        llm = AsyncMock(return_value=_llm_response(PATCHED_YAML.format(name=name)))
        with patch("sandcastle.engine.healer._call_llm", new=llm):
            await run_healer_pass(lookback_hours=24)

        # The 30-day-old item is outside the 24h lookback - no attempt filed for it
        from sqlalchemy import select

        async with async_session() as session:
            from sandcastle.models.db import HealAttempt

            attempts = (
                await session.execute(
                    select(HealAttempt).where(HealAttempt.dead_letter_id == item_id)
                )
            ).scalars().all()
        assert attempts == []


# --- API endpoints ---


class TestHealerApi:
    def _client(self):
        from fastapi.testclient import TestClient

        from sandcastle.main import app

        return TestClient(app)

    def test_trigger_healer_run(self):
        summary = {
            "scanned": 3,
            "proposed": 1,
            "auto_applied": 1,
            "rejected": 0,
            "failed": 0,
            "skipped": 1,
            "published_approved": 0,
            "resolved": 1,
            "regressed": 0,
        }
        with patch(
            "sandcastle.engine.healer.run_healer_pass", new=AsyncMock(return_value=summary)
        ):
            resp = self._client().post("/api/healer/run")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["scanned"] == 3
        assert data["auto_applied"] == 1
        assert data["resolved"] == 1

    @pytest.mark.asyncio
    async def test_healer_activity_lists_attempts(self, tmp_path, monkeypatch):
        from sandcastle.models.db import HealAttempt, async_session

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        name = f"heal-activity-{uuid.uuid4().hex[:8]}"
        _, item_id = await _make_failure(name, with_version=False)
        async with async_session() as session:
            session.add(
                HealAttempt(
                    dead_letter_id=item_id,
                    workflow_name=name,
                    step_id="summarize",
                    diagnosis="prompt was ambiguous",
                    confidence=0.85,
                    from_version=1,
                    to_version=2,
                    status="proposed",
                )
            )
            await session.commit()

        resp = self._client().get(f"/api/healer/activity?workflow_name={name}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 1
        entry = body["data"][0]
        assert entry["workflow_name"] == name
        assert entry["diagnosis"] == "prompt was ambiguous"
        assert entry["status"] == "proposed"
        assert entry["to_version"] == 2
