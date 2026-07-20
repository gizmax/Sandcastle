"""Focused API coverage for workflow evolution listing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_list_evolutions_is_empty_then_tenant_scoped(monkeypatch):
    """The evolution list is newest-first and never exposes another tenant's data."""
    from sandcastle.api import routes
    from sandcastle.models.db import WorkflowEvolution, async_session

    tenant_a = f"evolution-list-a-{uuid.uuid4().hex}"
    tenant_b = f"evolution-list-b-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)

    # The route is admin-gated in production. Bypass that gate here so this
    # focused test can verify the tenant filter itself.
    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes.settings, "auth_required", True)

    async def list_for(tenant_id: str):
        with patch("sandcastle.api.routes.get_tenant_id", return_value=tenant_id):
            return await routes.list_evolutions(MagicMock())

    assert (await list_for(tenant_a)).data == []

    older = WorkflowEvolution(
        workflow_name=f"evolution-old-{uuid.uuid4().hex}",
        tenant_id=tenant_a,
        status="completed",
        optimize_for="quality",
        baseline_score=80.0,
        best_score=90.0,
        max_iterations=3,
        current_iteration=3,
        created_at=now - timedelta(minutes=1),
        completed_at=now - timedelta(minutes=1),
    )
    newest = WorkflowEvolution(
        workflow_name=f"evolution-new-{uuid.uuid4().hex}",
        tenant_id=tenant_a,
        status="running",
        optimize_for="balanced",
        max_iterations=5,
        current_iteration=2,
        created_at=now,
    )
    async with async_session() as session:
        session.add_all([older, newest])
        await session.commit()

    tenant_a_list = (await list_for(tenant_a)).data
    assert [item.id for item in tenant_a_list] == [str(newest.id), str(older.id)]
    assert tenant_a_list[0].workflow_name == newest.workflow_name
    assert tenant_a_list[0].budget_limit_usd is None

    assert (await list_for(tenant_b)).data == []
