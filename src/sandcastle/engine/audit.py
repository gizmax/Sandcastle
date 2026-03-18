"""Tamper-evident audit trail with SHA-256 hash chain."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


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
    system events (ordered by created_at). For run-scoped events, the chain
    is scoped to the run.

    Returns the persisted AuditEvent, or None if persistence failed.
    """
    from sqlalchemy import select

    from sandcastle.models.db import AuditEvent

    try:
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()

        # Determine prev_hash: most recent entry for this run (or system chain)
        if run_id is not None:
            try:
                run_uuid = uuid.UUID(run_id)
            except ValueError:
                run_uuid = None

            if run_uuid is not None:
                prev_stmt = (
                    select(AuditEvent.entry_hash)
                    .where(AuditEvent.run_id == run_uuid)
                    .order_by(AuditEvent.created_at.desc())
                    .limit(1)
                )
            else:
                prev_stmt = (
                    select(AuditEvent.entry_hash)
                    .where(AuditEvent.run_id.is_(None))
                    .order_by(AuditEvent.created_at.desc())
                    .limit(1)
                )
        else:
            prev_stmt = (
                select(AuditEvent.entry_hash)
                .where(AuditEvent.run_id.is_(None))
                .order_by(AuditEvent.created_at.desc())
                .limit(1)
            )

        prev_result = await session.execute(prev_stmt)
        prev_hash_row = prev_result.scalar_one_or_none()
        prev_hash = prev_hash_row if prev_hash_row is not None else "genesis"

        run_id_str = str(run_id) if run_id is not None else None
        entry_hash = compute_audit_hash(
            event_type=event_type,
            run_id=run_id_str,
            actor_id=actor_id,
            timestamp=timestamp,
            payload=payload or {},
            prev_hash=prev_hash,
        )

        run_uuid_val: uuid.UUID | None = None
        if run_id is not None:
            try:
                run_uuid_val = uuid.UUID(str(run_id))
            except ValueError:
                run_uuid_val = None

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
        session.add(event)
        await session.flush()  # assign id without committing the outer transaction
        return event

    except Exception as exc:
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
            .order_by(AuditEvent.created_at.asc())
        )
    else:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.run_id.is_(None))
            .order_by(AuditEvent.created_at.asc())
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
