"""Tamper-evident audit trail with SHA-256 hash chain."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_SQLITE_SCOPE_LOCK_STATE = "_sandcastle_audit_scope_locks"
_sqlite_scope_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _audit_scope(run_id: uuid.UUID | None) -> str:
    """Return the lock scope for a run-specific or global audit chain."""
    return f"run:{run_id}" if run_id is not None else "global"


async def _lock_audit_scope(session: "AsyncSession", scope: str) -> bool:
    """Serialize appends for an audit chain until the session transaction ends.

    PostgreSQL's transaction-scoped advisory lock protects the chain across
    application processes. SQLite is single-writer in the supported local
    deployment, so an in-process lock is sufficient there; retain it until the
    outer transaction ends so a caller's later ``commit()`` remains covered.

    Returns whether this call acquired a new SQLite lock. PostgreSQL locks are
    released by the database at transaction end.
    """
    from sqlalchemy import event, text

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
            {"scope": scope},
        )
        return False

    if dialect_name != "sqlite":
        return False

    sync_session = session.sync_session
    state = sync_session.info.get(_SQLITE_SCOPE_LOCK_STATE)
    if state is None:
        locks: dict[str, asyncio.Lock] = {}

        def release_locks(_session, transaction) -> None:
            if transaction.parent is not None:
                return
            for held_lock in locks.values():
                if held_lock.locked():
                    held_lock.release()
            locks.clear()

        state = locks
        sync_session.info[_SQLITE_SCOPE_LOCK_STATE] = state
        event.listen(sync_session, "after_transaction_end", release_locks)

    if scope in state:
        return False

    loop_key = id(asyncio.get_running_loop())
    lock = _sqlite_scope_locks.setdefault((loop_key, scope), asyncio.Lock())
    await lock.acquire()
    state[scope] = lock
    return True


def _unlock_failed_sqlite_scope(session: "AsyncSession", scope: str) -> None:
    """Release a newly acquired SQLite scope lock after a failed append."""
    state = session.sync_session.info.get(_SQLITE_SCOPE_LOCK_STATE)
    if not state:
        return
    lock = state.pop(scope, None)
    if lock is not None and lock.locked():
        lock.release()


def compute_audit_hash(
    event_type: str,
    run_id: str | None,
    actor_id: str,
    timestamp: str,
    payload: dict,
    prev_hash: str,
) -> str:
    """Compute SHA-256 hash for an audit chain entry.

    The canonical JSON is deterministic (sorted keys, compact separators)
    so the same inputs always produce the same hash regardless of platform.
    """
    canonical = json.dumps(
        {
            "event_type": event_type,
            "run_id": run_id or "",
            "actor_id": actor_id,
            "timestamp": timestamp,
            "payload": payload,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def append_audit_event(
    session: AsyncSession,
    event_type: str,
    run_id: str | None,
    actor_id: str,
    payload: dict,
    actor_key_prefix: str | None = None,
    source_ip: str | None = None,
) -> "AuditEvent | None":  # noqa: F821
    """Append a new audit event, chaining it to the previous event for this run.

    For system-level events (run_id=None), the chain is global across all
    system events. For run-scoped events, the chain is scoped to the run.

    Returns the persisted AuditEvent, or None if persistence failed.
    """
    from sqlalchemy import select

    from sandcastle.models.db import AuditEvent

    run_uuid_val: uuid.UUID | None = None
    if run_id is not None:
        try:
            run_uuid_val = uuid.UUID(str(run_id))
        except ValueError:
            pass

    scope = _audit_scope(run_uuid_val)
    acquired_sqlite_lock = False
    try:
        acquired_sqlite_lock = await _lock_audit_scope(session, scope)
        now = datetime.now(timezone.utc)

        # Determine prev_hash: most recent entry for this run (or system chain)
        if run_uuid_val is not None:
            prev_stmt = (
                select(AuditEvent.entry_hash, AuditEvent.created_at)
                .where(AuditEvent.run_id == run_uuid_val)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(1)
            )
        else:
            prev_stmt = (
                select(AuditEvent.entry_hash, AuditEvent.created_at)
                .where(AuditEvent.run_id.is_(None))
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(1)
            )

        prev_result = await session.execute(prev_stmt)
        previous_event = prev_result.one_or_none()
        prev_hash = previous_event[0] if previous_event is not None else "genesis"
        if previous_event is not None and previous_event[1] is not None:
            previous_created_at = previous_event[1]
            if previous_created_at.tzinfo is None:
                previous_created_at = previous_created_at.replace(tzinfo=timezone.utc)
            # The chain order is timestamp-first. Ensure sequentially locked
            # appends have an unambiguous order even on coarse system clocks.
            if now <= previous_created_at:
                now = previous_created_at + timedelta(microseconds=1)
        timestamp = now.isoformat()

        run_id_str = str(run_uuid_val) if run_uuid_val is not None else None
        entry_hash = compute_audit_hash(
            event_type=event_type,
            run_id=run_id_str,
            actor_id=actor_id,
            timestamp=timestamp,
            payload=payload or {},
            prev_hash=prev_hash,
        )

        event = AuditEvent(
            event_type=event_type,
            run_id=run_uuid_val,
            actor_id=actor_id,
            actor_key_prefix=actor_key_prefix,
            source_ip=source_ip,
            payload=payload or {},
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            created_at=now,
        )
        # A failed audit insert must not leave the caller's enclosing
        # transaction in a failed state. The savepoint rolls back only this
        # best-effort audit event.
        async with session.begin_nested():
            session.add(event)
            await session.flush()  # assign id without committing the outer transaction
        return event

    except Exception as exc:
        if acquired_sqlite_lock:
            _unlock_failed_sqlite_scope(session, scope)
        logger.warning("Failed to append audit event '%s': %s", event_type, exc)
        return None


async def verify_audit_chain(
    session: AsyncSession,
    run_id: str | None,
) -> tuple[bool, int, dict | None]:
    """Verify the integrity of the audit chain for a run (or the system chain).

    Recomputes each entry's hash and checks it matches entry_hash and that
    prev_hash correctly references the previous entry.

    Returns:
        (valid, chain_length, first_broken_event)

    first_broken_event is a dict with the AuditEvent fields if tampering was
    detected, or None when the chain is intact.
    """
    from sqlalchemy import select

    from sandcastle.models.db import AuditEvent

    if run_id is not None:
        try:
            run_uuid = uuid.UUID(run_id)
        except ValueError:
            return False, 0, {"error": "Invalid run_id format"}

        stmt = (
            select(AuditEvent)
            .where(AuditEvent.run_id == run_uuid)
            .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        )
    else:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.run_id.is_(None))
            .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        )

    result = await session.execute(stmt)
    events = result.scalars().all()

    if not events:
        return True, 0, None

    expected_prev = "genesis"
    for idx, ev in enumerate(events):
        # Verify prev_hash
        if ev.prev_hash != expected_prev:
            return False, len(events), _event_to_dict(ev)

        # Recompute entry_hash.
        # SQLite stores datetimes without timezone info; normalise to UTC-aware
        # so the ISO string matches what was used during append_audit_event.
        ts = ev.created_at
        if ts is not None:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            timestamp = ts.isoformat()
        else:
            timestamp = ""

        computed = compute_audit_hash(
            event_type=ev.event_type,
            run_id=str(ev.run_id) if ev.run_id else None,
            actor_id=ev.actor_id,
            timestamp=timestamp,
            payload=ev.payload or {},
            prev_hash=ev.prev_hash,
        )
        if computed != ev.entry_hash:
            return False, len(events), _event_to_dict(ev)

        expected_prev = ev.entry_hash

    return True, len(events), None


def _event_to_dict(event) -> dict:
    """Serialize an AuditEvent to a plain dict for API responses."""
    return {
        "id": str(event.id),
        "event_type": event.event_type,
        "run_id": str(event.run_id) if event.run_id else None,
        "actor_id": event.actor_id,
        "actor_key_prefix": event.actor_key_prefix,
        "source_ip": event.source_ip,
        "payload": event.payload,
        "prev_hash": event.prev_hash,
        "entry_hash": event.entry_hash,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
