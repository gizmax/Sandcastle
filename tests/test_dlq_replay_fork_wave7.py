"""Wave 7 deep audit: Dead Letter Queue, Replay, Fork, and Checkpoint features.

Covers the full lifecycle of DLQ items, replay/fork from checkpoints,
checkpoint save/load, and edge cases including tenant isolation,
error handling, and input validation.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helper: workflow YAML for replay/fork tests
# ---------------------------------------------------------------------------

SIMPLE_WORKFLOW = """\
name: wave7-test
description: Test workflow for DLQ/replay/fork
steps:
  - id: step1
    prompt: "Do step 1"
    model: haiku
    max_turns: 1
  - id: step2
    prompt: "Do step 2 using {steps.step1.output}"
    model: haiku
    max_turns: 1
    depends_on:
      - step1
  - id: step3
    prompt: "Do step 3"
    model: haiku
    max_turns: 1
    depends_on:
      - step2
"""

DLQ_WORKFLOW = """\
name: dlq-workflow
description: Workflow with dead letter queue enabled
on_failure:
  dead_letter: true
steps:
  - id: step1
    prompt: "Do something"
    model: haiku
    max_turns: 1
"""


# ---------------------------------------------------------------------------
# 1. Dead Letter Queue - Model and Database
# ---------------------------------------------------------------------------


class TestDeadLetterItemModel:
    """Test DeadLetterItem ORM model constraints and defaults."""

    @pytest.mark.asyncio
    async def test_create_dlq_item_with_required_fields(self):
        """DeadLetterItem should be created with run_id and step_id."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="test-dlq",
                status=RunStatus.FAILED,
                input_data={"key": "value"},
            )
            session.add(run)
            await session.commit()

            dlq_item = DeadLetterItem(
                run_id=run_id,
                step_id="step1",
                error="Something went wrong",
                input_data={"key": "value"},
                attempts=1,
            )
            session.add(dlq_item)
            await session.commit()

            assert dlq_item.id is not None
            assert dlq_item.resolved_at is None
            assert dlq_item.resolved_by is None
            assert dlq_item.parallel_index is None

    @pytest.mark.asyncio
    async def test_dlq_item_defaults(self):
        """DeadLetterItem.attempts should default to 1."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="test-defaults",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)

            assert item.attempts == 1
            assert item.error is None
            assert item.input_data is None

    @pytest.mark.asyncio
    async def test_dlq_cascade_delete(self):
        """Deleting a run should cascade-delete its DLQ items."""
        from sqlalchemy import select

        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="cascade-test",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            for i in range(3):
                item = DeadLetterItem(
                    run_id=run_id,
                    step_id=f"step{i}",
                    error=f"error {i}",
                )
                session.add(item)
            await session.commit()

        # Delete the run
        async with async_session() as session:
            run = await session.get(Run, run_id)
            if run:
                await session.delete(run)
                await session.commit()

        # Verify DLQ items are gone
        async with async_session() as session:
            stmt = select(DeadLetterItem).where(
                DeadLetterItem.run_id == run_id
            )
            result = await session.execute(stmt)
            items = result.scalars().all()
            assert len(items) == 0

    @pytest.mark.asyncio
    async def test_dlq_item_with_parallel_index(self):
        """DLQ items for parallel (fan-out) failures store parallel_index."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="parallel-dlq",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="fan_out_step",
                parallel_index=5,
                error="Item 5 failed",
                input_data={"_item_index": 5},
                attempts=2,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)

            assert item.parallel_index == 5
            assert item.attempts == 2


# ---------------------------------------------------------------------------
# 2. Dead Letter Queue - API Endpoints
# ---------------------------------------------------------------------------


class TestDeadLetterListAPI:
    """Test GET /api/dead-letter endpoint."""

    def test_list_empty_dlq(self):
        """GET /api/dead-letter returns empty list and pagination meta."""
        response = client.get("/api/dead-letter")
        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert body["meta"]["limit"] == 50
        assert body["meta"]["offset"] == 0

    def test_list_dlq_with_pagination(self):
        """Pagination params are respected."""
        response = client.get("/api/dead-letter?limit=3&offset=10")
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["limit"] == 3
        assert body["meta"]["offset"] == 10

    def test_list_dlq_invalid_limit(self):
        """limit=0 should be rejected."""
        response = client.get("/api/dead-letter?limit=0")
        assert response.status_code == 422

    def test_list_dlq_limit_over_max(self):
        """limit=201 should be rejected (max 200)."""
        response = client.get("/api/dead-letter?limit=201")
        assert response.status_code == 422

    def test_list_dlq_negative_offset(self):
        """offset=-1 should be rejected."""
        response = client.get("/api/dead-letter?offset=-1")
        assert response.status_code == 422

    def test_list_dlq_with_resolved_filter(self):
        """resolved=true should include resolved items."""
        response = client.get("/api/dead-letter?resolved=true")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_dlq_returns_items(self):
        """Verify DLQ items appear in listing."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="dlq-list-test",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="list_step",
                error="test error for listing",
                attempts=1,
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        response = client.get("/api/dead-letter")
        assert response.status_code == 200
        body = response.json()
        found = [d for d in body["data"] if d["id"] == item_id]
        assert len(found) == 1
        assert found[0]["step_id"] == "list_step"
        assert found[0]["error"] == "test error for listing"

    @pytest.mark.asyncio
    async def test_list_dlq_excludes_resolved_by_default(self):
        """By default, resolved items are not shown."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="resolved-filter-test",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="resolved_step",
                error="already resolved",
                resolved_at=datetime.now(timezone.utc),
                resolved_by="manual",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        response = client.get("/api/dead-letter")
        body = response.json()
        found = [d for d in body["data"] if d["id"] == item_id]
        assert len(found) == 0, "Resolved items should not appear by default"

        # But with resolved=true they should appear
        response = client.get("/api/dead-letter?resolved=true")
        body = response.json()
        found = [d for d in body["data"] if d["id"] == item_id]
        assert len(found) == 1, "Resolved items should appear with resolved=true"


class TestDeadLetterRetryAPI:
    """Test POST /api/dead-letter/{item_id}/retry endpoint."""

    def test_retry_invalid_id(self):
        """Invalid UUID format should return 400."""
        response = client.post("/api/dead-letter/not-a-uuid/retry")
        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "INVALID_ID"

    def test_retry_nonexistent_item(self):
        """Nonexistent DLQ item should return 404."""
        fake_id = str(uuid.uuid4())
        response = client.post(f"/api/dead-letter/{fake_id}/retry")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_already_resolved(self):
        """Cannot retry an already-resolved DLQ item."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="retry-resolved-test",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="old error",
                resolved_at=datetime.now(timezone.utc),
                resolved_by="manual",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        response = client.post(f"/api/dead-letter/{item_id}/retry")
        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "ALREADY_RESOLVED"

    @pytest.mark.asyncio
    async def test_retry_success(self):
        """Successful DLQ retry marks item resolved, creates new run, and enqueues."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.FAILED,
                input_data={"name": "test"},
                tenant_id="tenant-dlq",
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="step1",
                error="timeout",
                input_data={"name": "test"},
                attempts=1,
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        with (
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            response = client.post(f"/api/dead-letter/{item_id}/retry")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["retried"] is True
        assert "new_run_id" in body["data"]
        mock_enqueue.assert_called_once()

        # Verify DLQ item is resolved
        async with async_session() as session:
            refreshed = await session.get(DeadLetterItem, uuid.UUID(item_id))
            assert refreshed.resolved_at is not None
            assert refreshed.resolved_by == "retry"
            assert refreshed.attempts == 2

        # Verify new run was created with correct parent linkage
        new_run_id = body["data"]["new_run_id"]
        async with async_session() as session:
            new_run = await session.get(Run, uuid.UUID(new_run_id))
            assert new_run is not None
            assert new_run.parent_run_id == run_id
            assert new_run.workflow_name == "wave7-test"
            assert new_run.input_data == {"name": "test"}
            # Retry preserves original tenant
            assert new_run.tenant_id == "tenant-dlq"

    @pytest.mark.asyncio
    async def test_retry_original_run_deleted(self):
        """If original run is deleted, retry should return 400 RUN_NOT_FOUND."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="deleted-run",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="error",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        # Delete the original run (but DLQ item survives because we
        # delete via session.delete which triggers cascade, not manual).
        # Instead, simulate by making session.get return None.
        async with async_session() as session:
            # Manually remove the run (bypass cascade for this test)
            run = await session.get(Run, run_id)
            # We can't delete the run because cascade would remove the DLQ item.
            # Instead, we re-resolve the DLQ item first to mark it, then
            # test the code path where run lookup fails.
            pass

        # Mock the session to return the DLQ item but None for the run
        # This tests the case where the run row is gone
        with patch("sandcastle.api.routes.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_ctx

            # First call: load DLQ item
            mock_dlq_item = MagicMock()
            mock_dlq_item.id = uuid.UUID(item_id)
            mock_dlq_item.run_id = run_id
            mock_dlq_item.resolved_at = None
            mock_dlq_item.attempts = 1

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_dlq_item
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.commit = AsyncMock()

            # session.get for Run returns None
            mock_session.get = AsyncMock(return_value=None)

            response = client.post(f"/api/dead-letter/{item_id}/retry")

        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "RUN_NOT_FOUND"


class TestDLQRetryBugFixes:
    """Tests for DLQ retry bug fixes (Wave 7 findings)."""

    @pytest.mark.asyncio
    async def test_retry_max_retries_exceeded(self):
        """BUG FIX: DLQ retry should reject items that exceeded max retries."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="max-retry-test",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="keeps failing",
                attempts=10,  # At the limit
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        response = client.post(f"/api/dead-letter/{item_id}/retry")
        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "MAX_RETRIES_EXCEEDED"

    @pytest.mark.asyncio
    async def test_retry_item_stays_unresolved_on_enqueue_failure(self):
        """BUG FIX: If enqueue fails, DLQ item should remain unresolved."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.FAILED,
                input_data={"k": "v"},
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="step1",
                error="timeout",
                attempts=1,
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        # Make _load_workflow_yaml succeed but enqueue_workflow fail
        with (
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
                side_effect=Exception("Redis connection refused"),
            ),
        ):
            response = client.post(f"/api/dead-letter/{item_id}/retry")

        assert response.status_code == 500

        # DLQ item should still be unresolved (not prematurely marked)
        async with async_session() as session:
            refreshed = await session.get(DeadLetterItem, uuid.UUID(item_id))
            assert refreshed.resolved_at is None, (
                "DLQ item should remain unresolved when enqueue fails"
            )
            assert refreshed.attempts == 1, (
                "Attempts should not be incremented on failed retry"
            )

    @pytest.mark.asyncio
    async def test_retry_resolves_item_only_after_success(self):
        """BUG FIX: DLQ item is resolved only after successful enqueue."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.FAILED,
                input_data={"k": "v"},
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="step1",
                error="timeout",
                attempts=1,
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        with (
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            response = client.post(f"/api/dead-letter/{item_id}/retry")

        assert response.status_code == 200

        # Now the item should be resolved
        async with async_session() as session:
            refreshed = await session.get(DeadLetterItem, uuid.UUID(item_id))
            assert refreshed.resolved_at is not None
            assert refreshed.resolved_by == "retry"
            assert refreshed.attempts == 2


class TestDeadLetterResolveAPI:
    """Test POST /api/dead-letter/{item_id}/resolve endpoint."""

    def test_resolve_invalid_id(self):
        """Invalid UUID format should return 400."""
        response = client.post(
            "/api/dead-letter/bad-id/resolve",
            json={"reason": "test"},
        )
        assert response.status_code == 400

    def test_resolve_nonexistent(self):
        """Resolving a nonexistent item returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.post(
            f"/api/dead-letter/{fake_id}/resolve",
            json={"reason": "done"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_resolve_success(self):
        """Successfully resolve a DLQ item."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="resolve-test",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="some error",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        response = client.post(
            f"/api/dead-letter/{item_id}/resolve",
            json={"reason": "manually fixed"},
        )
        assert response.status_code == 200
        body = response.json()
        data = body["data"]
        assert data["resolved_by"] == "manual"
        assert data["resolved_at"] is not None

    @pytest.mark.asyncio
    async def test_resolve_already_resolved(self):
        """Cannot resolve an already-resolved item."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="double-resolve",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="err",
                resolved_at=datetime.now(timezone.utc),
                resolved_by="manual",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        response = client.post(
            f"/api/dead-letter/{item_id}/resolve",
            json={"reason": "try again"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "ALREADY_RESOLVED"

    @pytest.mark.asyncio
    async def test_resolve_without_reason(self):
        """Resolve should work even without a reason body."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="no-reason",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="err",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        # No body at all
        response = client.post(f"/api/dead-letter/{item_id}/resolve")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 3. Dead Letter Queue - _send_to_dead_letter function
# ---------------------------------------------------------------------------


class TestSendToDeadLetter:
    """Test the executor's _send_to_dead_letter function."""

    @pytest.mark.asyncio
    async def test_send_to_dlq_success(self):
        """_send_to_dead_letter creates a DLQ item and returns True."""
        from sqlalchemy import select

        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )
        from sandcastle.engine.executor import _send_to_dead_letter

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="send-dlq-test",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            await session.commit()

        result = await _send_to_dead_letter(
            run_id=str(run_id),
            step_id="failing_step",
            error="step timed out",
            input_data={"key": "val"},
            attempts=3,
            parallel_index=None,
        )

        assert result is True

        # Verify the item exists in the DB
        async with async_session() as session:
            stmt = select(DeadLetterItem).where(
                DeadLetterItem.run_id == run_id
            )
            res = await session.execute(stmt)
            item = res.scalar_one()
            assert item.step_id == "failing_step"
            assert item.error == "step timed out"
            assert item.attempts == 3
            assert item.input_data == {"key": "val"}

    @pytest.mark.asyncio
    async def test_send_to_dlq_with_parallel_index(self):
        """_send_to_dead_letter stores parallel_index for fan-out failures."""
        from sqlalchemy import select

        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )
        from sandcastle.engine.executor import _send_to_dead_letter

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="parallel-dlq-test",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            await session.commit()

        result = await _send_to_dead_letter(
            run_id=str(run_id),
            step_id="fan_step",
            error="item 7 failed",
            input_data={"_item_index": 7},
            attempts=1,
            parallel_index=7,
        )

        assert result is True

        async with async_session() as session:
            stmt = select(DeadLetterItem).where(
                DeadLetterItem.run_id == run_id
            )
            res = await session.execute(stmt)
            item = res.scalar_one()
            assert item.parallel_index == 7

    @pytest.mark.asyncio
    async def test_send_to_dlq_db_failure_returns_false(self):
        """_send_to_dead_letter returns False on DB error."""
        from sandcastle.engine.executor import _send_to_dead_letter

        with patch(
            "sandcastle.models.db.async_session",
            side_effect=Exception("DB down"),
        ):
            result = await _send_to_dead_letter(
                run_id=str(uuid.uuid4()),
                step_id="s1",
                error="err",
                input_data=None,
                attempts=1,
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_send_to_dlq_publishes_event(self):
        """_send_to_dead_letter should publish a dlq.new event."""
        from sandcastle.models.db import Run, RunStatus, async_session
        from sandcastle.engine.executor import _send_to_dead_letter

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="event-test",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            await session.commit()

        with patch(
            "sandcastle.engine.executor.event_bus"
        ) as mock_bus:
            await _send_to_dead_letter(
                run_id=str(run_id),
                step_id="event_step",
                error="oops",
                input_data=None,
                attempts=1,
            )
            mock_bus.publish.assert_called_once_with(
                "dlq.new",
                {
                    "run_id": str(run_id),
                    "step_name": "event_step",
                    "error": "oops",
                },
            )


# ---------------------------------------------------------------------------
# 4. Checkpoint - Save and Load
# ---------------------------------------------------------------------------


class TestCheckpointSaveLoad:
    """Test _save_checkpoint and RunCheckpoint model."""

    @pytest.mark.asyncio
    async def test_save_checkpoint_creates_record(self):
        """_save_checkpoint creates a RunCheckpoint record."""
        from sqlalchemy import select

        from sandcastle.models.db import Run, RunCheckpoint, RunStatus, async_session
        from sandcastle.engine.executor import RunContext, _save_checkpoint

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="ckpt-test",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            await session.commit()

        context = RunContext(
            run_id=str(run_id),
            input={"prompt": "hello"},
            step_outputs={"step1": "output1"},
            costs=[0.001],
        )

        await _save_checkpoint(
            run_id=str(run_id),
            step_id="step1",
            stage_index=1,
            context=context,
        )

        async with async_session() as session:
            stmt = select(RunCheckpoint).where(
                RunCheckpoint.run_id == run_id
            )
            result = await session.execute(stmt)
            cp = result.scalar_one()
            assert cp.step_id == "step1"
            assert cp.stage_index == 1
            snapshot = cp.context_snapshot
            assert snapshot["input"] == {"prompt": "hello"}
            assert snapshot["step_outputs"] == {"step1": "output1"}
            assert snapshot["costs"] == [0.001]
            assert snapshot["run_id"] == str(run_id)

    @pytest.mark.asyncio
    async def test_multiple_checkpoints_per_run(self):
        """Multiple checkpoints can be saved for a single run."""
        from sqlalchemy import select

        from sandcastle.models.db import Run, RunCheckpoint, RunStatus, async_session
        from sandcastle.engine.executor import RunContext, _save_checkpoint

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="multi-ckpt",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            await session.commit()

        for i, step_id in enumerate(["step1", "step2", "step3"]):
            ctx = RunContext(
                run_id=str(run_id),
                input={"v": i},
                step_outputs={f"step{j+1}": f"out{j+1}" for j in range(i + 1)},
            )
            await _save_checkpoint(str(run_id), step_id, i + 1, ctx)

        async with async_session() as session:
            stmt = (
                select(RunCheckpoint)
                .where(RunCheckpoint.run_id == run_id)
                .order_by(RunCheckpoint.stage_index)
            )
            result = await session.execute(stmt)
            checkpoints = result.scalars().all()
            assert len(checkpoints) == 3
            assert checkpoints[0].step_id == "step1"
            assert checkpoints[2].step_id == "step3"
            assert len(checkpoints[2].context_snapshot["step_outputs"]) == 3

    @pytest.mark.asyncio
    async def test_checkpoint_snapshot_structure(self):
        """RunContext.snapshot() returns correct structure."""
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(
            run_id="test-123",
            input={"x": 1},
            step_outputs={"a": "b"},
            costs=[0.01, 0.02],
        )
        snap = ctx.snapshot()
        assert snap == {
            "run_id": "test-123",
            "input": {"x": 1},
            "step_outputs": {"a": "b"},
            "costs": [0.01, 0.02],
            "total_cost": pytest.approx(0.03),
        }

    @pytest.mark.asyncio
    async def test_checkpoint_db_failure_does_not_raise(self):
        """_save_checkpoint should log warning on DB failure, not crash."""
        from sandcastle.engine.executor import RunContext, _save_checkpoint

        ctx = RunContext(run_id=str(uuid.uuid4()), input={})

        with patch(
            "sandcastle.models.db.async_session",
            side_effect=Exception("DB exploded"),
        ):
            # Should not raise
            await _save_checkpoint(
                run_id=ctx.run_id,
                step_id="s1",
                stage_index=0,
                context=ctx,
            )

    @pytest.mark.asyncio
    async def test_checkpoint_cascade_on_run_delete(self):
        """Deleting a run should cascade-delete its checkpoints."""
        from sqlalchemy import select

        from sandcastle.models.db import Run, RunCheckpoint, RunStatus, async_session
        from sandcastle.engine.executor import RunContext, _save_checkpoint

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="cascade-ckpt",
                status=RunStatus.COMPLETED,
            )
            session.add(run)
            await session.commit()

        ctx = RunContext(run_id=str(run_id), input={"a": 1})
        await _save_checkpoint(str(run_id), "s1", 1, ctx)

        async with async_session() as session:
            run = await session.get(Run, run_id)
            await session.delete(run)
            await session.commit()

        async with async_session() as session:
            stmt = select(RunCheckpoint).where(
                RunCheckpoint.run_id == run_id
            )
            result = await session.execute(stmt)
            assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_checkpoint_with_large_output(self):
        """Checkpoint should handle reasonably large step outputs."""
        from sandcastle.models.db import Run, RunCheckpoint, RunStatus, async_session
        from sandcastle.engine.executor import RunContext, _save_checkpoint
        from sqlalchemy import select

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="large-ckpt",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            await session.commit()

        # Large but not unreasonable output (~100KB)
        large_output = "x" * 100_000
        ctx = RunContext(
            run_id=str(run_id),
            input={},
            step_outputs={"step1": large_output},
        )
        await _save_checkpoint(str(run_id), "step1", 1, ctx)

        async with async_session() as session:
            stmt = select(RunCheckpoint).where(
                RunCheckpoint.run_id == run_id
            )
            result = await session.execute(stmt)
            cp = result.scalar_one()
            assert len(cp.context_snapshot["step_outputs"]["step1"]) == 100_000


# ---------------------------------------------------------------------------
# 5. Replay API
# ---------------------------------------------------------------------------


class TestReplayAPI:
    """Test POST /api/runs/{run_id}/replay endpoint."""

    def test_replay_invalid_run_id(self):
        """Invalid run ID format should return 400."""
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            response = client.post(
                "/api/runs/not-a-uuid/replay",
                json={"from_step": "step1"},
            )
        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "INVALID_ID"

    def test_replay_nonexistent_run(self):
        """Replaying a nonexistent run returns 404."""
        fake_id = str(uuid.uuid4())
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{fake_id}/replay",
                json={"from_step": "step1"},
            )
        assert response.status_code == 404

    def test_replay_empty_from_step(self):
        """from_step must not be empty (min_length=1)."""
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{uuid.uuid4()}/replay",
                json={"from_step": ""},
            )
        assert response.status_code == 422

    def test_replay_missing_from_step(self):
        """from_step is required."""
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{uuid.uuid4()}/replay",
                json={},
            )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_replay_workflow_not_found(self):
        """If the workflow YAML no longer exists, replay should 400."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="deleted-workflow",
                status=RunStatus.COMPLETED,
                input_data={"x": 1},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                side_effect=FileNotFoundError("not found"),
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "WORKFLOW_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_replay_invalid_step_id(self):
        """If from_step doesn't exist in the workflow, replay should 400."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={"x": 1},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "nonexistent_step"},
            )

        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "INVALID_STEP"

    @pytest.mark.asyncio
    async def test_replay_from_first_step_no_checkpoint(self):
        """Replay from first step should work even without checkpoints."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={"name": "world"},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["replay_from_step"] == "step1"
        assert body["data"]["parent_run_id"] == str(run_id)
        assert body["data"]["status"] == "queued"

        # Verify enqueue was called with no initial_context and no skip_steps
        call_kwargs = mock_enqueue.call_args
        assert call_kwargs.kwargs.get("initial_context") is None
        assert call_kwargs.kwargs.get("skip_steps") == []

    @pytest.mark.asyncio
    async def test_replay_from_middle_step_with_checkpoint(self):
        """Replay from step2 should restore checkpoint and skip step1."""
        from sandcastle.models.db import (
            Run,
            RunCheckpoint,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        checkpoint_snapshot = {
            "run_id": str(run_id),
            "input": {"name": "world"},
            "step_outputs": {"step1": "hello world"},
            "costs": [0.001],
            "total_cost": 0.001,
        }

        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={"name": "world"},
            )
            session.add(run)
            await session.commit()

            cp = RunCheckpoint(
                run_id=run_id,
                step_id="step1",
                stage_index=1,
                context_snapshot=checkpoint_snapshot,
            )
            session.add(cp)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step2"},
            )

        assert response.status_code == 200

        # Verify enqueue was called with initial_context containing step1 output
        call_kwargs = mock_enqueue.call_args
        ctx = call_kwargs.kwargs.get("initial_context")
        assert ctx is not None
        assert ctx["step_outputs"]["step1"] == "hello world"
        # step1 should be in skip_steps, step2 should not
        skip = set(call_kwargs.kwargs.get("skip_steps", []))
        assert "step1" in skip
        assert "step2" not in skip

    @pytest.mark.asyncio
    async def test_replay_creates_new_run_linked_to_original(self):
        """Replay should create a new run with parent_run_id set."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={"n": 1},
                tenant_id="tenant-replay",
                max_cost_usd=5.0,
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        assert response.status_code == 200
        new_run_id = response.json()["data"]["new_run_id"]

        async with async_session() as session:
            new_run = await session.get(Run, uuid.UUID(new_run_id))
            assert new_run.parent_run_id == run_id
            assert new_run.replay_from_step == "step1"
            assert new_run.workflow_name == "wave7-test"
            assert new_run.input_data == {"n": 1}
            assert new_run.max_cost_usd == 5.0
            # Note: replay uses the REQUESTER's tenant_id, not the original
            # run's tenant_id. Without auth, get_tenant_id() returns None.
            # This is correct - the new run belongs to whoever initiated replay.

    @pytest.mark.asyncio
    async def test_replay_enqueue_failure_marks_run_failed(self):
        """If enqueue fails, the new run should be marked as failed."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
                side_effect=Exception("Redis down"),
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        assert response.status_code == 500
        body = response.json()
        assert body["detail"]["error"]["code"] == "QUEUE_ERROR"


# ---------------------------------------------------------------------------
# 6. Fork API
# ---------------------------------------------------------------------------


class TestForkAPI:
    """Test POST /api/runs/{run_id}/fork endpoint."""

    def test_fork_invalid_run_id(self):
        """Invalid run ID format should return 400."""
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            response = client.post(
                "/api/runs/bad-uuid/fork",
                json={"from_step": "step1", "changes": {}},
            )
        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "INVALID_ID"

    def test_fork_nonexistent_run(self):
        """Forking a nonexistent run returns 404."""
        fake_id = str(uuid.uuid4())
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{fake_id}/fork",
                json={"from_step": "step1", "changes": {}},
            )
        assert response.status_code == 404

    def test_fork_missing_from_step(self):
        """from_step is required."""
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{uuid.uuid4()}/fork",
                json={"changes": {}},
            )
        assert response.status_code == 422

    def test_fork_empty_from_step(self):
        """from_step must not be empty."""
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{uuid.uuid4()}/fork",
                json={"from_step": "", "changes": {}},
            )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_fork_workflow_not_found(self):
        """If the workflow YAML no longer exists, fork should 400."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="gone-workflow",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                side_effect=FileNotFoundError("not found"),
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/fork",
                json={"from_step": "step1", "changes": {}},
            )

        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "WORKFLOW_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_fork_invalid_step_id(self):
        """If from_step doesn't exist in the workflow, fork should 400."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/fork",
                json={"from_step": "no_such_step", "changes": {}},
            )

        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"]["code"] == "INVALID_STEP"

    @pytest.mark.asyncio
    async def test_fork_with_changes(self):
        """Fork with model/prompt overrides should pass step_overrides to enqueue."""
        from sandcastle.models.db import (
            Run,
            RunCheckpoint,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        checkpoint_snapshot = {
            "run_id": str(run_id),
            "input": {},
            "step_outputs": {"step1": "result1"},
            "costs": [0.001],
            "total_cost": 0.001,
        }

        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

            cp = RunCheckpoint(
                run_id=run_id,
                step_id="step1",
                stage_index=1,
                context_snapshot=checkpoint_snapshot,
            )
            session.add(cp)
            await session.commit()

        changes = {"model": "opus", "prompt": "custom prompt"}

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/fork",
                json={"from_step": "step2", "changes": changes},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["fork_from_step"] == "step2"
        assert body["data"]["changes"] == changes

        # Verify step_overrides passed to enqueue
        call_kwargs = mock_enqueue.call_args
        overrides = call_kwargs.kwargs.get("step_overrides")
        assert overrides == {"step2": changes}

    @pytest.mark.asyncio
    async def test_fork_stores_fork_changes_on_run(self):
        """Forked run should have fork_changes stored in the DB."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        changes = {"model": "sonnet", "max_turns": 5}

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/fork",
                json={"from_step": "step1", "changes": changes},
            )

        assert response.status_code == 200
        new_run_id = response.json()["data"]["new_run_id"]

        async with async_session() as session:
            new_run = await session.get(Run, uuid.UUID(new_run_id))
            assert new_run.fork_changes == changes
            assert new_run.replay_from_step == "step1"
            assert new_run.parent_run_id == run_id

    @pytest.mark.asyncio
    async def test_fork_empty_changes(self):
        """Fork with empty changes should still work (no overrides)."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/fork",
                json={"from_step": "step1", "changes": {}},
            )

        assert response.status_code == 200
        # Empty changes -> None step_overrides
        call_kwargs = mock_enqueue.call_args
        assert call_kwargs.kwargs.get("step_overrides") is None

    @pytest.mark.asyncio
    async def test_fork_enqueue_failure_marks_run_failed(self):
        """If enqueue fails, the forked run should be marked as failed."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
                side_effect=Exception("Queue error"),
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/fork",
                json={"from_step": "step1", "changes": {"model": "opus"}},
            )

        assert response.status_code == 500
        body = response.json()
        assert body["detail"]["error"]["code"] == "QUEUE_ERROR"


# ---------------------------------------------------------------------------
# 7. Fork vs Replay differences
# ---------------------------------------------------------------------------


class TestForkVsReplay:
    """Verify structural differences between fork and replay."""

    @pytest.mark.asyncio
    async def test_replay_has_no_fork_changes(self):
        """Replay run should not have fork_changes set."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        assert response.status_code == 200
        new_run_id = response.json()["data"]["new_run_id"]

        async with async_session() as session:
            new_run = await session.get(Run, uuid.UUID(new_run_id))
            assert new_run.fork_changes is None

    @pytest.mark.asyncio
    async def test_replay_does_not_send_step_overrides(self):
        """Replay should not pass step_overrides to enqueue."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_limiter.check = AsyncMock()
            client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        # Replay should not pass step_overrides
        call_kwargs = mock_enqueue.call_args
        # replay_run does NOT pass step_overrides at all (not in the call)
        assert "step_overrides" not in call_kwargs.kwargs


# ---------------------------------------------------------------------------
# 8. Step overrides in executor (fork behavior)
# ---------------------------------------------------------------------------


class TestStepOverridesInExecutor:
    """Test that execute_step_with_retry applies fork overrides correctly."""

    def test_override_allowed_fields(self):
        """Only prompt, model, max_turns, timeout are applied from overrides."""
        import dataclasses
        from sandcastle.engine.dag import StepDefinition

        step = StepDefinition(
            id="test_step",
            prompt="original prompt",
            model="haiku",
            max_turns=3,
            timeout=60,
        )

        overrides = {
            "prompt": "new prompt",
            "model": "opus",
            "max_turns": 10,
            "timeout": 120,
        }

        override_fields = {}
        for key in ("prompt", "model", "max_turns", "timeout"):
            if key in overrides:
                override_fields[key] = overrides[key]

        new_step = dataclasses.replace(step, **override_fields)
        assert new_step.prompt == "new prompt"
        assert new_step.model == "opus"
        assert new_step.max_turns == 10
        assert new_step.timeout == 120
        # Original is unchanged
        assert step.prompt == "original prompt"

    def test_unknown_override_keys_ignored(self):
        """Unknown override keys should not affect the step."""
        import dataclasses
        from sandcastle.engine.dag import StepDefinition

        step = StepDefinition(
            id="test_step",
            prompt="original",
            model="haiku",
        )

        overrides = {
            "nonexistent_field": "value",
            "model": "opus",
        }

        override_fields = {}
        for key in ("prompt", "model", "max_turns", "timeout"):
            if key in overrides:
                override_fields[key] = overrides[key]

        new_step = dataclasses.replace(step, **override_fields)
        assert new_step.model == "opus"
        assert new_step.prompt == "original"  # Unchanged


# ---------------------------------------------------------------------------
# 9. Delete Run cleans up DLQ and checkpoints
# ---------------------------------------------------------------------------


class TestDeleteRunCleansRelated:
    """Test that DELETE /api/runs/{run_id} removes DLQ items and checkpoints."""

    @pytest.mark.asyncio
    async def test_delete_run_removes_dlq_and_checkpoints(self):
        """Deleting a completed run should remove DLQ items and checkpoints."""
        from sqlalchemy import select

        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunCheckpoint,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="cleanup-test",
                status=RunStatus.FAILED,
                input_data={},
            )
            session.add(run)
            await session.commit()

            # Add DLQ item
            dlq = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="err",
            )
            session.add(dlq)

            # Add checkpoint
            cp = RunCheckpoint(
                run_id=run_id,
                step_id="s1",
                stage_index=1,
                context_snapshot={"step_outputs": {}},
            )
            session.add(cp)
            await session.commit()

        response = client.delete(f"/api/runs/{run_id}")
        assert response.status_code == 200

        async with async_session() as session:
            dlq_items = (
                await session.execute(
                    select(DeadLetterItem).where(
                        DeadLetterItem.run_id == run_id
                    )
                )
            ).scalars().all()
            assert len(dlq_items) == 0

            ckpts = (
                await session.execute(
                    select(RunCheckpoint).where(
                        RunCheckpoint.run_id == run_id
                    )
                )
            ).scalars().all()
            assert len(ckpts) == 0


# ---------------------------------------------------------------------------
# 10. Tenant isolation
# ---------------------------------------------------------------------------


class TestDLQTenantIsolation:
    """Verify DLQ endpoints enforce tenant isolation when auth is enabled."""

    @pytest.mark.asyncio
    async def test_dlq_list_tenant_filtered(self):
        """With auth enabled, DLQ list should filter by tenant."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        # Create runs for two tenants
        run_a = uuid.uuid4()
        run_b = uuid.uuid4()
        async with async_session() as session:
            session.add(Run(
                id=run_a,
                workflow_name="tenant-a-wf",
                status=RunStatus.FAILED,
                tenant_id="tenant-a",
            ))
            session.add(Run(
                id=run_b,
                workflow_name="tenant-b-wf",
                status=RunStatus.FAILED,
                tenant_id="tenant-b",
            ))
            await session.commit()

            session.add(DeadLetterItem(
                run_id=run_a,
                step_id="s1",
                error="err-a",
            ))
            session.add(DeadLetterItem(
                run_id=run_b,
                step_id="s1",
                error="err-b",
            ))
            await session.commit()

        # Mock auth to return tenant-a
        with (
            patch("sandcastle.api.routes.settings") as mock_settings,
            patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-a"),
        ):
            mock_settings.auth_required = True
            mock_settings.workflows_dir = "workflows"
            response = client.get("/api/dead-letter")

        assert response.status_code == 200
        body = response.json()
        # Should only see tenant-a items
        for item in body["data"]:
            assert item["run_id"] == str(run_a)

    @pytest.mark.asyncio
    async def test_dlq_retry_tenant_isolation(self):
        """DLQ retry should not allow cross-tenant access."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            session.add(Run(
                id=run_id,
                workflow_name="other-tenant-wf",
                status=RunStatus.FAILED,
                tenant_id="tenant-other",
            ))
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="err",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        # Mock auth as tenant-me (different from tenant-other)
        with (
            patch("sandcastle.api.routes.settings") as mock_settings,
            patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-me"),
        ):
            mock_settings.auth_required = True
            mock_settings.workflows_dir = "workflows"
            response = client.post(f"/api/dead-letter/{item_id}/retry")

        # Should return 404 because the DLQ item's run belongs to a different tenant
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_dlq_resolve_tenant_isolation(self):
        """DLQ resolve should not allow cross-tenant access."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            session.add(Run(
                id=run_id,
                workflow_name="other-tenant-wf2",
                status=RunStatus.FAILED,
                tenant_id="tenant-x",
            ))
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="err",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        with (
            patch("sandcastle.api.routes.settings") as mock_settings,
            patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-y"),
        ):
            mock_settings.auth_required = True
            mock_settings.workflows_dir = "workflows"
            response = client.post(
                f"/api/dead-letter/{item_id}/resolve",
                json={"reason": "resolved"},
            )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 11. Replay/Fork tenant isolation
# ---------------------------------------------------------------------------


class TestReplayForkTenantIsolation:
    """Verify replay/fork enforce tenant isolation."""

    @pytest.mark.asyncio
    async def test_replay_other_tenant_run_404(self):
        """Replaying another tenant's run should return 404."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="other-tenant",
                status=RunStatus.COMPLETED,
                input_data={},
                tenant_id="tenant-owner",
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch("sandcastle.api.routes.settings") as mock_settings,
            patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-attacker"),
        ):
            mock_limiter.check = AsyncMock()
            mock_settings.auth_required = True
            mock_settings.workflows_dir = "workflows"
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_fork_other_tenant_run_404(self):
        """Forking another tenant's run should return 404."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="other-tenant",
                status=RunStatus.COMPLETED,
                input_data={},
                tenant_id="tenant-owner",
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch("sandcastle.api.routes.settings") as mock_settings,
            patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-attacker"),
        ):
            mock_limiter.check = AsyncMock()
            mock_settings.auth_required = True
            mock_settings.workflows_dir = "workflows"
            response = client.post(
                f"/api/runs/{run_id}/fork",
                json={"from_step": "step1", "changes": {}},
            )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 12. Checkpoint selection logic for replay/fork
# ---------------------------------------------------------------------------


class TestCheckpointSelection:
    """Test the checkpoint selection logic in replay/fork."""

    @pytest.mark.asyncio
    async def test_checkpoint_selected_before_target_step(self):
        """Should pick checkpoint where from_step is NOT in step_outputs."""
        from sandcastle.models.db import (
            Run,
            RunCheckpoint,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
            )
            session.add(run)
            await session.commit()

            # Checkpoint after step1 (step2 NOT in outputs)
            cp1 = RunCheckpoint(
                run_id=run_id,
                step_id="step1",
                stage_index=1,
                context_snapshot={
                    "step_outputs": {"step1": "out1"},
                    "costs": [0.001],
                },
            )
            # Checkpoint after step2 (step2 IS in outputs)
            cp2 = RunCheckpoint(
                run_id=run_id,
                step_id="step2",
                stage_index=2,
                context_snapshot={
                    "step_outputs": {"step1": "out1", "step2": "out2"},
                    "costs": [0.001, 0.002],
                },
            )
            session.add(cp1)
            session.add(cp2)
            await session.commit()

        # Replay from step2 should use cp1 (where step2 is NOT in outputs)
        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step2"},
            )

        assert response.status_code == 200
        call_kwargs = mock_enqueue.call_args
        ctx = call_kwargs.kwargs.get("initial_context")
        # Should have step1 output but NOT step2
        assert "step1" in ctx["step_outputs"]
        assert "step2" not in ctx["step_outputs"]

    @pytest.mark.asyncio
    async def test_no_checkpoint_replays_from_beginning(self):
        """If no checkpoint exists, replay starts from scratch."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={"v": 1},
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        assert response.status_code == 200
        call_kwargs = mock_enqueue.call_args
        assert call_kwargs.kwargs.get("initial_context") is None
        assert call_kwargs.kwargs.get("skip_steps") == []


# ---------------------------------------------------------------------------
# 13. DAG on_failure.dead_letter parsing
# ---------------------------------------------------------------------------


class TestDagDeadLetterParsing:
    """Test that on_failure.dead_letter is correctly parsed from YAML."""

    def test_dead_letter_enabled(self):
        """dead_letter: true should be parsed correctly."""
        from sandcastle.engine.dag import parse_yaml_string

        wf = parse_yaml_string(DLQ_WORKFLOW)
        assert wf.on_failure is not None
        assert wf.on_failure.dead_letter is True

    def test_dead_letter_disabled_by_default(self):
        """Without on_failure, dead_letter should be absent."""
        from sandcastle.engine.dag import parse_yaml_string

        wf = parse_yaml_string(SIMPLE_WORKFLOW)
        assert wf.on_failure is None

    def test_dead_letter_explicit_false(self):
        """dead_letter: false should parse correctly."""
        from sandcastle.engine.dag import parse_yaml_string

        yaml_str = """\
name: no-dlq
description: test
on_failure:
  dead_letter: false
steps:
  - id: s1
    prompt: "test"
    model: haiku
"""
        wf = parse_yaml_string(yaml_str)
        assert wf.on_failure is not None
        assert wf.on_failure.dead_letter is False


# ---------------------------------------------------------------------------
# 14. Schema validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Test request schema validation for DLQ, replay, and fork."""

    def test_replay_request_from_step_max_length(self):
        """from_step should have max_length=100."""
        from sandcastle.api.schemas import ReplayRequest

        # Should work with 100 chars
        req = ReplayRequest(from_step="x" * 100)
        assert len(req.from_step) == 100

        # Should fail with 101 chars
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ReplayRequest(from_step="x" * 101)

    def test_fork_request_from_step_max_length(self):
        """from_step should have max_length=100."""
        from sandcastle.api.schemas import ForkRequest

        req = ForkRequest(from_step="x" * 100, changes={})
        assert len(req.from_step) == 100

        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ForkRequest(from_step="x" * 101, changes={})

    def test_fork_request_changes_default(self):
        """ForkRequest.changes should default to empty dict."""
        from sandcastle.api.schemas import ForkRequest

        req = ForkRequest(from_step="step1")
        assert req.changes == {}

    def test_dead_letter_resolve_reason_max_length(self):
        """DeadLetterResolveRequest.reason should have max_length=5000."""
        from sandcastle.api.schemas import DeadLetterResolveRequest

        req = DeadLetterResolveRequest(reason="x" * 5000)
        assert len(req.reason) == 5000

        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DeadLetterResolveRequest(reason="x" * 5001)

    def test_dead_letter_resolve_reason_optional(self):
        """DeadLetterResolveRequest.reason should be optional."""
        from sandcastle.api.schemas import DeadLetterResolveRequest

        req = DeadLetterResolveRequest()
        assert req.reason is None

    def test_dead_letter_response_schema(self):
        """DeadLetterItemResponse should have all expected fields."""
        from sandcastle.api.schemas import DeadLetterItemResponse

        resp = DeadLetterItemResponse(
            id="abc",
            run_id="def",
            step_id="step1",
            parallel_index=None,
            error="test",
            input_data={"x": 1},
            attempts=3,
        )
        assert resp.id == "abc"
        assert resp.attempts == 3
        assert resp.resolved_at is None


# ---------------------------------------------------------------------------
# 15. Context restoration in executor
# ---------------------------------------------------------------------------


class TestContextRestoration:
    """Test that initial_context is properly restored in execute_workflow."""

    def test_context_restore_step_outputs(self):
        """Restoring context should set step_outputs from checkpoint."""
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(
            run_id="test",
            input={"x": 1},
        )

        initial_context = {
            "step_outputs": {"step1": "out1", "step2": "out2"},
            "costs": [0.001, 0.002],
        }

        # Simulate what execute_workflow does
        ctx.step_outputs = initial_context.get("step_outputs", {})
        ctx.costs = initial_context.get("costs", [])

        assert ctx.step_outputs == {"step1": "out1", "step2": "out2"}
        assert ctx.costs == [0.001, 0.002]
        assert ctx.total_cost == pytest.approx(0.003)

    def test_context_with_item_is_independent(self):
        """RunContext.with_item() should create an independent child context."""
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(
            run_id="test",
            input={"x": 1},
            step_outputs={"s1": "out1"},
            costs=[0.01],
        )

        child = ctx.with_item("item_val", 0)
        child.step_outputs["s2"] = "out2"
        child.costs.append(0.02)

        # Original should be unchanged (deep copy)
        assert "s2" not in ctx.step_outputs
        assert len(ctx.costs) == 1
        # Child gets item in its input
        assert child.input["_item"] == "item_val"
        assert child.input["_index"] == 0

    def test_skip_steps_excludes_from_step(self):
        """skip_steps should contain prior steps but NOT the from_step itself."""
        initial_context = {
            "step_outputs": {"step1": "out1", "step2": "out2"},
        }
        from_step = "step2"

        skip_steps = set(initial_context["step_outputs"].keys())
        skip_steps.discard(from_step)

        assert "step1" in skip_steps
        assert "step2" not in skip_steps


# ---------------------------------------------------------------------------
# 16. Edge cases and error handling
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases for DLQ, replay, fork."""

    def test_dlq_retry_with_very_long_error(self):
        """DLQ items with very long error strings should be handled."""
        from sandcastle.api.schemas import DeadLetterItemResponse

        long_error = "E" * 10_000
        resp = DeadLetterItemResponse(
            id="abc",
            run_id="def",
            step_id="s1",
            error=long_error,
            attempts=1,
        )
        assert len(resp.error) == 10_000

    @pytest.mark.asyncio
    async def test_dlq_item_with_null_input_data(self):
        """DLQ item with null input_data should be serializable."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="null-input",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="s1",
                error="err",
                input_data=None,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            assert item.input_data is None

    @pytest.mark.asyncio
    async def test_multiple_dlq_items_same_step(self):
        """Multiple DLQ items can exist for the same step (e.g., retries)."""
        from sqlalchemy import select

        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="multi-dlq",
                status=RunStatus.FAILED,
            )
            session.add(run)
            await session.commit()

            for i in range(5):
                item = DeadLetterItem(
                    run_id=run_id,
                    step_id="same_step",
                    error=f"attempt {i + 1}",
                    attempts=i + 1,
                )
                session.add(item)
            await session.commit()

        async with async_session() as session:
            stmt = select(DeadLetterItem).where(
                DeadLetterItem.run_id == run_id,
                DeadLetterItem.step_id == "same_step",
            )
            result = await session.execute(stmt)
            items = result.scalars().all()
            assert len(items) == 5

    @pytest.mark.asyncio
    async def test_replay_preserves_callback_url(self):
        """Replay should preserve the original run's callback_url."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
                callback_url="https://example.com/webhook",
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/replay",
                json={"from_step": "step1"},
            )

        new_run_id = response.json()["data"]["new_run_id"]
        async with async_session() as session:
            new_run = await session.get(Run, uuid.UUID(new_run_id))
            assert new_run.callback_url == "https://example.com/webhook"

    @pytest.mark.asyncio
    async def test_fork_preserves_callback_url(self):
        """Fork should preserve the original run's callback_url."""
        from sandcastle.models.db import Run, RunStatus, async_session

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.COMPLETED,
                input_data={},
                callback_url="https://example.com/hook",
            )
            session.add(run)
            await session.commit()

        with (
            patch("sandcastle.api.routes.execution_limiter") as mock_limiter,
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            mock_limiter.check = AsyncMock()
            response = client.post(
                f"/api/runs/{run_id}/fork",
                json={"from_step": "step1", "changes": {}},
            )

        new_run_id = response.json()["data"]["new_run_id"]
        async with async_session() as session:
            new_run = await session.get(Run, uuid.UUID(new_run_id))
            assert new_run.callback_url == "https://example.com/hook"


# ---------------------------------------------------------------------------
# 17. DLQ retry creates proper run with parent linkage
# ---------------------------------------------------------------------------


class TestDLQRetryRunCreation:
    """Verify the run created by DLQ retry has correct metadata."""

    @pytest.mark.asyncio
    async def test_retry_new_run_has_parent_id(self):
        """DLQ retry should set parent_run_id on the new run."""
        from sandcastle.models.db import (
            DeadLetterItem,
            Run,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="wave7-test",
                status=RunStatus.FAILED,
                input_data={"k": "v"},
                callback_url="https://hook.example.com",
                tenant_id="t1",
            )
            session.add(run)
            await session.commit()

            item = DeadLetterItem(
                run_id=run_id,
                step_id="step1",
                error="timeout",
            )
            session.add(item)
            await session.commit()
            item_id = str(item.id)

        with (
            patch(
                "sandcastle.api.routes._load_workflow_yaml",
                return_value=SIMPLE_WORKFLOW,
            ),
            patch(
                "sandcastle.api.routes.enqueue_workflow",
                new_callable=AsyncMock,
            ),
        ):
            response = client.post(f"/api/dead-letter/{item_id}/retry")

        assert response.status_code == 200
        new_run_id = response.json()["data"]["new_run_id"]

        async with async_session() as session:
            new_run = await session.get(Run, uuid.UUID(new_run_id))
            assert new_run.parent_run_id == run_id
            assert new_run.workflow_name == "wave7-test"
            assert new_run.input_data == {"k": "v"}
            assert new_run.callback_url == "https://hook.example.com"
            assert new_run.tenant_id == "t1"
            assert new_run.status == RunStatus.QUEUED


# ---------------------------------------------------------------------------
# 18. Checkpoint index ordering
# ---------------------------------------------------------------------------


class TestCheckpointIndexOrdering:
    """Test that checkpoints are properly ordered by stage_index."""

    @pytest.mark.asyncio
    async def test_checkpoints_ordered_desc_by_stage_index(self):
        """Replay/fork query checkpoints DESC by stage_index."""
        from sqlalchemy import select

        from sandcastle.models.db import (
            Run,
            RunCheckpoint,
            RunStatus,
            async_session,
        )

        run_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=run_id,
                workflow_name="order-test",
                status=RunStatus.COMPLETED,
            )
            session.add(run)
            await session.commit()

            for i, step_id in enumerate(["s1", "s2", "s3"]):
                cp = RunCheckpoint(
                    run_id=run_id,
                    step_id=step_id,
                    stage_index=i + 1,
                    context_snapshot={
                        "step_outputs": {
                            f"s{j+1}": f"out{j+1}" for j in range(i + 1)
                        }
                    },
                )
                session.add(cp)
            await session.commit()

        # Query with DESC ordering (same as replay/fork code)
        async with async_session() as session:
            stmt = (
                select(RunCheckpoint)
                .where(RunCheckpoint.run_id == run_id)
                .order_by(RunCheckpoint.stage_index.desc())
            )
            result = await session.execute(stmt)
            checkpoints = list(result.scalars().all())

        assert len(checkpoints) == 3
        assert checkpoints[0].stage_index == 3  # Highest first
        assert checkpoints[1].stage_index == 2
        assert checkpoints[2].stage_index == 1

        # For replay from s2: find newest checkpoint where s2 NOT in outputs
        # s3 has s2 in outputs -> skip; s2 has s2 in outputs -> skip;
        # s1 does NOT have s2 -> use it
        target = None
        for cp in checkpoints:
            if "s2" not in cp.context_snapshot.get("step_outputs", {}):
                target = cp
                break

        assert target is not None
        assert target.step_id == "s1"
        assert target.stage_index == 1
