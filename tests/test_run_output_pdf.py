"""Regression tests for run-level PDF downloads."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_run_output_pdf_renders_and_is_tenant_scoped(tmp_path, monkeypatch):
    from sandcastle.api import routes
    from sandcastle.models.db import Run, RunStatus, async_session

    run = Run(
        workflow_name="pdf-regression",
        tenant_id="tenant-a",
        status=RunStatus.COMPLETED,
        output_data={
            "report": "Curly “quotes” and a long bullet:\n"
            + " " * 120
            + "- deeply nested",
        },
        total_cost_usd=0.01,
    )
    async with async_session() as session:
        session.add(run)
        await session.commit()
        run_id = run.id

    monkeypatch.setattr(routes.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(routes.settings, "auth_required", True)
    with patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-a"):
        response = await routes.download_run_output_pdf(str(run_id), MagicMock())

    assert response.media_type == "application/pdf"
    assert response.path.exists()
    assert response.path.read_bytes().startswith(b"%PDF")

    with (
        patch("sandcastle.api.routes.get_tenant_id", return_value="tenant-b"),
        pytest.raises(HTTPException) as exc_info,
    ):
        await routes.download_run_output_pdf(str(run_id), MagicMock())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_run_output_pdf_rejects_invalid_id():
    from sandcastle.api import routes

    with pytest.raises(HTTPException) as exc_info:
        await routes.download_run_output_pdf("not-a-uuid", MagicMock())

    assert exc_info.value.status_code == 400
