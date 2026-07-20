"""Regression coverage for routes.py robustness fixes (wave D1)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from sandcastle.api import agent_webhooks, routes
from sandcastle.api.schemas import UpdateRequest, WorkflowRunRequest
from sandcastle.models.db import AuditEvent, RunStatus, async_session


class _ChunkedUpload:
    """Minimal UploadFile replacement that records bounded reads."""

    filename = "upload.txt"

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._offset = 0
        self.read_sizes: list[int] = []
        self.bytes_returned = 0

    async def read(self, size: int = -1) -> bytes:
        assert size >= 0, "uploads must be read in bounded chunks"
        self.read_sizes.append(size)
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        self.bytes_returned += len(chunk)
        return chunk


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(tenant_id=None))


@pytest.mark.asyncio
async def test_upload_rejects_over_limit_without_reading_past_limit_plus_one(monkeypatch):
    """The rejected upload is bounded at max_bytes + 1, not full file size."""
    upload = _ChunkedUpload(b"x" * 100)
    monkeypatch.setattr(routes.settings, "storage_backend", "local")
    monkeypatch.setattr(routes, "_UPLOAD_MAX_BYTES_LOCAL", 4)
    monkeypatch.setattr(routes, "_UPLOAD_READ_CHUNK_BYTES", 2)

    with pytest.raises(HTTPException) as exc_info:
        await routes.upload_file(upload)

    assert exc_info.value.status_code == 400
    assert upload.read_sizes == [2, 2, 1]
    assert upload.bytes_returned == 5


@pytest.mark.asyncio
async def test_upload_small_file_still_succeeds_with_chunked_reads(monkeypatch, tmp_path):
    upload = _ChunkedUpload(b"small")
    monkeypatch.setattr(routes.settings, "storage_backend", "local")
    monkeypatch.setattr(routes.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(routes, "_UPLOAD_MAX_BYTES_LOCAL", 10)
    monkeypatch.setattr(routes, "_UPLOAD_READ_CHUNK_BYTES", 2)

    response = await routes.upload_file(upload)

    assert response.data["size_bytes"] == 5
    assert upload.read_sizes == [2, 2, 2, 2]
    assert (tmp_path / "uploads" / response.data["path"].split("/")[-1]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [routes.run_workflow_sync, routes.run_workflow_async])
async def test_idempotency_integrity_error_returns_existing_run(monkeypatch, runner):
    """A duplicate INSERT race returns the already-created run instead of a 5xx."""
    existing_id = uuid.uuid4()
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock(side_effect=[None, existing_id])
    session.commit = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, RuntimeError("duplicate idempotency key"))
    )
    session.rollback = AsyncMock()

    workflow = SimpleNamespace(name="race-test", input_schema=None, risk_level="minimal")
    monkeypatch.setattr(routes.settings, "auth_required", False)
    monkeypatch.setattr(routes.execution_limiter, "check", AsyncMock())
    monkeypatch.setattr(
        routes, "_resolve_workflow_request", AsyncMock(return_value=("name: race-test", 1))
    )
    monkeypatch.setattr(routes, "parse_yaml_string", MagicMock(return_value=workflow))
    monkeypatch.setattr(routes, "validate", MagicMock(return_value=[]))
    monkeypatch.setattr(routes, "_resolve_budget", AsyncMock(return_value=None))
    monkeypatch.setattr(routes, "async_session", MagicMock(return_value=session))
    monkeypatch.setattr(routes, "build_plan", MagicMock())
    monkeypatch.setattr(routes, "create_storage", MagicMock())

    response = await runner(
        WorkflowRunRequest(workflow="name: race-test", idempotency_key="same-request"),
        _request(),
    )

    assert response.data.run_id == str(existing_id)
    assert response.data.idempotent is True
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [
        RunStatus.PARTIAL,
        RunStatus.CANCELLED,
        RunStatus.BUDGET_EXCEEDED,
        RunStatus.AWAITING_APPROVAL,
    ],
)
async def test_batch_poller_reports_all_terminal_statuses_without_timeout(
    monkeypatch, terminal_status
):
    """Non-completed terminal states finish the batch item on its first poll."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(status=terminal_status, total_cost_usd=0.25, error="done")
    )
    workflow = SimpleNamespace(name="batch-test", input_schema=None, risk_level="minimal")
    request = _request()
    request.json = AsyncMock(return_value={"items": [{"value": "one"}], "max_parallel": 1})

    monkeypatch.setattr(routes.settings, "auth_required", False)
    monkeypatch.setattr(routes.execution_limiter, "check", AsyncMock())
    monkeypatch.setattr(
        routes, "_resolve_workflow_request", AsyncMock(return_value=("name: batch-test", 1))
    )
    monkeypatch.setattr(routes, "parse_yaml_string", MagicMock(return_value=workflow))
    monkeypatch.setattr(routes, "validate", MagicMock(return_value=[]))
    monkeypatch.setattr(routes, "_resolve_budget", AsyncMock(return_value=None))
    monkeypatch.setattr(routes, "enqueue_workflow", AsyncMock())
    monkeypatch.setattr(routes, "async_session", MagicMock(return_value=session))
    monkeypatch.setattr(routes.asyncio, "sleep", AsyncMock())

    response = await routes.batch_run_workflow("batch-test", request)
    batch_id = response.data["batch_id"]
    await asyncio.gather(*list(routes._background_tasks))

    item = routes._batch_store[batch_id]["items"][0]
    assert item["status"] == terminal_status.value
    assert item["error"] == "done"
    assert routes._batch_store[batch_id]["failed"] == 1


@pytest.mark.asyncio
async def test_background_tasks_are_retained_discarded_and_logged(caplog):
    """Both API task helpers retain work and consume/log task failures."""

    async def completes() -> None:
        return None

    async def raises() -> None:
        raise RuntimeError("background failure")

    for module in (routes, agent_webhooks):
        task = module._create_background_task(completes())
        assert task in module._background_tasks
        await task
        await asyncio.sleep(0)
        assert task not in module._background_tasks

        with caplog.at_level(logging.ERROR):
            failed_task = module._create_background_task(raises())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert failed_task not in module._background_tasks

    assert "background failure" in caplog.text


@pytest.mark.asyncio
async def test_run_stream_stops_with_error_after_max_duration(monkeypatch):
    """A queued run stream ends with a terminal error instead of polling forever."""
    run_id = uuid.uuid4()
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock(return_value=run_id)
    request = _request()
    request.is_disconnected = AsyncMock(return_value=False)

    monkeypatch.setattr(routes.settings, "auth_required", False)
    monkeypatch.setattr(routes, "async_session", MagicMock(return_value=session))
    monkeypatch.setattr(routes, "SSE_MAX_DURATION_SECONDS", 0)

    response = await routes.stream_run(str(run_id), request)
    events = [event async for event in response.body_iterator]

    assert len(events) == 1
    assert "event: error" in events[0]
    assert "Run not progressing" in json.loads(events[0].split("data: ")[1])["message"]


@pytest.mark.asyncio
async def test_update_audit_event_is_committed():
    """The update audit helper makes its flush-only audit event durable."""
    event_type = f"update.test-{uuid.uuid4()}"

    await routes._emit_update_audit(event_type, {"source": "wave-d1"})

    async with async_session() as session:
        event = await session.scalar(select(AuditEvent).where(AuditEvent.event_type == event_type))

    assert event is not None
    assert event.payload == {"source": "wave-d1"}


@pytest.mark.asyncio
async def test_triggered_update_persists_its_audit_events(monkeypatch):
    """A successful update operation leaves its started and completed audit rows."""
    target_version = "99.0.0"
    install_proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
    verify_proc = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(target_version.encode(), b""))
    )
    request = _request()
    request.client = SimpleNamespace(host="127.0.0.1")

    monkeypatch.setattr(routes.settings, "auth_required", False)
    monkeypatch.setattr(routes.settings, "update_channel", "stable")
    monkeypatch.setattr(routes, "_is_in_blackout_window", MagicMock(return_value=False))
    monkeypatch.setattr(routes, "_pre_update_backup", AsyncMock(return_value=None))
    monkeypatch.setattr(
        routes.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=[install_proc, verify_proc]),
    )

    response = await routes.trigger_update(request, UpdateRequest(target_version=target_version))

    assert response.data.status == "success"
    async with async_session() as session:
        events = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type.in_(["update.started", "update.completed"])
                    )
                )
            )
            .scalars()
            .all()
        )

    assert any(event.payload.get("target_version") == target_version for event in events)
    assert any(event.payload.get("new_version") == target_version for event in events)
