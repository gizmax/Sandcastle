"""Tests for the multiagent coordinator helper (v0.32 prep)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandcastle.engine.dag import parse_yaml_string, validate
from sandcastle.engine.multiagent import (
    MAX_CONCURRENT_THREADS,
    MAX_ROSTER_SIZE,
    MultiagentConfig,
    MultiagentRosterEntry,
    RosterValidationError,
    ThreadEvent,
    build_coordinator_payload,
    parse_thread_event,
    validate_roster,
)


# ---------------------------------------------------------------------------
# MultiagentConfig roster-size validation
# ---------------------------------------------------------------------------

class TestRosterSize:
    def test_empty_roster_is_allowed(self):
        cfg = MultiagentConfig(roster=[])
        assert cfg.roster == []
        assert cfg.max_concurrent_threads == 25

    def test_max_size_roster_is_allowed(self):
        roster = [
            MultiagentRosterEntry(type="agent", id=f"agent-{i}")
            for i in range(MAX_ROSTER_SIZE)
        ]
        cfg = MultiagentConfig(roster=roster)
        assert len(cfg.roster) == MAX_ROSTER_SIZE

    def test_oversized_roster_raises(self):
        roster = [
            MultiagentRosterEntry(type="agent", id=f"agent-{i}")
            for i in range(MAX_ROSTER_SIZE + 1)
        ]
        with pytest.raises(RosterValidationError) as exc:
            MultiagentConfig(roster=roster)
        assert any("exceeds max" in e for e in exc.value.errors)

    def test_max_concurrent_threads_capped(self):
        with pytest.raises(RosterValidationError):
            MultiagentConfig(
                roster=[],
                max_concurrent_threads=MAX_CONCURRENT_THREADS + 1,
            )


# ---------------------------------------------------------------------------
# validate_roster - structural checks
# ---------------------------------------------------------------------------

class TestValidateRoster:
    def test_missing_id_on_agent_entry(self):
        errors = validate_roster([
            MultiagentRosterEntry(type="agent", id=None, nickname="r"),
        ])
        assert errors
        assert any("non-empty 'id'" in e for e in errors)

    def test_two_self_entries_rejected(self):
        errors = validate_roster([
            MultiagentRosterEntry(type="self", nickname="me1"),
            MultiagentRosterEntry(type="self", nickname="me2"),
        ])
        assert any("'self' entries" in e for e in errors)

    def test_valid_mixed_roster(self):
        errors = validate_roster([
            MultiagentRosterEntry(type="self", nickname="root"),
            MultiagentRosterEntry(type="agent", id="ag_1", nickname="a"),
            MultiagentRosterEntry(type="agent", id="ag_2", nickname="b"),
        ])
        assert errors == []


# ---------------------------------------------------------------------------
# build_coordinator_payload - wire format
# ---------------------------------------------------------------------------

class TestBuildPayload:
    def test_basic_shape(self):
        cfg = MultiagentConfig(
            roster=[
                MultiagentRosterEntry(
                    type="agent", id="ag_researcher", nickname="researcher"
                ),
                MultiagentRosterEntry(
                    type="agent", id="ag_writer", version=3, nickname="writer"
                ),
            ],
            max_concurrent_threads=4,
        )
        payload = build_coordinator_payload(cfg)
        assert "multiagent" in payload
        block = payload["multiagent"]
        assert block["type"] == "coordinator"
        assert block["max_concurrent_threads"] == 4
        assert len(block["agents"]) == 2
        assert block["agents"][0] == {
            "type": "agent",
            "id": "ag_researcher",
            "nickname": "researcher",
        }
        assert block["agents"][1] == {
            "type": "agent",
            "id": "ag_writer",
            "version": 3,
            "nickname": "writer",
        }
        assert "prompt_routing_hint" not in block

    def test_with_self_and_hint(self):
        cfg = MultiagentConfig(
            roster=[
                MultiagentRosterEntry(type="self", nickname="root"),
                MultiagentRosterEntry(
                    type="agent", id="ag_translator", nickname="translator"
                ),
            ],
            max_concurrent_threads=2,
            prompt_routing_hint="Route translations through translator.",
        )
        payload = build_coordinator_payload(cfg)
        block = payload["multiagent"]
        assert block["agents"][0] == {"type": "self", "nickname": "root"}
        assert block["prompt_routing_hint"] == "Route translations through translator."


# ---------------------------------------------------------------------------
# parse_thread_event - SSE event types
# ---------------------------------------------------------------------------

class TestParseThreadEvent:
    def test_thread_message_received(self):
        ev = parse_thread_event({
            "type": "agent.thread_message_received",
            "thread_id": "thr_123",
            "agent_id": "ag_researcher",
            "message": {"role": "assistant", "content": "hello"},
        })
        assert isinstance(ev, ThreadEvent)
        assert ev.event_type == "agent.thread_message_received"
        assert ev.thread_id == "thr_123"
        assert ev.agent_id == "ag_researcher"
        assert ev.payload["message"]["role"] == "assistant"

    def test_thread_message_sent(self):
        ev = parse_thread_event({
            "type": "agent.thread_message_sent",
            "thread_id": "thr_124",
            "agent_id": "ag_writer",
            "tokens": 42,
        })
        assert ev.event_type == "agent.thread_message_sent"
        assert ev.thread_id == "thr_124"
        assert ev.agent_id == "ag_writer"
        assert ev.payload["tokens"] == 42

    def test_session_thread_started_nested_data(self):
        ev = parse_thread_event({
            "type": "session.thread_started",
            "data": {
                "thread_id": "thr_125",
                "agent_id": "ag_analyst",
                "parent_thread_id": "thr_parent",
            },
        })
        assert ev.event_type == "session.thread_started"
        assert ev.thread_id == "thr_125"
        assert ev.agent_id == "ag_analyst"
        assert ev.payload["parent_thread_id"] == "thr_parent"

    def test_session_thread_completed_camelcase(self):
        ev = parse_thread_event({
            "type": "session.thread_completed",
            "threadId": "thr_126",
            "agentId": "ag_translator",
            "status": "completed",
            "duration_ms": 1234,
        })
        assert ev.event_type == "session.thread_completed"
        assert ev.thread_id == "thr_126"
        assert ev.agent_id == "ag_translator"
        assert ev.payload["status"] == "completed"
        assert ev.payload["duration_ms"] == 1234


# ---------------------------------------------------------------------------
# YAML templates parse and validate cleanly
# ---------------------------------------------------------------------------

TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent
    / "workflows"
    / "coordinator-templates"
)

TEMPLATE_FILES = [
    "research-and-write.yaml",
    "code-review-and-test.yaml",
    "analyst-with-translator.yaml",
]


@pytest.mark.parametrize("filename", TEMPLATE_FILES)
def test_coordinator_template_parses(filename):
    path = TEMPLATE_DIR / filename
    assert path.exists(), f"missing template: {path}"
    wf = parse_yaml_string(path.read_text())
    assert wf.name
    assert wf.risk_level == "limited"
    assert any(s.type == "managed-agent" for s in wf.steps)


@pytest.mark.parametrize("filename", TEMPLATE_FILES)
def test_coordinator_template_validates(filename):
    path = TEMPLATE_DIR / filename
    wf = parse_yaml_string(path.read_text())
    errors = validate(wf)
    assert errors == [], f"{filename} validation errors: {errors}"
