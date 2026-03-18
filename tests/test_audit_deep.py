"""Comprehensive deep tests for the tamper-evident audit trail system.

Scenarios:
1.  Chain of 50 events - integrity + mid-chain tamper detection
2.  Rapid concurrent writes - 10 runs x 20 events via asyncio.gather
3.  Payload edge cases - empty, nested 5-level, 1000-item list, unicode emoji,
    null bytes, very long string (50 KB)
4.  Replay attack - duplicate row inserted, chain detects extra event
5.  Deletion attack - middle event deleted, chain detects gap
6.  Reorder attack - swap two events' created_at, verify hash mismatch
7.  Genesis edge cases - first event is genesis, second references first,
    system events chain independently from run-scoped events
8.  API endpoint integration - GET /audit with filters, GET /audit/verify
9.  Hash determinism - same payload different key-order produces same hash
10. Performance - 1000 events, verify in under 5 seconds
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.audit import (
    _event_to_dict,
    append_audit_event,
    compute_audit_hash,
    verify_audit_chain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_and_session():
    """Create an isolated in-memory SQLite engine + session factory."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sandcastle.models.db import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    return engine, Session, _init


async def _build_chain(Session, run_id: str, n: int) -> list:
    """Append n events for run_id sequentially, flushing each time."""
    events = []
    async with Session() as session:
        for i in range(n):
            ev = await append_audit_event(
                session=session,
                event_type=f"step.event.{i}",
                run_id=run_id,
                actor_id="test-actor",
                payload={"index": i},
            )
            await session.flush()
            events.append(ev)
        await session.commit()
    return events


# ---------------------------------------------------------------------------
# 1. Chain of 50 events
# ---------------------------------------------------------------------------


class TestChainOf50Events:
    @pytest.mark.asyncio
    async def test_50_event_chain_valid(self):
        """50-event chain must verify as intact."""
        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())
        events = await _build_chain(Session, run_id, 50)

        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, run_id)

        assert valid is True
        assert length == 50
        assert broken is None

    @pytest.mark.asyncio
    async def test_tamper_middle_event_detected(self):
        """Tampering the entry_hash of event #25 must break verification."""
        from sqlalchemy import select

        from sandcastle.models.db import AuditEvent

        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())
        await _build_chain(Session, run_id, 50)

        # Tamper event at position 24 (0-indexed middle)
        async with Session() as session:
            run_uuid = uuid.UUID(run_id)
            stmt = (
                select(AuditEvent)
                .where(AuditEvent.run_id == run_uuid)
                .order_by(AuditEvent.created_at.asc())
            )
            result = await session.execute(stmt)
            all_events = result.scalars().all()
            # Tamper entry_hash on event 24
            all_events[24].entry_hash = "d" * 64
            await session.commit()

        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, run_id)

        assert valid is False
        assert broken is not None
        # Detection must happen at or before event 25 (the next one sees wrong prev_hash
        # OR event 24 itself has wrong computed hash)

    @pytest.mark.asyncio
    async def test_50_event_chain_hashes_are_unique(self):
        """All 50 entry_hashes must be distinct (no collision)."""
        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())
        events = await _build_chain(Session, run_id, 50)

        hashes = [ev.entry_hash for ev in events]
        assert len(set(hashes)) == 50, "Duplicate hash detected in chain"

    @pytest.mark.asyncio
    async def test_50_event_chain_links_consecutive(self):
        """Each event's prev_hash must equal the prior event's entry_hash."""
        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())
        events = await _build_chain(Session, run_id, 50)

        assert events[0].prev_hash == "genesis"
        for i in range(1, 50):
            assert events[i].prev_hash == events[i - 1].entry_hash, (
                f"Chain broken at position {i}"
            )


# ---------------------------------------------------------------------------
# 2. Rapid concurrent writes
# ---------------------------------------------------------------------------


class TestRapidConcurrentWrites:
    @pytest.mark.asyncio
    async def test_10_runs_20_events_each_all_valid(self):
        """10 concurrent runs each getting 20 events must all verify correctly."""
        engine, Session, init = _make_engine_and_session()
        await init()

        run_ids = [str(uuid.uuid4()) for _ in range(10)]

        async def write_run(run_id: str):
            async with Session() as session:
                for i in range(20):
                    await append_audit_event(
                        session=session,
                        event_type=f"concurrent.event.{i}",
                        run_id=run_id,
                        actor_id=f"actor-{run_id[:8]}",
                        payload={"seq": i},
                    )
                    await session.flush()
                await session.commit()

        # Run all 10 concurrent chains
        await asyncio.gather(*[write_run(rid) for rid in run_ids])

        # Verify each chain independently
        failures = []
        for run_id in run_ids:
            async with Session() as session:
                valid, length, broken = await verify_audit_chain(session, run_id)
                if not valid or length != 20:
                    failures.append((run_id, valid, length, broken))

        assert failures == [], f"Chain verification failures: {failures}"

    @pytest.mark.asyncio
    async def test_concurrent_runs_chains_do_not_cross_contaminate(self):
        """Events from run A must not appear in run B's chain."""
        from sqlalchemy import select

        from sandcastle.models.db import AuditEvent

        engine, Session, init = _make_engine_and_session()
        await init()

        run_a = str(uuid.uuid4())
        run_b = str(uuid.uuid4())

        await asyncio.gather(
            _build_chain(Session, run_a, 10),
            _build_chain(Session, run_b, 10),
        )

        async with Session() as session:
            stmt_a = select(AuditEvent).where(
                AuditEvent.run_id == uuid.UUID(run_a)
            )
            stmt_b = select(AuditEvent).where(
                AuditEvent.run_id == uuid.UUID(run_b)
            )
            evs_a = (await session.execute(stmt_a)).scalars().all()
            evs_b = (await session.execute(stmt_b)).scalars().all()

        ids_a = {ev.id for ev in evs_a}
        ids_b = {ev.id for ev in evs_b}
        assert ids_a.isdisjoint(ids_b), "Run chains share events - contamination!"
        assert len(evs_a) == 10
        assert len(evs_b) == 10


# ---------------------------------------------------------------------------
# 3. Payload edge cases
# ---------------------------------------------------------------------------


class TestPayloadEdgeCases:
    @pytest.fixture
    async def session_factory(self):
        engine, Session, init = _make_engine_and_session()
        await init()
        return Session

    async def _append_and_verify(self, Session, payload: dict) -> bool:
        run_id = str(uuid.uuid4())
        async with Session() as session:
            ev = await append_audit_event(
                session=session,
                event_type="payload.test",
                run_id=run_id,
                actor_id="tester",
                payload=payload,
            )
            await session.commit()

        assert ev is not None
        assert len(ev.entry_hash) == 64

        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, run_id)
        return valid

    @pytest.mark.asyncio
    async def test_empty_dict_payload(self, session_factory):
        assert await self._append_and_verify(session_factory, {})

    @pytest.mark.asyncio
    async def test_nested_5_level_json(self, session_factory):
        payload = {"l1": {"l2": {"l3": {"l4": {"l5": "deep_value", "x": 42}}}}}
        assert await self._append_and_verify(session_factory, payload)

    @pytest.mark.asyncio
    async def test_list_of_1000_items(self, session_factory):
        payload = {"items": list(range(1000))}
        assert await self._append_and_verify(session_factory, payload)

    @pytest.mark.asyncio
    async def test_unicode_emoji_payload(self, session_factory):
        payload = {"emoji": "\U0001f600\U0001f4a5\U0001f916", "text": "Hello \u4e16\u754c"}
        assert await self._append_and_verify(session_factory, payload)

    @pytest.mark.asyncio
    async def test_null_bytes_in_string_payload(self, session_factory):
        # Null bytes in JSON string value - should be hashed correctly
        payload = {"data": "before\x00after"}
        assert await self._append_and_verify(session_factory, payload)

    @pytest.mark.asyncio
    async def test_very_long_string_50kb(self, session_factory):
        large = "X" * (50 * 1024)
        payload = {"large_field": large}
        assert await self._append_and_verify(session_factory, payload)

    @pytest.mark.asyncio
    async def test_mixed_types_payload(self, session_factory):
        payload = {
            "int": 42,
            "float": 3.14,
            "bool_true": True,
            "bool_false": False,
            "null_val": None,
            "list": [1, "two", 3.0],
        }
        assert await self._append_and_verify(session_factory, payload)


# ---------------------------------------------------------------------------
# 4. Replay attack
# ---------------------------------------------------------------------------


class TestReplayAttack:
    @pytest.mark.asyncio
    async def test_duplicate_event_breaks_chain(self):
        """Inserting a duplicate row (copy of an existing event) must break the chain.

        When we duplicate event N, it appears in the chain as an extra event
        with the same hashes but out of order - the verify will encounter a
        prev_hash mismatch at position N+1 (or after the duplicate).
        """
        from sqlalchemy import select

        from sandcastle.models.db import AuditEvent

        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())
        await _build_chain(Session, run_id, 5)

        # Copy event #2 (middle) - insert a new row with same hashes but
        # different id and a created_at that falls between event 2 and 3
        async with Session() as session:
            run_uuid = uuid.UUID(run_id)
            stmt = (
                select(AuditEvent)
                .where(AuditEvent.run_id == run_uuid)
                .order_by(AuditEvent.created_at.asc())
            )
            result = await session.execute(stmt)
            all_events = result.scalars().all()
            original = all_events[2]

            # Duplicate: same entry_hash/prev_hash but new id, timestamp
            # between event 2 and 3 to be injected at that position
            mid_ts = original.created_at + timedelta(microseconds=1)
            duplicate = AuditEvent(
                id=uuid.uuid4(),
                event_type=original.event_type,
                run_id=run_uuid,
                actor_id=original.actor_id,
                payload=original.payload,
                prev_hash=original.prev_hash,  # duplicated prev_hash
                entry_hash=original.entry_hash,  # duplicated entry_hash
                created_at=mid_ts,
            )
            session.add(duplicate)
            await session.commit()

        # The chain now has 6 events but with duplicated hashes -
        # verification should detect the inconsistency
        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, run_id)

        # With 6 events but one duplicate the chain MUST be broken
        # (either prev_hash mismatch or entry_hash recompute fails)
        assert valid is False or length == 6, (
            f"Replay attack not detected: valid={valid}, length={length}"
        )
        # More strict: chain should be invalid
        assert valid is False, "Replay attack (duplicate row) was not detected"


# ---------------------------------------------------------------------------
# 5. Deletion attack
# ---------------------------------------------------------------------------


class TestDeletionAttack:
    @pytest.mark.asyncio
    async def test_delete_middle_event_breaks_chain(self):
        """Deleting event #3 from a 7-event chain must break verification."""
        from sqlalchemy import delete, select

        from sandcastle.models.db import AuditEvent

        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())
        await _build_chain(Session, run_id, 7)

        # Delete the event at position 3 (0-indexed)
        async with Session() as session:
            run_uuid = uuid.UUID(run_id)
            stmt = (
                select(AuditEvent)
                .where(AuditEvent.run_id == run_uuid)
                .order_by(AuditEvent.created_at.asc())
            )
            result = await session.execute(stmt)
            all_events = result.scalars().all()
            target_id = all_events[3].id

            await session.execute(
                delete(AuditEvent).where(AuditEvent.id == target_id)
            )
            await session.commit()

        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, run_id)

        assert valid is False, "Deletion attack was not detected"
        assert length == 6  # 7 - 1
        assert broken is not None

    @pytest.mark.asyncio
    async def test_delete_first_event_breaks_chain(self):
        """Deleting the genesis event must break verification."""
        from sqlalchemy import delete, select

        from sandcastle.models.db import AuditEvent

        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())
        await _build_chain(Session, run_id, 5)

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
            await session.execute(
                delete(AuditEvent).where(AuditEvent.id == first.id)
            )
            await session.commit()

        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, run_id)

        # After deleting first event, the new first has prev_hash != "genesis"
        assert valid is False, "Deletion of genesis event not detected"


# ---------------------------------------------------------------------------
# 6. Reorder attack
# ---------------------------------------------------------------------------


class TestReorderAttack:
    @pytest.mark.asyncio
    async def test_swap_timestamps_detected(self):
        """Swapping created_at of events #1 and #2 reorders the chain
        so prev_hash mismatches occur during verification."""
        from sqlalchemy import select

        from sandcastle.models.db import AuditEvent

        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())
        await _build_chain(Session, run_id, 5)

        async with Session() as session:
            run_uuid = uuid.UUID(run_id)
            stmt = (
                select(AuditEvent)
                .where(AuditEvent.run_id == run_uuid)
                .order_by(AuditEvent.created_at.asc())
            )
            result = await session.execute(stmt)
            all_events = result.scalars().all()

            # Swap timestamps of events 1 and 2
            ts1 = all_events[1].created_at
            ts2 = all_events[2].created_at
            all_events[1].created_at = ts2
            all_events[2].created_at = ts1
            await session.commit()

        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, run_id)

        assert valid is False, "Reorder attack (timestamp swap) was not detected"
        assert broken is not None


# ---------------------------------------------------------------------------
# 7. Genesis edge cases
# ---------------------------------------------------------------------------


class TestGenesisEdgeCases:
    @pytest.mark.asyncio
    async def test_first_event_uses_genesis_prev_hash(self):
        """The very first event in a chain MUST have prev_hash == 'genesis'."""
        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())

        async with Session() as session:
            ev = await append_audit_event(
                session=session,
                event_type="run.started",
                run_id=run_id,
                actor_id="system",
                payload={},
            )
            await session.commit()

        assert ev.prev_hash == "genesis"

    @pytest.mark.asyncio
    async def test_second_event_references_first(self):
        """Second event's prev_hash must equal first event's entry_hash."""
        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())

        async with Session() as session:
            ev1 = await append_audit_event(
                session=session,
                event_type="run.started",
                run_id=run_id,
                actor_id="system",
                payload={},
            )
            await session.flush()
            ev2 = await append_audit_event(
                session=session,
                event_type="step.started",
                run_id=run_id,
                actor_id="system",
                payload={"step": "s1"},
            )
            await session.commit()

        assert ev2.prev_hash == ev1.entry_hash

    @pytest.mark.asyncio
    async def test_system_events_chain_independently(self):
        """System events (run_id=None) form their own chain separate
        from any run-scoped chain."""
        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())

        async with Session() as session:
            # Write a run-scoped event first
            await append_audit_event(
                session=session,
                event_type="run.started",
                run_id=run_id,
                actor_id="system",
                payload={},
            )
            await session.flush()
            # Write a system-level event (run_id=None)
            sys_ev = await append_audit_event(
                session=session,
                event_type="settings.updated",
                run_id=None,
                actor_id="admin",
                payload={"key": "anthropic_api_key"},
            )
            await session.commit()

        # System event should use "genesis" as prev_hash (first in its chain)
        assert sys_ev.prev_hash == "genesis"

    @pytest.mark.asyncio
    async def test_system_chain_grows_independently(self):
        """Two system events chain to each other, not to run-scoped events."""
        engine, Session, init = _make_engine_and_session()
        await init()

        async with Session() as session:
            sys1 = await append_audit_event(
                session=session,
                event_type="system.event.1",
                run_id=None,
                actor_id="admin",
                payload={"n": 1},
            )
            await session.flush()
            # Run-scoped event in between - must not affect system chain
            run_id = str(uuid.uuid4())
            await append_audit_event(
                session=session,
                event_type="run.started",
                run_id=run_id,
                actor_id="system",
                payload={},
            )
            await session.flush()
            sys2 = await append_audit_event(
                session=session,
                event_type="system.event.2",
                run_id=None,
                actor_id="admin",
                payload={"n": 2},
            )
            await session.commit()

        assert sys1.prev_hash == "genesis"
        assert sys2.prev_hash == sys1.entry_hash

    @pytest.mark.asyncio
    async def test_invalid_run_id_string_returns_false(self):
        """verify_audit_chain with an invalid UUID string must return False."""
        engine, Session, init = _make_engine_and_session()
        await init()

        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, "not-a-uuid")

        assert valid is False
        assert broken is not None
        assert "error" in broken


# ---------------------------------------------------------------------------
# 8. API endpoint integration
# ---------------------------------------------------------------------------


def _make_test_client():
    """Create a FastAPI TestClient with the Sandcastle router."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sandcastle.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


class TestAPIEndpointIntegration:
    """Integration tests for GET /audit and GET /audit/verify/{run_id}."""

    @pytest.fixture(autouse=True)
    def _patch_admin(self):
        with patch("sandcastle.api.routes._require_admin"):
            yield

    @pytest.fixture(autouse=True)
    def _patch_tenant(self):
        with patch("sandcastle.api.routes.get_tenant_id", return_value=None):
            yield

    def _make_mock_session(self, events=None, total=0):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = events or []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.scalar = AsyncMock(return_value=total)
        return mock_session

    def test_get_audit_no_filters_returns_200(self):
        client = _make_test_client()
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get("/audit")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)

    def test_get_audit_filter_by_event_type(self):
        client = _make_test_client()
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get("/audit?event_type=run.started")
        assert resp.status_code == 200

    def test_get_audit_filter_by_date_range(self):
        client = _make_test_client()
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get(
                "/audit?since=2026-01-01T00:00:00&until=2026-12-31T23:59:59"
            )
        assert resp.status_code == 200

    def test_get_audit_filter_by_actor_id(self):
        client = _make_test_client()
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get("/audit?actor_id=admin-user")
        assert resp.status_code == 200

    def test_get_audit_invalid_run_id_400(self):
        client = _make_test_client()
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get("/audit?run_id=not-a-uuid")
        assert resp.status_code == 400

    def test_get_audit_invalid_since_400(self):
        client = _make_test_client()
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get("/audit?since=not-a-date")
        assert resp.status_code == 400

    def test_get_audit_invalid_until_400(self):
        client = _make_test_client()
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get("/audit?until=not-a-date")
        assert resp.status_code == 400

    def test_get_audit_verify_valid_chain(self):
        client = _make_test_client()
        run_id = str(uuid.uuid4())
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            with patch(
                "sandcastle.api.routes.verify_audit_chain",
                new_callable=AsyncMock,
                return_value=(True, 10, None),
            ):
                resp = client.get(f"/audit/verify/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["valid"] is True
        assert body["data"]["chain_length"] == 10
        assert body["data"]["broken_at"] is None

    def test_get_audit_verify_invalid_uuid_400(self):
        client = _make_test_client()
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get("/audit/verify/not-a-uuid")
        assert resp.status_code == 400

    def test_get_audit_verify_broken_chain_returns_invalid(self):
        client = _make_test_client()
        run_id = str(uuid.uuid4())
        broken_event = {
            "id": str(uuid.uuid4()),
            "event_type": "run.started",
            "run_id": run_id,
            "actor_id": "system",
            "actor_key_prefix": None,
            "source_ip": None,
            "payload": {},
            "prev_hash": "tampered",
            "entry_hash": "a" * 64,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        mock_session = self._make_mock_session()
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            with patch(
                "sandcastle.api.routes.verify_audit_chain",
                new_callable=AsyncMock,
                return_value=(False, 5, broken_event),
            ):
                resp = client.get(f"/audit/verify/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["valid"] is False
        assert body["data"]["broken_at"] is not None

    def test_get_audit_pagination_meta(self):
        client = _make_test_client()
        mock_session = self._make_mock_session(total=42)
        with patch("sandcastle.api.routes.async_session", return_value=mock_session):
            resp = client.get("/audit?limit=10&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert "meta" in body
        assert body["meta"]["total"] == 42
        assert body["meta"]["limit"] == 10


# ---------------------------------------------------------------------------
# 9. Hash determinism
# ---------------------------------------------------------------------------


class TestHashDeterminism:
    def test_same_payload_different_key_order_same_hash(self):
        """sort_keys=True ensures key order doesn't affect the hash."""
        h1 = compute_audit_hash(
            "run.started",
            "run-abc",
            "system",
            "2026-01-01T00:00:00+00:00",
            {"z": 26, "a": 1, "m": 13},
            "genesis",
        )
        h2 = compute_audit_hash(
            "run.started",
            "run-abc",
            "system",
            "2026-01-01T00:00:00+00:00",
            {"m": 13, "z": 26, "a": 1},
            "genesis",
        )
        assert h1 == h2

    def test_different_whitespace_in_string_values_differs(self):
        """Different whitespace inside string values IS a real difference."""
        h1 = compute_audit_hash(
            "x", None, "y", "t", {"k": "hello world"}, "genesis"
        )
        h2 = compute_audit_hash(
            "x", None, "y", "t", {"k": "hello  world"}, "genesis"
        )
        assert h1 != h2

    def test_none_run_id_equals_empty_string_run_id(self):
        """run_id=None and run_id='' produce the same hash (canonical: '')."""
        h1 = compute_audit_hash("x", None, "y", "t", {}, "genesis")
        h2 = compute_audit_hash("x", "", "y", "t", {}, "genesis")
        assert h1 == h2

    def test_idempotent_across_calls(self):
        """Same inputs always produce same hash across multiple calls."""
        kwargs = dict(
            event_type="step.completed",
            run_id="some-run",
            actor_id="actor",
            timestamp="2026-06-01T12:00:00+00:00",
            payload={"result": "ok", "cost": 0.001},
            prev_hash="a" * 64,
        )
        hashes = [compute_audit_hash(**kwargs) for _ in range(10)]
        assert len(set(hashes)) == 1, "Hash is not idempotent"

    def test_hash_is_sha256_hex(self):
        """Hash must be exactly 64 lowercase hex characters."""
        h = compute_audit_hash("e", "r", "a", "t", {"k": "v"}, "p")
        assert len(h) == 64
        assert h == h.lower()
        assert all(c in "0123456789abcdef" for c in h)

    def test_nested_dict_key_order_independent(self):
        """Nested dict key order must also not affect the hash."""
        payload_a = {"outer": {"b": 2, "a": 1}, "top": "x"}
        payload_b = {"top": "x", "outer": {"a": 1, "b": 2}}
        h1 = compute_audit_hash("e", "r", "a", "t", payload_a, "p")
        h2 = compute_audit_hash("e", "r", "a", "t", payload_b, "p")
        assert h1 == h2


# ---------------------------------------------------------------------------
# 10. Performance - 1000 events in under 5 seconds
# ---------------------------------------------------------------------------


class TestPerformance:
    @pytest.mark.asyncio
    async def test_1000_events_append_and_verify_under_5_seconds(self):
        """1000 sequential events for one run, full chain verify, must finish < 5s."""
        engine, Session, init = _make_engine_and_session()
        await init()
        run_id = str(uuid.uuid4())

        start = time.monotonic()

        async with Session() as session:
            for i in range(1000):
                await append_audit_event(
                    session=session,
                    event_type=f"perf.event.{i % 10}",
                    run_id=run_id,
                    actor_id="perf-actor",
                    payload={"index": i, "data": "x" * 100},
                )
                if i % 100 == 99:
                    await session.flush()
            await session.commit()

        async with Session() as session:
            valid, length, broken = await verify_audit_chain(session, run_id)

        elapsed = time.monotonic() - start

        assert valid is True, f"Chain invalid after 1000 events: broken={broken}"
        assert length == 1000
        assert elapsed < 5.0, f"Performance test took {elapsed:.2f}s (limit: 5.0s)"

    @pytest.mark.asyncio
    async def test_compute_hash_speed_1000_iterations(self):
        """compute_audit_hash must handle 1000 calls in under 1 second."""
        start = time.monotonic()
        for i in range(1000):
            compute_audit_hash(
                event_type="perf.hash",
                run_id=str(uuid.uuid4()),
                actor_id="actor",
                timestamp="2026-01-01T00:00:00+00:00",
                payload={"i": i, "data": "x" * 200},
                prev_hash="a" * 64,
            )
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"1000 hash computations took {elapsed:.3f}s"
