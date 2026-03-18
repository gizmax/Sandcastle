"""Tests for the tamper-evident audit trail system.

Covers:
- compute_audit_hash produces consistent deterministic output
- append_audit_event creates correct hash chain (genesis -> chained)
- verify_audit_chain returns True for a valid chain
- verify_audit_chain detects tampered entry_hash
- verify_audit_chain detects tampered prev_hash
- Genesis hash for the first event in a chain
- API endpoints: GET /audit, GET /runs/{run_id}/audit, GET /audit/verify/{run_id}
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.audit import (
    _event_to_dict,
    append_audit_event,
    compute_audit_hash,
    verify_audit_chain,
)


# ---------------------------------------------------------------------------
# Unit tests for compute_audit_hash
# ---------------------------------------------------------------------------


class TestComputeAuditHash:
    def test_deterministic_output(self):
        h1 = compute_audit_hash(
            event_type="run.started",
            run_id="abc",
            actor_id="system",
            timestamp="2026-01-01T00:00:00+00:00",
            payload={"workflow": "test"},
            prev_hash="genesis",
        )
        h2 = compute_audit_hash(
            event_type="run.started",
            run_id="abc",
            actor_id="system",
            timestamp="2026-01-01T00:00:00+00:00",
            payload={"workflow": "test"},
            prev_hash="genesis",
        )
        assert h1 == h2

    def test_different_inputs_produce_different_hashes(self):
        h1 = compute_audit_hash("run.started", "abc", "system", "t1", {}, "genesis")
        h2 = compute_audit_hash("run.completed", "abc", "system", "t1", {}, "genesis")
        assert h1 != h2

    def test_hash_length_is_64_hex_chars(self):
        h = compute_audit_hash("x", None, "y", "t", {}, "genesis")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_none_run_id_treated_as_empty_string(self):
        h1 = compute_audit_hash("x", None, "y", "t", {}, "genesis")
        h2 = compute_audit_hash("x", "", "y", "t", {}, "genesis")
        assert h1 == h2

    def test_payload_order_independent(self):
        """sort_keys=True means dict key order must not matter."""
        h1 = compute_audit_hash("x", None, "y", "t", {"a": 1, "b": 2}, "genesis")
        h2 = compute_audit_hash("x", None, "y", "t", {"b": 2, "a": 1}, "genesis")
        assert h1 == h2


# ---------------------------------------------------------------------------
# Integration-style tests using in-memory SQLite
# ---------------------------------------------------------------------------


def _make_async_session():
    """Return a context manager that yields an in-memory SQLite async session."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sandcastle.models.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False,
                                  connect_args={"check_same_thread": False})

    Session = async_sessionmaker(engine, expire_on_commit=False)

    class _AsyncCtx:
        async def __aenter__(self):
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._session = Session()
            return self._session

        async def __aexit__(self, *_):
            await self._session.close()

    return _AsyncCtx()


@pytest.mark.asyncio
async def test_first_event_uses_genesis_prev_hash():
    async with _make_async_session() as session:
        run_id = str(uuid.uuid4())
        ev = await append_audit_event(
            session=session,
            event_type="run.started",
            run_id=run_id,
            actor_id="system",
            payload={"workflow": "hello"},
        )
        await session.commit()

    assert ev is not None
    assert ev.prev_hash == "genesis"
    assert len(ev.entry_hash) == 64


@pytest.mark.asyncio
async def test_chained_events_link_correctly():
    """Second event's prev_hash must match first event's entry_hash."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sandcastle.models.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False,
                                  connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    run_id = str(uuid.uuid4())

    async with Session() as session:
        ev1 = await append_audit_event(
            session=session,
            event_type="run.started",
            run_id=run_id,
            actor_id="system",
            payload={"workflow": "test"},
        )
        await session.commit()

    async with Session() as session:
        ev2 = await append_audit_event(
            session=session,
            event_type="step.started",
            run_id=run_id,
            actor_id="system",
            payload={"step_id": "step1"},
        )
        await session.commit()

    assert ev2 is not None
    assert ev2.prev_hash == ev1.entry_hash


@pytest.mark.asyncio
async def test_verify_audit_chain_valid():
    """verify_audit_chain returns (True, N, None) for an intact chain."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sandcastle.models.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False,
                                  connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    run_id = str(uuid.uuid4())

    async with Session() as session:
        for event_type in ("run.started", "step.started", "step.completed", "run.completed"):
            await append_audit_event(
                session=session,
                event_type=event_type,
                run_id=run_id,
                actor_id="system",
                payload={"step_id": "s1"},
            )
            await session.flush()
        await session.commit()

    async with Session() as session:
        valid, length, broken = await verify_audit_chain(session, run_id)

    assert valid is True
    assert length == 4
    assert broken is None


@pytest.mark.asyncio
async def test_verify_audit_chain_detects_tampered_entry_hash():
    """Tampering entry_hash of an event should be detected."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sandcastle.models.db import AuditEvent, Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False,
                                  connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    run_id = str(uuid.uuid4())

    async with Session() as session:
        for event_type in ("run.started", "step.started"):
            await append_audit_event(
                session=session,
                event_type=event_type,
                run_id=run_id,
                actor_id="system",
                payload={},
            )
            await session.flush()
        await session.commit()

    # Tamper: change entry_hash of the first event
    async with Session() as session:
        run_uuid = uuid.UUID(run_id)
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.run_id == run_uuid)
            .order_by(AuditEvent.created_at.asc())
            .limit(1)
        )
        result = await session.execute(stmt)
        first = result.scalar_one()
        first.entry_hash = "a" * 64  # tampered
        await session.commit()

    async with Session() as session:
        valid, length, broken = await verify_audit_chain(session, run_id)

    assert valid is False
    assert broken is not None


@pytest.mark.asyncio
async def test_verify_audit_chain_detects_tampered_prev_hash():
    """Tampering prev_hash of an event (but leaving entry_hash intact) also breaks verification."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sandcastle.models.db import AuditEvent, Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False,
                                  connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    run_id = str(uuid.uuid4())

    async with Session() as session:
        for et in ("run.started", "run.completed"):
            await append_audit_event(
                session=session,
                event_type=et,
                run_id=run_id,
                actor_id="system",
                payload={},
            )
            await session.flush()
        await session.commit()

    # Tamper: change prev_hash of the second event
    async with Session() as session:
        run_uuid = uuid.UUID(run_id)
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.run_id == run_uuid)
            .order_by(AuditEvent.created_at.asc())
            .offset(1)
            .limit(1)
        )
        result = await session.execute(stmt)
        second = result.scalar_one()
        second.prev_hash = "b" * 64  # tampered prev_hash
        await session.commit()

    async with Session() as session:
        valid, length, broken = await verify_audit_chain(session, run_id)

    assert valid is False
    assert broken is not None


@pytest.mark.asyncio
async def test_verify_empty_chain_is_valid():
    """A run with zero audit events has a valid (empty) chain."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sandcastle.models.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False,
                                  connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    run_id = str(uuid.uuid4())

    async with Session() as session:
        valid, length, broken = await verify_audit_chain(session, run_id)

    assert valid is True
    assert length == 0
    assert broken is None


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def _make_app():
    """Build a minimal FastAPI app with the Sandcastle router for testing.

    The production app mounts the router under /api, but for unit tests we
    mount it at the root so tests can use /audit directly.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sandcastle.api.routes import router

    app = FastAPI()
    app.include_router(router)  # no prefix - routes use /audit directly
    return TestClient(app, raise_server_exceptions=False)


class TestAuditAPIEndpoints:
    """Smoke-test the three audit API endpoints via TestClient."""

    @pytest.fixture(autouse=True)
    def _patch_admin(self):
        """Bypass admin auth for all tests in this class."""
        with patch("sandcastle.api.routes._require_admin"):
            yield

    @pytest.fixture(autouse=True)
    def _patch_tenant(self):
        with patch("sandcastle.api.routes.get_tenant_id", return_value=None):
            yield

    @pytest.fixture(autouse=True)
    def _patch_audit_session(self):
        """Patch async_session to return an in-memory mock session."""
        # We mock the DB layer to avoid requiring a real database in unit tests.
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # List endpoint: return empty list
        mock_execute_result = MagicMock()
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_execute_result)
        mock_session.scalar = AsyncMock(return_value=0)

        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            yield mock_session

    def test_list_audit_events_returns_200(self):
        client = _make_app()
        resp = client.get("/audit")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"] == []

    def test_list_audit_events_filter_by_run_id(self):
        client = _make_app()
        run_id = str(uuid.uuid4())
        resp = client.get(f"/audit?run_id={run_id}")
        assert resp.status_code == 200

    def test_list_audit_events_invalid_run_id(self):
        client = _make_app()
        resp = client.get("/audit?run_id=not-a-uuid")
        assert resp.status_code == 400

    def test_verify_run_audit_invalid_uuid(self):
        client = _make_app()
        with patch("sandcastle.engine.audit.verify_audit_chain", new_callable=AsyncMock,
                   return_value=(True, 0, None)):
            resp = client.get("/audit/verify/not-a-uuid")
        assert resp.status_code == 400

    def test_verify_run_audit_valid_chain(self):
        client = _make_app()
        run_id = str(uuid.uuid4())
        with patch(
            "sandcastle.api.routes.verify_audit_chain",
            new_callable=AsyncMock,
            return_value=(True, 3, None),
        ):
            resp = client.get(f"/audit/verify/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["valid"] is True
        assert body["data"]["chain_length"] == 3
        assert body["data"]["broken_at"] is None


# ---------------------------------------------------------------------------
# Test _event_to_dict helper
# ---------------------------------------------------------------------------


def test_event_to_dict_serializes_correctly():
    ev = MagicMock()
    ev.id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    ev.event_type = "run.started"
    ev.run_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    ev.actor_id = "system"
    ev.actor_key_prefix = "sk-12345"
    ev.source_ip = "127.0.0.1"
    ev.payload = {"workflow": "test"}
    ev.prev_hash = "genesis"
    ev.entry_hash = "a" * 64
    ev.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    d = _event_to_dict(ev)
    assert d["event_type"] == "run.started"
    assert d["actor_id"] == "system"
    assert d["prev_hash"] == "genesis"
    assert d["run_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_event_to_dict_none_run_id():
    ev = MagicMock()
    ev.id = uuid.uuid4()
    ev.event_type = "api_key.created"
    ev.run_id = None
    ev.actor_id = "admin"
    ev.actor_key_prefix = None
    ev.source_ip = None
    ev.payload = {}
    ev.prev_hash = "genesis"
    ev.entry_hash = "b" * 64
    ev.created_at = None

    d = _event_to_dict(ev)
    assert d["run_id"] is None
    assert d["created_at"] is None
