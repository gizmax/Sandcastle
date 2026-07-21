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


class TestTemplateReplayFallback:
    """Replay of runs started from hub templates loads YAML from the catalog."""

    def test_find_template_by_declared_name(self):
        from sandcastle.templates import find_template_yaml_by_workflow_name

        yaml_content = find_template_yaml_by_workflow_name("seo-content-writer")
        assert yaml_content is not None
        assert "name: seo-content-writer" in yaml_content

    def test_unknown_name_returns_none(self):
        from sandcastle.templates import find_template_yaml_by_workflow_name

        assert find_template_yaml_by_workflow_name("no-such-workflow-xyz") is None

    @pytest.mark.asyncio
    async def test_versioned_loader_falls_back_to_template(self, monkeypatch, tmp_path):
        from sandcastle.api.routes import _load_versioned_workflow_yaml

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))  # empty dir
        yaml_content = await _load_versioned_workflow_yaml("seo-content-writer", None)
        assert "name: seo-content-writer" in yaml_content

    @pytest.mark.asyncio
    async def test_versioned_loader_still_raises_for_unknown(self, monkeypatch, tmp_path):
        from sandcastle.api.routes import _load_versioned_workflow_yaml

        monkeypatch.setattr(settings, "workflows_dir", str(tmp_path))
        with pytest.raises(FileNotFoundError):
            await _load_versioned_workflow_yaml("no-such-workflow-xyz", None)
