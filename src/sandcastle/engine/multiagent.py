"""Multiagent Coordinator helper for Sandcastle (v0.32 prep).

Wraps Anthropic's Managed Agents multiagent preview feature
(header ``managed-agents-2026-04-01``, added 2026-05-06):

- A *coordinator* agent delegates to a roster of other Managed Agents.
- Roster supports up to 20 entries, including an optional ``type: self``
  entry that lets the coordinator delegate to itself.
- Up to 25 concurrent threads can run in parallel.
- Hierarchy is one level deep - subagents cannot themselves spawn more.

This module is self-contained: it builds the wire payload for the
``POST /v1/agents`` (and the future ``managed_agent_config.multiagent``
YAML block) and parses the SSE thread events streamed back on the
parent thread.

The executor and DAG parser do *not* yet read the ``multiagent`` block;
that wire-up arrives in v0.32 Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

# ---------------------------------------------------------------------------
# Limits taken from Anthropic's preview docs
# ---------------------------------------------------------------------------

MAX_ROSTER_SIZE = 20
MAX_CONCURRENT_THREADS = 25
MAX_DEPTH = 1

VALID_ENTRY_TYPES = frozenset({"agent", "self"})

# SSE event types emitted on the coordinator (parent) thread when subagents
# are running. We surface them via ``parse_thread_event``.
KNOWN_THREAD_EVENTS = frozenset({
    "agent.thread_message_received",
    "agent.thread_message_sent",
    "session.thread_started",
    "session.thread_completed",
    "session.thread_failed",
})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RosterValidationError(Exception):
    """Raised when a multiagent roster fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid roster")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MultiagentRosterEntry:
    """One entry in a coordinator's roster.

    - ``type="agent"`` references another Managed Agent by ``id`` (and
      optional pinned ``version``).
    - ``type="self"`` lets the coordinator delegate to itself - useful
      for recursive style flows. Only one ``self`` entry is allowed.
    - ``nickname`` is the short label the coordinator uses to address
      this entry in its routing prompt (e.g. ``"researcher"``).
    """

    type: Literal["agent", "self"] = "agent"
    id: str | None = None
    version: int | None = None
    nickname: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialize to the Anthropic wire format."""
        payload: dict[str, Any] = {"type": self.type}
        if self.type == "agent":
            if self.id:
                payload["id"] = self.id
            if self.version is not None:
                payload["version"] = self.version
        if self.nickname:
            payload["nickname"] = self.nickname
        return payload


@dataclass
class MultiagentConfig:
    """Coordinator configuration."""

    roster: list[MultiagentRosterEntry] = field(default_factory=list)
    max_concurrent_threads: int = 25
    prompt_routing_hint: str | None = None

    def __post_init__(self) -> None:
        errors = validate_roster(self.roster)
        if errors:
            raise RosterValidationError(errors)
        if not isinstance(self.max_concurrent_threads, int):
            raise RosterValidationError(
                [f"max_concurrent_threads must be int, got "
                 f"{type(self.max_concurrent_threads).__name__}"]
            )
        if self.max_concurrent_threads < 1:
            raise RosterValidationError([
                "max_concurrent_threads must be >= 1"
            ])
        if self.max_concurrent_threads > MAX_CONCURRENT_THREADS:
            raise RosterValidationError([
                f"max_concurrent_threads {self.max_concurrent_threads} exceeds "
                f"limit of {MAX_CONCURRENT_THREADS}"
            ])


@dataclass
class ThreadEvent:
    """A parsed SSE event from a coordinator's parent thread."""

    thread_id: str
    agent_id: str
    event_type: str
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_roster(roster: Iterable[MultiagentRosterEntry] | None) -> list[str]:
    """Validate a roster. Returns a list of error strings (empty == ok)."""
    errors: list[str] = []
    if roster is None:
        errors.append("roster must be a list, got None")
        return errors

    try:
        items = list(roster)
    except TypeError:
        errors.append("roster must be iterable")
        return errors

    if len(items) > MAX_ROSTER_SIZE:
        errors.append(
            f"roster has {len(items)} entries, exceeds max of {MAX_ROSTER_SIZE}"
        )

    seen_self = 0
    seen_ids: set[str] = set()
    seen_nicknames: set[str] = set()

    for idx, entry in enumerate(items):
        if not isinstance(entry, MultiagentRosterEntry):
            errors.append(
                f"roster[{idx}] must be a MultiagentRosterEntry, "
                f"got {type(entry).__name__}"
            )
            continue
        if entry.type not in VALID_ENTRY_TYPES:
            errors.append(
                f"roster[{idx}] has invalid type '{entry.type}'. "
                f"Must be one of: {', '.join(sorted(VALID_ENTRY_TYPES))}"
            )
            continue
        if entry.type == "agent":
            if not entry.id or not isinstance(entry.id, str):
                errors.append(
                    f"roster[{idx}] type=agent requires a non-empty 'id'"
                )
            else:
                if entry.id in seen_ids:
                    errors.append(
                        f"roster[{idx}] duplicate agent id '{entry.id}'"
                    )
                seen_ids.add(entry.id)
            if entry.version is not None and (
                not isinstance(entry.version, int) or entry.version < 1
            ):
                errors.append(
                    f"roster[{idx}] version must be a positive int, "
                    f"got {entry.version!r}"
                )
        elif entry.type == "self":
            seen_self += 1
            if entry.id is not None:
                errors.append(
                    f"roster[{idx}] type=self must not carry an 'id'"
                )

        if entry.nickname is not None:
            if not isinstance(entry.nickname, str) or not entry.nickname:
                errors.append(
                    f"roster[{idx}] nickname must be a non-empty string"
                )
            elif entry.nickname in seen_nicknames:
                errors.append(
                    f"roster[{idx}] duplicate nickname '{entry.nickname}'"
                )
            else:
                seen_nicknames.add(entry.nickname)

    if seen_self > 1:
        errors.append(
            f"roster has {seen_self} 'self' entries; at most 1 is allowed"
        )

    return errors


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def build_coordinator_payload(config: MultiagentConfig) -> dict[str, Any]:
    """Return the ``multiagent`` payload chunk for an agent create call.

    Shape::

        {
          "multiagent": {
            "type": "coordinator",
            "agents": [ {type, id, version?, nickname?}, ... ],
            "max_concurrent_threads": 25,
            "prompt_routing_hint": "..."   # only when set
          }
        }
    """
    errors = validate_roster(config.roster)
    if errors:
        raise RosterValidationError(errors)

    block: dict[str, Any] = {
        "type": "coordinator",
        "agents": [e.to_payload() for e in config.roster],
        "max_concurrent_threads": config.max_concurrent_threads,
    }
    if config.prompt_routing_hint:
        block["prompt_routing_hint"] = config.prompt_routing_hint
    return {"multiagent": block}


# ---------------------------------------------------------------------------
# SSE thread event parsing
# ---------------------------------------------------------------------------

def parse_thread_event(event: dict[str, Any]) -> ThreadEvent:
    """Parse an Anthropic SSE thread event into a typed object.

    Accepts both the flat shape ({type, thread_id, agent_id, ...}) and the
    nested shape ({type, data: {...}}). The remaining payload fields end up
    in ``ThreadEvent.payload``.
    """
    if not isinstance(event, dict):
        raise ValueError(
            f"event must be a mapping, got {type(event).__name__}"
        )

    event_type = event.get("type") or event.get("event") or ""
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event is missing a 'type' field")

    # Some clients put fields inside a `data` envelope.
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    merged: dict[str, Any] = {}
    merged.update(data)
    for k, v in event.items():
        if k in ("type", "event", "data"):
            continue
        merged[k] = v

    thread_id = (
        merged.pop("thread_id", None)
        or merged.pop("threadId", None)
        or ""
    )
    agent_id = (
        merged.pop("agent_id", None)
        or merged.pop("agentId", None)
        or ""
    )

    return ThreadEvent(
        thread_id=str(thread_id) if thread_id is not None else "",
        agent_id=str(agent_id) if agent_id is not None else "",
        event_type=event_type,
        payload=merged,
    )


__all__ = [
    "MAX_ROSTER_SIZE",
    "MAX_CONCURRENT_THREADS",
    "MAX_DEPTH",
    "KNOWN_THREAD_EVENTS",
    "VALID_ENTRY_TYPES",
    "RosterValidationError",
    "MultiagentRosterEntry",
    "MultiagentConfig",
    "ThreadEvent",
    "validate_roster",
    "build_coordinator_payload",
    "parse_thread_event",
]
