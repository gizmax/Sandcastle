"""Focused API coverage for workflow evolution listing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware


@pytest.mark.asyncio
async def test_start_evolution_persists_visible_record_before_response(monkeypatch):
    """The 202 response contains a real ID whose queued row is already committed."""
    from sandcastle.api import routes
    from sandcastle.engine import evolution

    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "workflow_name": "test-workflow",
            "eval_suite_yaml": (
                "suite:\n"
                "  cases:\n"
                "    - name: baseline\n"
                "      input: {}\n"
                "      assertions:\n"
                "        - type: not_empty\n"
            ),
            "max_iterations": 2,
        }
    )
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.scalar = AsyncMock(return_value=None)

    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes, "get_tenant_id", lambda _req: "tenant-a")
    monkeypatch.setattr(routes, "parse_yaml_string", MagicMock())
    monkeypatch.setattr(routes, "validate", MagicMock(return_value=[]))
    monkeypatch.setattr(routes, "async_session", MagicMock(return_value=session))
    enqueue = AsyncMock()
    monkeypatch.setattr(routes, "enqueue_evolution", enqueue)
    monkeypatch.setattr(evolution, "_get_workflow_yaml", lambda _name: "name: test-workflow")

    response = await routes.start_evolution(request)

    persisted = session.add.call_args.args[0]
    assert session.commit.await_count == 1
    assert response.data.evolution_id == str(persisted.id)
    assert persisted.status == "queued"
    assert persisted.tenant_id == "tenant-a"
    assert "workflow: test-workflow" in persisted.eval_suite_yaml
    assert "suite:" not in persisted.eval_suite_yaml
    enqueue.assert_awaited_once_with(
        str(persisted.id),
        "test-workflow",
        persisted.eval_suite_yaml,
        2,
        "balanced",
        budget_limit=None,
        tenant_id="tenant-a",
    )


@pytest.mark.asyncio
async def test_start_evolution_reports_queue_failure(monkeypatch):
    """A queue outage is reported immediately instead of returning a false start."""
    from sandcastle.api import routes
    from sandcastle.engine import evolution

    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "workflow_name": "test-workflow",
            "eval_suite_yaml": (
                "cases:\n"
                "  - name: baseline\n"
                "    input: {}\n"
                "    assertions:\n"
                "      - type: not_empty\n"
            ),
        }
    )
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.scalar = AsyncMock(return_value=None)

    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes, "get_tenant_id", lambda _req: None)
    monkeypatch.setattr(routes, "parse_yaml_string", MagicMock())
    monkeypatch.setattr(routes, "validate", MagicMock(return_value=[]))
    monkeypatch.setattr(routes, "async_session", MagicMock(return_value=session))
    monkeypatch.setattr(
        routes,
        "enqueue_evolution",
        AsyncMock(side_effect=ConnectionError("queue offline")),
    )
    monkeypatch.setattr(evolution, "_get_workflow_yaml", lambda _name: "name: test-workflow")

    with pytest.raises(HTTPException) as exc_info:
        await routes.start_evolution(request)

    assert exc_info.value.status_code == 503
    assert "queue offline" in exc_info.value.detail


@pytest.mark.asyncio
async def test_start_evolution_rejects_invalid_json(monkeypatch):
    from sandcastle.api import routes

    request = MagicMock()
    request.json = AsyncMock(side_effect=ValueError("malformed"))
    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)

    with pytest.raises(HTTPException) as exc_info:
        await routes.start_evolution(request)

    assert exc_info.value.status_code == 400
    assert "Invalid JSON body" in exc_info.value.detail


@pytest.mark.asyncio
async def test_start_evolution_rejects_duplicate_active_workflow(monkeypatch):
    """A second active evolution cannot become an uncontrollable superseded job."""
    from sandcastle.api import routes
    from sandcastle.engine import evolution

    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "workflow_name": "test-workflow",
            "eval_suite_yaml": (
                "cases:\n"
                "  - name: baseline\n"
                "    input: {}\n"
                "    assertions:\n"
                "      - type: not_empty\n"
            ),
        }
    )
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock(return_value=uuid.uuid4())
    session.add = MagicMock()

    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes, "get_tenant_id", lambda _req: "tenant-a")
    monkeypatch.setattr(routes, "parse_yaml_string", MagicMock())
    monkeypatch.setattr(routes, "validate", MagicMock(return_value=[]))
    monkeypatch.setattr(routes, "async_session", MagicMock(return_value=session))
    monkeypatch.setattr(routes, "enqueue_evolution", AsyncMock())
    monkeypatch.setattr(evolution, "_get_workflow_yaml", lambda _name: "name: test-workflow")

    with pytest.raises(HTTPException) as exc_info:
        await routes.start_evolution(request)

    assert exc_info.value.status_code == 409
    session.add.assert_not_called()
    routes.enqueue_evolution.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_evolution_rejects_invalid_workflow_before_queueing(monkeypatch):
    """Invalid dependencies are reported synchronously without creating a job."""
    from sandcastle.api import routes
    from sandcastle.engine import evolution

    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "workflow_name": "invalid-workflow",
            "eval_suite_yaml": (
                "cases:\n"
                "  - name: baseline\n"
                "    input: {}\n"
                "    assertions:\n"
                "      - type: not_empty\n"
            ),
        }
    )
    invalid_workflow = """
name: invalid-workflow
steps:
  - id: answer
    prompt: Answer
    depends_on: [missing]
"""
    enqueue = AsyncMock()
    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes, "get_tenant_id", lambda _req: None)
    monkeypatch.setattr(routes, "enqueue_evolution", enqueue)
    monkeypatch.setattr(evolution, "_get_workflow_yaml", lambda _name: invalid_workflow)

    with pytest.raises(HTTPException) as exc_info:
        await routes.start_evolution(request)

    assert exc_info.value.status_code == 400
    assert "missing" in exc_info.value.detail
    enqueue.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_evolution_handlers_do_not_cross_tenant_boundaries(monkeypatch):
    """A tenant-scoped caller cannot inspect, promote, cancel, or count another tenant's runs."""
    from sandcastle.api import routes
    from sandcastle.models.db import WorkflowEvolution, async_session

    tenant_a = f"evolution-handler-a-{uuid.uuid4().hex}"
    tenant_b = f"evolution-handler-b-{uuid.uuid4().hex}"
    workflow_name = f"evolution-tenant-b-{uuid.uuid4().hex}"
    evolution_id = uuid.uuid4()

    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes.settings, "auth_required", True)

    async with async_session() as session:
        session.add(
            WorkflowEvolution(
                id=evolution_id,
                workflow_name=workflow_name,
                tenant_id=tenant_b,
                status="running",
                optimize_for="quality",
                baseline_score=75.0,
                best_score=80.0,
                max_iterations=3,
            )
        )
        await session.commit()

    request = MagicMock()
    request.json = AsyncMock(return_value={})

    with patch("sandcastle.api.routes.get_tenant_id", return_value=tenant_a):
        with pytest.raises(HTTPException) as status_error:
            await routes.get_evolution_status(workflow_name, request)
        with pytest.raises(HTTPException) as accept_error:
            await routes.accept_evolution(workflow_name, request)
        with pytest.raises(HTTPException) as cancel_error:
            await routes.cancel_evolution(workflow_name, request)
        stats = await routes.get_evolution_stats(request)

    assert status_error.value.status_code == 404
    assert accept_error.value.status_code == 404
    assert cancel_error.value.status_code == 404
    assert stats.data.total_evolutions == 0
    assert stats.data.total_improvements == 0

    async with async_session() as session:
        evolution = await session.get(WorkflowEvolution, evolution_id)
    assert evolution is not None
    assert evolution.status == "running"


@pytest.mark.asyncio
async def test_evolution_status_returns_latest_run_for_workflow(monkeypatch):
    """Repeated evolutions for one workflow return the newest run without a 500."""
    from sandcastle.api import routes
    from sandcastle.models.db import WorkflowEvolution, async_session

    tenant_id = f"evolution-latest-{uuid.uuid4().hex}"
    workflow_name = f"evolution-repeat-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    older_id = uuid.uuid4()
    latest_id = uuid.uuid4()

    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes.settings, "auth_required", True)

    async with async_session() as session:
        session.add_all(
            [
                WorkflowEvolution(
                    id=older_id,
                    workflow_name=workflow_name,
                    tenant_id=tenant_id,
                    status="completed",
                    optimize_for="quality",
                    baseline_score=70.0,
                    best_score=80.0,
                    max_iterations=3,
                    current_iteration=3,
                    created_at=now - timedelta(minutes=1),
                ),
                WorkflowEvolution(
                    id=latest_id,
                    workflow_name=workflow_name,
                    tenant_id=tenant_id,
                    status="running",
                    optimize_for="cost",
                    baseline_score=80.0,
                    best_score=85.0,
                    max_iterations=5,
                    current_iteration=2,
                    created_at=now,
                ),
            ]
        )
        await session.commit()

    with patch("sandcastle.api.routes.get_tenant_id", return_value=tenant_id):
        response = await routes.get_evolution_status(workflow_name, MagicMock())

    assert response.data.evolution_id == str(latest_id)
    assert response.data.status == "running"
    assert response.data.optimize_for == "cost"

    with patch("sandcastle.api.routes.get_tenant_id", return_value=tenant_id):
        cancelled = await routes.cancel_evolution(workflow_name, MagicMock())

    assert cancelled.data["cancelled"] is True
    async with async_session() as session:
        older = await session.get(WorkflowEvolution, older_id)
        latest = await session.get(WorkflowEvolution, latest_id)
    assert older is not None and older.status == "completed"
    assert latest is not None and latest.status == "cancelled"


@pytest.mark.asyncio
async def test_accept_evolution_archives_old_production_and_marks_latest_accepted(
    monkeypatch,
):
    """Accept is an atomic promotion and leaves exactly one production version."""
    from sqlalchemy import select

    from sandcastle.api import routes
    from sandcastle.models.db import (
        WorkflowEvolution,
        WorkflowVersion,
        WorkflowVersionStatus,
        async_session,
    )

    tenant_id = f"evolution-accept-{uuid.uuid4().hex}"
    workflow_name = f"accepted-workflow-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    best_yaml = (
        f"name: {workflow_name}\n"
        "steps:\n"
        "  - id: answer\n"
        "    prompt: Say hello\n"
    )
    older = WorkflowEvolution(
        workflow_name=workflow_name,
        tenant_id=tenant_id,
        status="completed",
        optimize_for="quality",
        best_variant_yaml=best_yaml,
        best_score=70.0,
        best_quality=0.7,
        max_iterations=1,
        created_at=now - timedelta(minutes=1),
    )
    latest = WorkflowEvolution(
        workflow_name=workflow_name,
        tenant_id=tenant_id,
        status="completed",
        optimize_for="quality",
        best_variant_yaml=best_yaml,
        best_score=90.0,
        best_quality=0.9,
        max_iterations=2,
        created_at=now,
    )
    production = WorkflowVersion(
        workflow_name=workflow_name,
        version=1,
        status=WorkflowVersionStatus.PRODUCTION,
        yaml_content=best_yaml,
        steps_count=1,
        checksum="0" * 64,
    )

    async with async_session() as session:
        session.add_all([older, latest, production])
        await session.commit()

    request = MagicMock()
    request.json = AsyncMock(return_value={})
    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes.settings, "auth_required", True)

    with patch("sandcastle.api.routes.get_tenant_id", return_value=tenant_id):
        response = await routes.accept_evolution(workflow_name, request)
        stats = await routes.get_evolution_stats(MagicMock())

    assert response.data["accepted"] is True
    assert response.data["version"] == 2
    assert stats.data.completed_evolutions == 2

    async with async_session() as session:
        refreshed_older = await session.get(WorkflowEvolution, older.id)
        refreshed_latest = await session.get(WorkflowEvolution, latest.id)
        versions = (
            await session.execute(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_name == workflow_name)
                .order_by(WorkflowVersion.version)
            )
        ).scalars().all()

    assert refreshed_older is not None and refreshed_older.status == "completed"
    assert refreshed_latest is not None and refreshed_latest.status == "accepted"
    assert [version.status for version in versions] == [
        WorkflowVersionStatus.ARCHIVED,
        WorkflowVersionStatus.PRODUCTION,
    ]


@pytest.mark.asyncio
async def test_accept_evolution_validates_notes_before_database_access(monkeypatch):
    from sandcastle.api import routes

    request = MagicMock()
    request.json = AsyncMock(return_value={"notes": "x" * 501})
    session_factory = MagicMock()
    monkeypatch.setattr(routes, "_require_admin", lambda _req: None)
    monkeypatch.setattr(routes, "async_session", session_factory)

    with pytest.raises(HTTPException) as exc_info:
        await routes.accept_evolution("workflow", request)

    assert exc_info.value.status_code == 422
    session_factory.assert_not_called()


def test_real_auth_middleware_protects_evolution_list(monkeypatch):
    """The root-mounted evolution router enforces the real admin gate over HTTP."""
    from sandcastle.api import routes
    from sandcastle.api.auth import auth_middleware, hash_key
    from sandcastle.config import settings
    from sandcastle.models.db import ApiKey, async_session

    monkeypatch.setattr(settings, "auth_required", True)

    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)

    tenant_key = f"sc_evo_tenant_{uuid.uuid4().hex}"

    async def insert_tenant_key() -> None:
        async with async_session() as session:
            session.add(
                ApiKey(
                    key_hash=hash_key(tenant_key),
                    key_prefix=tenant_key[:8],
                    tenant_id="tenant_a",
                    name="evolution tenant key",
                )
            )
            await session.commit()

    asyncio.run(insert_tenant_key())

    assert client.get("/api/evolution").status_code == 401
    assert client.get("/api/evolution", headers={"X-API-Key": tenant_key}).status_code == 403
