"""restore_db_settings is shared by the API lifespan and the arq worker startup.

Without the worker-side call, dashboard-managed settings (provider keys,
workflow_default_model) never reached the process that actually executes steps.
"""

from __future__ import annotations

import pytest

from sandcastle.config import settings
from sandcastle.db_settings import restore_db_settings
from sandcastle.models.db import Setting, async_session


async def _seed(key: str, value: str) -> None:
    async with async_session() as session:
        await session.merge(Setting(key=key, value=value))
        await session.commit()


@pytest.mark.asyncio
async def test_restores_workflow_default_model(monkeypatch):
    monkeypatch.setattr(settings, "workflow_default_model", "")
    await _seed("workflow_default_model", "nim/ornith")
    applied = await restore_db_settings()
    assert applied >= 1
    assert settings.workflow_default_model == "nim/ornith"


@pytest.mark.asyncio
async def test_non_restorable_keys_skipped(monkeypatch):
    before = settings.auth_required
    await _seed("auth_required", "true" if not before else "false")
    await restore_db_settings()
    assert settings.auth_required == before


@pytest.mark.asyncio
async def test_invalid_value_ignored(monkeypatch):
    before = settings.max_workflow_depth
    await _seed("max_workflow_depth", "not-a-number")
    await restore_db_settings()
    assert settings.max_workflow_depth == before
