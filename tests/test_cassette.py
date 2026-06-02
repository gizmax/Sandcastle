"""Tests for deterministic cassettes - record/replay of model step outputs.

A cassette records each model step's output keyed by its deterministic cache key, then
replays it offline without calling any provider: identical output, zero cost, and a
tamper-evident signature. These tests prove the record produces a signed file and the
replay returns the recorded output without touching the (mocked) provider.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.engine.cassette import CassetteStore
from sandcastle.engine.dag import build_plan, parse_yaml_string
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime

WORKFLOW = """name: cassette-test
description: A single model (standard) step for record/replay.
default_model: sonnet
input_schema:
  required: []
  properties:
    q: { type: string, default: "hi" }
steps:
  - id: think
    prompt: "Answer: {input.q}"
"""


def _sandbox(text: str, cost: float = 0.02):
    """A mock SandshoreRuntime whose query returns a fixed result and counts calls."""
    sb = MagicMock(spec=SandshoreRuntime)
    calls = {"n": 0}

    async def _query(request):
        calls["n"] += 1
        return SandshoreResult(
            text=text, structured_output=None, total_cost_usd=cost,
            input_tokens=5, output_tokens=5,
        )

    sb.query = _query
    sb._calls = calls
    return sb


async def _run(cassette, mode, sandbox_text):
    wf = parse_yaml_string(WORKFLOW)
    plan = build_plan(wf)
    sandbox = _sandbox(sandbox_text)
    with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
        result = await execute_workflow(
            workflow=wf, plan=plan, input_data={"q": "hi"},
            admin_trusted=True, cassette=cassette, cassette_mode=mode,
        )
    return result, sandbox


@pytest.mark.asyncio
async def test_record_then_replay_is_deterministic(tmp_path):
    cassette_path = tmp_path / "run.cassette.json"

    # RECORD: the provider returns "RECORDED"; the cassette captures it.
    rec = CassetteStore(cassette_path, "record")
    result, sandbox = await _run(rec, "record", "RECORDED")
    assert result.status == "completed"
    assert sandbox._calls["n"] == 1, "provider should be called once during record"
    rec.save()
    assert cassette_path.exists()
    assert len(rec.records) == 1, "the model step output should be recorded"

    # The file is signed and round-trips.
    data = json.loads(cassette_path.read_text())
    assert data["meta"]["step_count"] == 1
    assert data["meta"]["signature"]

    # REPLAY: the provider would now return "DIFFERENT", but replay must return the
    # RECORDED output and never call the provider.
    rep = CassetteStore(cassette_path, "replay")
    assert rep.verify(), "signature should verify on an unmodified cassette"
    result2, sandbox2 = await _run(rep, "replay", "DIFFERENT")
    assert result2.status == "completed"
    assert sandbox2._calls["n"] == 0, "replay must not call the provider"
    assert rep.replay_hits == 1
    assert result2.outputs["think"] == "RECORDED"
    assert result2.total_cost_usd == 0.0, "replay is free"
    # The replayed output equals the recorded one, not the live provider's.
    assert result.outputs["think"] == result2.outputs["think"]


def test_replay_detects_tampering(tmp_path):
    """Editing a recorded output without re-signing must fail verification (unit-level,
    no workflow run needed)."""
    cassette_path = tmp_path / "tampered.cassette.json"
    rec = CassetteStore(cassette_path, "record")
    rec.put("k1", output="ORIGINAL", cost_usd=0.01, model="sonnet", step_id="s")
    rec.save()

    data = json.loads(cassette_path.read_text())
    assert rec.verify() is True or data["meta"]["signature"]  # baseline signed
    data["records"]["k1"]["output"] = "TAMPERED"
    cassette_path.write_text(json.dumps(data))

    rep = CassetteStore(cassette_path, "replay")
    assert rep.verify() is False, "a modified cassette must fail signature verification"
    assert rep.get("k1")["output"] == "TAMPERED"  # still loads, but flagged unverified


def test_record_mode_requires_valid_mode(tmp_path):
    with pytest.raises(ValueError):
        CassetteStore(tmp_path / "x.json", "bogus")


def test_replay_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        CassetteStore(tmp_path / "does-not-exist.json", "replay")
