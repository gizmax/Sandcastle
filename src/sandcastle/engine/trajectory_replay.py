"""Trajectory Replay step type primitives (v0.32 prep).

This module provides pure-Python primitives for capturing, checksumming,
diffing, and scoring agent trajectories. A trajectory is the ordered
sequence of tool calls produced during a workflow run, plus its final
output, total cost, and total duration.

Trajectory-level evaluation grades the tool-call sequence (the "how"),
not just the final output (the "what"). Combined with Sandcastle's
SHA-256 audit chain, this enables cryptographically verifiable golden
trajectories suitable for regression gating and Annex IV transparency.

YAML step type specification (Phase 3 wiring is intentionally deferred,
this module does NOT register the step type in dag.py):

    - id: replay_check
      type: trajectory-replay
      trajectory_replay_config:
        golden_run_id: run_2026_05_01_abc123
        fail_below_score: 0.8       # 0..1, defaults to 0.8
        allow_cost_delta_pct: 10.0  # percent, defaults to 10.0

Expected runtime behaviour (for Phase 3 implementer):
  1. Load the golden Trajectory by run_id.
  2. Extract the candidate Trajectory from the current run's audit
     events and step records using ``extract_trajectory``.
  3. Compute the diff via ``diff_trajectories`` and the score via
     ``replay_score``.
  4. Fail the step when ``score < fail_below_score`` or when
     ``abs(cost_delta_usd) / golden.total_cost_usd * 100`` exceeds
     ``allow_cost_delta_pct``.

This module is pure: no DB, no HTTP, no provider SDKs. Only stdlib.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

ToolCallDiffKind = Literal[
    "added",
    "removed",
    "args_changed",
    "output_changed",
    "order_changed",
]


@dataclass
class ToolCall:
    """A single tool invocation within a trajectory."""

    step_id: str
    tool_name: str
    args: dict[str, Any]
    output: dict[str, Any] | str
    error: str | None
    duration_ms: int
    ts: datetime


@dataclass
class Trajectory:
    """An ordered sequence of tool calls plus final output and totals."""

    run_id: str
    workflow_name: str
    version: int
    tool_calls: list[ToolCall]
    total_cost_usd: float
    total_duration_ms: int
    final_output: dict[str, Any]
    checksum: str = ""


@dataclass
class ToolCallDiff:
    """One difference between two trajectories at the tool-call level."""

    kind: ToolCallDiffKind
    step_id: str
    golden: ToolCall | None
    candidate: ToolCall | None
    details: str


@dataclass
class TrajectoryDiff:
    """Structured diff between a golden and candidate trajectory."""

    tool_call_diffs: list[ToolCallDiff] = field(default_factory=list)
    cost_delta_usd: float = 0.0
    duration_delta_ms: int = 0
    final_output_match: bool = True
    summary: str = ""


# ---------------------------------------------------------------------------
# Canonical serialisation + checksum
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> Any:
    """Convert dataclasses, datetimes, and dicts into a JSON-safe form.

    Datetimes are serialised as ISO-8601 strings to keep checksums stable
    across host timezones (we serialise via ``isoformat`` which preserves
    any tzinfo attached to the datetime).
    """

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _canonical(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return _canonical(asdict(value))
    return value


def compute_trajectory_checksum(trajectory: Trajectory) -> str:
    """Return a deterministic SHA-256 hex digest for a trajectory.

    The checksum covers the canonical-JSON form of ``tool_calls`` and
    ``final_output`` (order-sensitive for tool_calls, key-sorted for
    dicts). Other fields like ``run_id`` are intentionally excluded so
    that the same logical trajectory replayed under a different run id
    still hashes the same.
    """

    payload = {
        "tool_calls": _canonical(trajectory.tool_calls),
        "final_output": _canonical(trajectory.final_output),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# Extraction from audit events + step records
# ---------------------------------------------------------------------------


def _parse_ts(value: Any) -> datetime:
    """Accept datetime or ISO-8601 string. Falls back to epoch on junk."""

    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.fromtimestamp(0)


def extract_trajectory(
    run_id: str,
    audit_events: list[dict[str, Any]],
    run_steps: list[dict[str, Any]],
) -> Trajectory:
    """Build a Trajectory from a run's audit events and step records.

    Audit events are expected to contain entries of the shape::

        {"event_type": "step.started"  | "step.completed" | "step.failed",
         "step_id": str, "ts": datetime | iso-str, "data": {...}}

    Run steps provide the tool-call payload::

        {"step_id": str, "tool_name": str, "args": dict,
         "output": dict | str, "error": str | None, "cost_usd": float,
         "workflow_name": str (optional), "version": int (optional),
         "final_output": dict (optional, set on the terminal step)}

    Ordering is taken from ``audit_events`` (step.started timestamps).
    Duration is computed as ``completed.ts - started.ts`` in milliseconds
    when both events are present, otherwise it falls back to the step
    record's ``duration_ms`` field, otherwise zero.

    This function is pure: it does not read or write any persistent
    store.
    """

    steps_by_id: dict[str, dict[str, Any]] = {s["step_id"]: s for s in run_steps}

    started: dict[str, datetime] = {}
    completed: dict[str, datetime] = {}
    order: list[str] = []
    for ev in audit_events:
        etype = ev.get("event_type", "")
        sid = ev.get("step_id")
        if not sid:
            continue
        ts = _parse_ts(ev.get("ts"))
        if etype == "step.started":
            if sid not in started:
                started[sid] = ts
                order.append(sid)
        elif etype in {"step.completed", "step.failed"}:
            completed[sid] = ts

    tool_calls: list[ToolCall] = []
    for sid in order:
        step = steps_by_id.get(sid)
        if step is None:
            continue
        ts = started.get(sid, _parse_ts(step.get("ts")))
        if sid in completed:
            duration_ms = int(
                max(0.0, (completed[sid] - started[sid]).total_seconds() * 1000)
            )
        else:
            duration_ms = int(step.get("duration_ms", 0))
        tool_calls.append(
            ToolCall(
                step_id=sid,
                tool_name=step.get("tool_name", ""),
                args=dict(step.get("args", {})),
                output=step.get("output", {}),
                error=step.get("error"),
                duration_ms=duration_ms,
                ts=ts,
            )
        )

    total_cost = sum(float(s.get("cost_usd", 0.0) or 0.0) for s in run_steps)
    total_duration = sum(tc.duration_ms for tc in tool_calls)

    workflow_name = ""
    version = 0
    final_output: dict[str, Any] = {}
    for s in run_steps:
        if "workflow_name" in s and not workflow_name:
            workflow_name = str(s["workflow_name"])
        if "version" in s and not version:
            try:
                version = int(s["version"])
            except (TypeError, ValueError):
                version = 0
        if "final_output" in s and s["final_output"] is not None:
            final_output = dict(s["final_output"])

    trajectory = Trajectory(
        run_id=run_id,
        workflow_name=workflow_name,
        version=version,
        tool_calls=tool_calls,
        total_cost_usd=round(total_cost, 10),
        total_duration_ms=total_duration,
        final_output=final_output,
    )
    trajectory.checksum = compute_trajectory_checksum(trajectory)
    return trajectory


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def _key(tc: ToolCall) -> tuple[str, str]:
    return (tc.step_id, tc.tool_name)


def diff_trajectories(
    golden: Trajectory,
    candidate: Trajectory,
) -> TrajectoryDiff:
    """Diff two trajectories at the tool-call level.

    The diff reports five kinds of changes:

    * ``added``         - a tool call present in ``candidate`` only.
    * ``removed``       - a tool call present in ``golden`` only.
    * ``args_changed``  - same step_id + tool_name, different ``args``.
    * ``output_changed``- same step_id + tool_name, different ``output``
                          or ``error``.
    * ``order_changed`` - both trajectories share the same set of
                          (step_id, tool_name) pairs but their ordering
                          differs.

    ``final_output_match`` compares the two ``final_output`` dicts via
    canonical JSON to be insensitive to key ordering.
    """

    diffs: list[ToolCallDiff] = []

    g_index: dict[str, ToolCall] = {tc.step_id: tc for tc in golden.tool_calls}
    c_index: dict[str, ToolCall] = {tc.step_id: tc for tc in candidate.tool_calls}

    # added / removed
    for sid, gtc in g_index.items():
        if sid not in c_index:
            diffs.append(
                ToolCallDiff(
                    kind="removed",
                    step_id=sid,
                    golden=gtc,
                    candidate=None,
                    details=f"step '{sid}' missing in candidate",
                )
            )
    for sid, ctc in c_index.items():
        if sid not in g_index:
            diffs.append(
                ToolCallDiff(
                    kind="added",
                    step_id=sid,
                    golden=None,
                    candidate=ctc,
                    details=f"step '{sid}' added in candidate",
                )
            )

    # args / output / order for matched ids
    shared_ids = [sid for sid in g_index if sid in c_index]
    for sid in shared_ids:
        gtc = g_index[sid]
        ctc = c_index[sid]
        if _canonical(gtc.args) != _canonical(ctc.args):
            diffs.append(
                ToolCallDiff(
                    kind="args_changed",
                    step_id=sid,
                    golden=gtc,
                    candidate=ctc,
                    details=f"args differ for step '{sid}'",
                )
            )
        if _canonical(gtc.output) != _canonical(ctc.output) or (gtc.error != ctc.error):
            diffs.append(
                ToolCallDiff(
                    kind="output_changed",
                    step_id=sid,
                    golden=gtc,
                    candidate=ctc,
                    details=f"output differs for step '{sid}'",
                )
            )

    # order changes are only meaningful when both sequences share the
    # same set of (step_id, tool_name) pairs but in a different order.
    g_keys = [_key(tc) for tc in golden.tool_calls]
    c_keys = [_key(tc) for tc in candidate.tool_calls]
    if sorted(g_keys) == sorted(c_keys) and g_keys != c_keys:
        diffs.append(
            ToolCallDiff(
                kind="order_changed",
                step_id="",
                golden=None,
                candidate=None,
                details=(
                    "tool calls match by set but order differs: "
                    f"golden={[k[0] for k in g_keys]} "
                    f"candidate={[k[0] for k in c_keys]}"
                ),
            )
        )

    cost_delta = round(candidate.total_cost_usd - golden.total_cost_usd, 10)
    duration_delta = candidate.total_duration_ms - golden.total_duration_ms
    final_match = _canonical(golden.final_output) == _canonical(candidate.final_output)

    parts = [f"{len(diffs)} tool-call diff(s)"]
    parts.append(f"cost delta {cost_delta:+.6f} USD")
    parts.append(f"duration delta {duration_delta:+d} ms")
    parts.append("final output " + ("match" if final_match else "mismatch"))
    summary = ", ".join(parts)

    return TrajectoryDiff(
        tool_call_diffs=diffs,
        cost_delta_usd=cost_delta,
        duration_delta_ms=duration_delta,
        final_output_match=final_match,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------


_DEFAULT_WEIGHTS: dict[str, float] = {
    "tool_match": 0.60,
    "final_output": 0.30,
    "cost": 0.10,
}


def replay_score(
    diff: TrajectoryDiff,
    weights: dict[str, float] | None = None,
    cost_budget_usd: float = 0.01,
) -> float:
    """Combine a diff into a 0..1 replay score.

    Defaults weight tool-call match rate at 60 percent, final-output
    match at 30 percent, and cost-within-budget at 10 percent. A perfect
    match returns 1.0. Custom weights override the defaults; missing
    keys fall back to the defaults and the result is normalised by the
    sum of the supplied weights so callers can pass any positive
    numbers.

    ``cost_budget_usd`` defines how much absolute cost drift is fully
    tolerated before the cost component starts to decay. Beyond
    ``cost_budget_usd`` the cost component decays linearly to zero at
    ten times the budget.
    """

    w = dict(_DEFAULT_WEIGHTS)
    if weights:
        for k, v in weights.items():
            if v < 0:
                raise ValueError(f"weight '{k}' must be non-negative")
            w[k] = float(v)
    total_w = w["tool_match"] + w["final_output"] + w["cost"]
    if total_w <= 0:
        raise ValueError("sum of weights must be positive")

    # Tool-call match component: penalise added / removed / changed /
    # order-changed equally. The denominator is the larger of the count
    # of matched-or-mismatched calls and 1 to avoid div-by-zero on empty
    # trajectories.
    mismatched = len(diff.tool_call_diffs)
    # We do not have direct access to the trajectory lengths here, so we
    # rebuild an approximation from the diff: any "removed" pair plus
    # any "added" pair plus any shared step that had args/output/order
    # changes. A perfect diff (empty) yields 1.0.
    if mismatched == 0:
        tool_match = 1.0
    else:
        # Approximate total calls as mismatched + shared-clean. We do not
        # know shared-clean from the diff alone, so we use a soft decay:
        # score = 1 / (1 + mismatched).
        tool_match = 1.0 / (1.0 + mismatched)

    final_output = 1.0 if diff.final_output_match else 0.0

    abs_cost = abs(diff.cost_delta_usd)
    if abs_cost <= cost_budget_usd:
        cost_score = 1.0
    else:
        # Linear decay from 1.0 at budget down to 0.0 at 10x budget.
        slope_end = cost_budget_usd * 10
        if abs_cost >= slope_end:
            cost_score = 0.0
        else:
            cost_score = 1.0 - (abs_cost - cost_budget_usd) / (slope_end - cost_budget_usd)

    score = (
        w["tool_match"] * tool_match
        + w["final_output"] * final_output
        + w["cost"] * cost_score
    ) / total_w
    # Clamp for safety against floating-point drift.
    return max(0.0, min(1.0, score))


__all__ = [
    "ToolCall",
    "Trajectory",
    "ToolCallDiff",
    "ToolCallDiffKind",
    "TrajectoryDiff",
    "compute_trajectory_checksum",
    "extract_trajectory",
    "diff_trajectories",
    "replay_score",
]
