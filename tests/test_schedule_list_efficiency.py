"""Regression tests for batched schedule-list enrichment."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from sandcastle.api.routes import list_schedules
from sandcastle.models.db import Run, RunStatus, Schedule


@pytest.mark.asyncio
async def test_list_schedules_batches_last_runs_and_success_rates():
    now = datetime.now(timezone.utc)
    failed_run = Run(
        id=uuid.uuid4(),
        workflow_name="schedule-batch-alpha",
        status=RunStatus.FAILED,
        created_at=now,
    )
    completed_run = Run(
        id=uuid.uuid4(),
        workflow_name="schedule-batch-beta",
        status=RunStatus.COMPLETED,
        created_at=now,
    )
    schedules = [
        Schedule(
            id=uuid.uuid4(),
            workflow_name=failed_run.workflow_name,
            cron_expression="0 * * * *",
            enabled=True,
            last_run_id=failed_run.id,
            created_at=now,
        ),
        Schedule(
            id=uuid.uuid4(),
            workflow_name=completed_run.workflow_name,
            cron_expression="0 * * * *",
            enabled=True,
            last_run_id=completed_run.id,
            created_at=now,
        ),
    ]

    schedule_result = MagicMock()
    schedule_result.scalars.return_value.all.return_value = schedules
    last_runs_result = MagicMock()
    last_runs_result.scalars.return_value.all.return_value = [
        failed_run,
        completed_run,
    ]
    recent_result = MagicMock()
    recent_result.all.return_value = [
        (failed_run.workflow_name, 2, 1),
        (completed_run.workflow_name, 4, 4),
    ]

    session = AsyncMock()
    session.scalar.return_value = 2
    session.execute.side_effect = [
        schedule_result,
        last_runs_result,
        recent_result,
    ]
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=False)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/schedules",
            "headers": [],
        }
    )

    with patch("sandcastle.api.routes.async_session", return_value=session_context) as factory:
        response = await list_schedules(request, limit=50, offset=0)

    assert factory.call_count == 1
    assert session.execute.await_count == 3
    assert response.data[0].status == "failing"
    assert response.data[0].success_rate == 0.5
    assert response.data[1].status == "active"
    assert response.data[1].success_rate == 1.0
