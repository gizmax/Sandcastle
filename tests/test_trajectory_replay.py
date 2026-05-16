"""Tests for the trajectory replay primitives."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sandcastle.engine.trajectory_replay import (
    ToolCall,
    Trajectory,
    compute_trajectory_checksum,
    diff_trajectories,
    extract_trajectory,
    replay_score,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tc(
    step_id: str,
    tool_name: str = "search",
    args: dict | None = None,
    output: dict | str | None = None,
    error: str | None = None,
    duration_ms: int = 100,
    ts: datetime | None = None,
) -> ToolCall:
    return ToolCall(
        step_id=step_id,
        tool_name=tool_name,
        args=args if args is not None else {"q": "hi"},
        output=output if output is not None else {"ok": True},
        error=error,
        duration_ms=duration_ms,
        ts=ts or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _traj(tool_calls: list[ToolCall], final_output: dict | None = None) -> Trajectory:
    t = Trajectory(
        run_id="run_test",
        workflow_name="demo",
        version=1,
        tool_calls=tool_calls,
        total_cost_usd=sum(0.001 for _ in tool_calls),
        total_duration_ms=sum(tc.duration_ms for tc in tool_calls),
        final_output=final_output if final_output is not None else {"answer": 42},
    )
    t.checksum = compute_trajectory_checksum(t)
    return t


# ---------------------------------------------------------------------------
# compute_trajectory_checksum
# ---------------------------------------------------------------------------


def test_checksum_is_deterministic_for_same_input():
    t1 = _traj([_tc("a"), _tc("b", tool_name="fetch")])
    t2 = _traj([_tc("a"), _tc("b", tool_name="fetch")])
    assert t1.checksum == t2.checksum
    # Re-compute should also be stable.
    assert compute_trajectory_checksum(t1) == compute_trajectory_checksum(t2)
    # SHA-256 hex is 64 chars.
    assert len(t1.checksum) == 64


def test_checksum_changes_when_tool_call_order_changes():
    a = _tc("a", tool_name="search")
    b = _tc("b", tool_name="fetch")
    forward = _traj([a, b])
    reverse = _traj([b, a])
    assert forward.checksum != reverse.checksum


# ---------------------------------------------------------------------------
# extract_trajectory
# ---------------------------------------------------------------------------


def test_extract_trajectory_builds_correct_object():
    t0 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    audit = [
        {"event_type": "step.started", "step_id": "s1", "ts": t0},
        {
            "event_type": "step.completed",
            "step_id": "s1",
            "ts": t0 + timedelta(milliseconds=250),
        },
        {
            "event_type": "step.started",
            "step_id": "s2",
            "ts": t0 + timedelta(milliseconds=300),
        },
        {
            "event_type": "step.completed",
            "step_id": "s2",
            "ts": t0 + timedelta(milliseconds=800),
        },
    ]
    steps = [
        {
            "step_id": "s1",
            "tool_name": "search",
            "args": {"q": "hello"},
            "output": {"hits": 3},
            "error": None,
            "cost_usd": 0.002,
            "workflow_name": "demo_flow",
            "version": 7,
        },
        {
            "step_id": "s2",
            "tool_name": "summarize",
            "args": {"text": "..."},
            "output": "done",
            "error": None,
            "cost_usd": 0.003,
            "final_output": {"answer": "all good"},
        },
    ]

    traj = extract_trajectory("run_xyz", audit, steps)

    assert traj.run_id == "run_xyz"
    assert traj.workflow_name == "demo_flow"
    assert traj.version == 7
    assert [tc.step_id for tc in traj.tool_calls] == ["s1", "s2"]
    assert traj.tool_calls[0].duration_ms == 250
    assert traj.tool_calls[1].duration_ms == 500
    assert traj.total_duration_ms == 750
    assert traj.total_cost_usd == pytest.approx(0.005)
    assert traj.final_output == {"answer": "all good"}
    assert len(traj.checksum) == 64


def test_extract_trajectory_uses_audit_order_not_step_order():
    t0 = datetime(2026, 5, 1, 9, 0, 0, tzinfo=timezone.utc)
    audit = [
        {"event_type": "step.started", "step_id": "second", "ts": t0},
        {
            "event_type": "step.completed",
            "step_id": "second",
            "ts": t0 + timedelta(milliseconds=100),
        },
        {
            "event_type": "step.started",
            "step_id": "first",
            "ts": t0 + timedelta(milliseconds=200),
        },
        {
            "event_type": "step.completed",
            "step_id": "first",
            "ts": t0 + timedelta(milliseconds=300),
        },
    ]
    steps = [
        {"step_id": "first", "tool_name": "a", "args": {}, "output": {}, "cost_usd": 0},
        {"step_id": "second", "tool_name": "b", "args": {}, "output": {}, "cost_usd": 0},
    ]
    traj = extract_trajectory("run_2", audit, steps)
    assert [tc.step_id for tc in traj.tool_calls] == ["second", "first"]


# ---------------------------------------------------------------------------
# diff_trajectories
# ---------------------------------------------------------------------------


def test_diff_detects_added_tool_call():
    golden = _traj([_tc("a")])
    candidate = _traj([_tc("a"), _tc("b", tool_name="fetch")])
    diff = diff_trajectories(golden, candidate)
    kinds = [d.kind for d in diff.tool_call_diffs]
    assert "added" in kinds
    added = [d for d in diff.tool_call_diffs if d.kind == "added"][0]
    assert added.step_id == "b"
    assert added.golden is None
    assert added.candidate is not None


def test_diff_detects_removed_tool_call():
    golden = _traj([_tc("a"), _tc("b", tool_name="fetch")])
    candidate = _traj([_tc("a")])
    diff = diff_trajectories(golden, candidate)
    removed = [d for d in diff.tool_call_diffs if d.kind == "removed"]
    assert len(removed) == 1
    assert removed[0].step_id == "b"


def test_diff_detects_args_changed():
    golden = _traj([_tc("a", args={"q": "alpha"})])
    candidate = _traj([_tc("a", args={"q": "beta"})])
    diff = diff_trajectories(golden, candidate)
    kinds = [d.kind for d in diff.tool_call_diffs]
    assert "args_changed" in kinds


def test_diff_detects_output_changed():
    golden = _traj([_tc("a", output={"hits": 1})])
    candidate = _traj([_tc("a", output={"hits": 99})])
    diff = diff_trajectories(golden, candidate)
    kinds = [d.kind for d in diff.tool_call_diffs]
    assert "output_changed" in kinds


def test_diff_detects_order_changed_when_same_set():
    a = _tc("a", tool_name="search")
    b = _tc("b", tool_name="fetch")
    golden = _traj([a, b])
    candidate = _traj([b, a])
    diff = diff_trajectories(golden, candidate)
    kinds = [d.kind for d in diff.tool_call_diffs]
    assert "order_changed" in kinds
    # Same set, no added/removed.
    assert "added" not in kinds
    assert "removed" not in kinds


def test_diff_zero_when_identical():
    a = _tc("a", tool_name="search", args={"q": "x"}, output={"ok": True})
    b = _tc("b", tool_name="fetch", args={"u": "/y"}, output={"data": [1]})
    golden = _traj([a, b])
    candidate = _traj([a, b])
    diff = diff_trajectories(golden, candidate)
    assert diff.tool_call_diffs == []
    assert diff.cost_delta_usd == pytest.approx(0.0)
    assert diff.duration_delta_ms == 0
    assert diff.final_output_match is True
    assert "0 tool-call diff" in diff.summary


# ---------------------------------------------------------------------------
# replay_score
# ---------------------------------------------------------------------------


def test_replay_score_returns_one_on_identical():
    a = _tc("a")
    b = _tc("b", tool_name="fetch")
    golden = _traj([a, b])
    candidate = _traj([a, b])
    diff = diff_trajectories(golden, candidate)
    assert replay_score(diff) == pytest.approx(1.0)


def test_replay_score_drops_when_output_mismatches():
    golden = _traj([_tc("a", output={"ok": True})], final_output={"answer": 1})
    candidate = _traj(
        [_tc("a", output={"ok": False})],
        final_output={"answer": 2},
    )
    diff = diff_trajectories(golden, candidate)
    score = replay_score(diff)
    assert 0.0 <= score < 1.0
    # With defaults: output_changed -> tool_match = 0.5, final mismatch
    # -> 0.0, cost within budget -> 1.0. Score = 0.6*0.5 + 0.3*0 + 0.1*1
    # = 0.4.
    assert score == pytest.approx(0.4, abs=1e-6)


def test_replay_score_respects_custom_weights():
    golden = _traj([_tc("a")], final_output={"answer": 1})
    candidate = _traj([_tc("a")], final_output={"answer": 2})
    diff = diff_trajectories(golden, candidate)
    # Make final_output dominate, drop tool_match weight.
    score = replay_score(
        diff,
        weights={"tool_match": 0.0, "final_output": 1.0, "cost": 0.0},
    )
    # Final output mismatches -> 0 with this weighting.
    assert score == pytest.approx(0.0)

    # Flip the weighting: tool calls match perfectly, ignore final
    # output entirely -> score should be 1.0.
    score_flipped = replay_score(
        diff,
        weights={"tool_match": 1.0, "final_output": 0.0, "cost": 0.0},
    )
    assert score_flipped == pytest.approx(1.0)
