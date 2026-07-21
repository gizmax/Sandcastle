"""json_safe must make any workflow output persistable to JSON columns."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from sandcastle.engine.json_utils import json_safe


def test_json_safe_passes_plain_structures_unchanged():
    payload = {"a": 1, "b": [1, 2, {"c": "x"}], "d": None}
    assert json_safe(payload) == payload


def test_json_safe_coerces_non_native_types():
    run_id = uuid.uuid4()
    ts = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    result = json_safe({"run_id": run_id, "ts": ts, "items": (1, 2)})
    assert result["run_id"] == str(run_id)
    assert result["ts"] == str(ts)
    assert result["items"] == [1, 2]


def test_json_safe_degrades_arbitrary_objects_to_strings():
    result = json_safe({"_token_report": AsyncMock(), "status": "completed"})
    assert isinstance(result["_token_report"], str)
    assert result["status"] == "completed"


def test_json_safe_output_persists_to_run_row():
    """End-to-end: outputs with non-native values survive the DB round trip."""
    import asyncio

    from sandcastle.models.db import Run, async_session

    async def scenario():
        rid = uuid.uuid4()
        async with async_session() as session:
            session.add(Run(id=rid, workflow_name="json-safe", input_data={}))
            await session.commit()
        async with async_session() as session:
            run = await session.get(Run, rid)
            run.output_data = json_safe({"when": datetime.now(timezone.utc), "id": rid})
            await session.commit()
        async with async_session() as session:
            reloaded = await session.get(Run, rid)
            assert reloaded.output_data["id"] == str(rid)
            assert isinstance(reloaded.output_data["when"], str)

    asyncio.run(scenario())
