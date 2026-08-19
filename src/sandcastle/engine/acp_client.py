"""Sandcastle as an Agent Client Protocol client.

ACP is one wire protocol for "run somebody else's agent loop": the client spawns
a harness (Claude Code, Codex, Gemini CLI, goose) as a subprocess and talks
newline-delimited JSON-RPC 2.0 over its stdio. Sandcastle is a **client only** -
ACP server mode is deliberately not implemented, see ``docs/acp.md``.

Three facts shape everything below, and each is a decision the code cannot make
differently without being wrong:

1. **The protocol version is the integer ``1``.** Not semver. ``0.70`` is the
   version of the ``claude-agent-acp`` npm adapter, not of the spec. A breaking
   change bumps the integer; everything additive arrives as a capability.

2. **``session/prompt`` returns only ``stopReason``.** There is no ``result``,
   ``output`` or ``text`` field anywhere in the response. The answer has to be
   reassembled by *us* from the ``agent_message_chunk`` stream that arrives as
   ``session/update`` notifications while the prompt is in flight. Miss that and
   an ACP step returns the empty string on every successful turn.

3. **Permission option ids are agent-defined strings.** ``"allow"`` and
   ``"reject"`` are illustrations in the spec, not constants. A client that
   matches on the id works against one harness and silently mis-decides against
   the next, so :func:`resolve_permission` matches on ``PermissionOptionKind``
   and never reads ``optionId`` when deciding.

Why hand-rolled rather than the ``agent-client-protocol`` SDK: the wire surface
we need is five outbound methods and two inbound ones, the framing is one JSON
object per line, and an optional dependency that CI cannot install would leave
the whole layer exercised only by mocks. The fake agent in
``tests/fixtures/fake_acp_agent.py`` is hand-rolled for the same reason - it
pins our reading of the wire format from the other side.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# The integer on the wire. Version 2 exists as an unstable draft gated behind a
# Rust feature flag and restructures the very areas we use (prompt lifecycle,
# permission requests), so it is refused in validation rather than attempted.
ACP_PROTOCOL_VERSION = 1

# JSON-RPC 2.0 error codes we actually produce.
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

# StopReason is a closed enum of exactly these five strings.
STOP_REASONS = frozenset(
    {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}
)

# PermissionOptionKind, also closed.
PERMISSION_KINDS = frozenset(
    {"allow_once", "allow_always", "reject_once", "reject_always"}
)

VALID_FILESYSTEM_MODES = frozenset({"none", "read", "readwrite"})
VALID_PERMISSION_DEFAULTS = frozenset({"reject", "allow_once", "allow_always", "ask"})
VALID_ELICITATION_MODES = frozenset({"decline", "ask"})
VALID_OUTPUT_FORMATS = frozenset({"text", "json", "full"})

# Shorthand names for harnesses in the public ACP registry. Deliberately
# **unpinned**: a pin in this table ships stale the week after release, and an
# unpinned npx invocation is reproducible-enough because we record the
# agentInfo the harness reports at initialize into the audit event. Anyone who
# needs a byte-exact rerun writes command/args with their own pin.
_BUILTIN_ACP_AGENTS: dict[str, tuple[str, list[str]]] = {
    "claude": ("npx", ["-y", "@agentclientprotocol/claude-agent-acp"]),
    "codex": ("npx", ["-y", "@agentclientprotocol/codex-acp"]),
    "gemini": ("npx", ["-y", "@google/gemini-cli", "--acp"]),
    "goose": ("goose", ["acp"]),
}

# One JSON message per line, and messages "MUST NOT contain embedded newlines".
# asyncio's default StreamReader limit is 64 KB, which a single large tool-call
# update blows through; the SDK raised its own limit to 50 MB for the same
# reason, so we match it.
STDIO_BUFFER_LIMIT_BYTES = 50 * 1024 * 1024

# Real CLIs print banners, npm prints install noise. The spec says an agent
# "MUST NOT write anything to its stdout that is not a valid ACP message", but
# refusing to tolerate any junk turns a cosmetic vendor bug into an outage. We
# skip non-JSON lines, keep them for the diagnosis, and give up after this many
# so an agent that never speaks ACP fails loudly instead of hanging.
_MAX_JUNK_LINES = 64

# Tail of the harness's stderr kept for the failure message. Never part of the
# step output - stderr is free-form logging, not an answer.
_STDERR_TAIL_BYTES = 8192

# How long a graceful cancel gets before we stop being polite.
_CANCEL_GRACE_SECONDS = 10.0
_TERMINATE_GRACE_SECONDS = 5.0

# How often the watchdog wakes to check the idle clock and the run's cancel flag.
_WATCHDOG_TICK_SECONDS = 0.5


class AcpError(Exception):
    """An ACP turn failed. ``kind`` classifies it for the executor.

    Kinds: ``spawn`` | ``protocol`` | ``version`` | ``timeout`` | ``idle`` |
    ``cancelled`` | ``crashed`` | ``capability`` | ``config``.
    """

    def __init__(self, kind: str, message: str, *, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.stderr_tail = stderr_tail


@dataclass
class AcpTurnResult:
    """Everything one ``session/prompt`` turn produced, reassembled."""

    text: str = ""
    thoughts: str = ""
    stop_reason: str = ""
    session_id: str = ""
    agent_info: dict | None = None
    protocol_version: int = ACP_PROTOCOL_VERSION
    tool_calls: list[dict] = field(default_factory=list)
    permissions: list[dict] = field(default_factory=list)
    usage: dict | None = None
    plan: Any = None
    modes: dict | None = None
    truncated: bool = False
    stderr_tail: str = ""


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def build_acp_env(cfg: Any, parent_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build the harness's environment: OS plumbing, then explicit additions.

    Starts from :func:`build_minimal_subprocess_env` - PATH/HOME/LANG/TMPDIR and
    the ``LC_*`` family, with parent credentials and application configuration
    deliberately excluded - then adds exactly what the step asked for. Nothing
    is inherited implicitly, so handing a harness ``ANTHROPIC_API_KEY`` is a
    written decision in the YAML rather than an accident of the worker's shell.
    """
    from sandcastle.engine.subprocess_env import (
        _SAFE_SUBPROCESS_ENV_VARS,
        build_minimal_subprocess_env,
    )

    if parent_env is None:
        source: dict[str, str] = dict(os.environ)
        env = build_minimal_subprocess_env()
    else:
        source = dict(parent_env)
        env = {
            key: value
            for key, value in source.items()
            if value and (key in _SAFE_SUBPROCESS_ENV_VARS or key.startswith("LC_"))
        }
    for name in getattr(cfg, "env_passthrough", None) or []:
        value = source.get(str(name))
        if value:
            env[str(name)] = value
        else:
            logger.warning(
                "ACP env_passthrough names '%s' but it is not set in the parent "
                "environment - the harness will not receive it",
                name,
            )
    for key, value in (getattr(cfg, "env", None) or {}).items():
        env[str(key)] = str(value)
    return env


def resolve_agent_shorthand(cfg: Any) -> tuple[str, list[str]]:
    """Return the (command, args) an ``acp_config`` resolves to.

    ``agent:`` is a lookup in a built-in table, never a registry download: a
    workflow pulled from the hub must not be able to make us fetch and execute
    an arbitrary artifact.
    """
    shorthand = (getattr(cfg, "agent", "") or "").strip()
    if shorthand:
        entry = _BUILTIN_ACP_AGENTS.get(shorthand)
        if entry is None:
            raise AcpError(
                "config",
                f"Unknown acp agent shorthand '{shorthand}'. "
                f"Known: {', '.join(sorted(_BUILTIN_ACP_AGENTS))}",
            )
        command, args = entry
        return command, list(args)
    command = (getattr(cfg, "command", "") or "").strip()
    if not command:
        raise AcpError("config", "acp_config needs either 'command' or 'agent'")
    return command, [str(a) for a in (getattr(cfg, "args", None) or [])]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def resolve_workspace_path(
    raw: str,
    allowed_roots: list[str],
    *,
    label: str = "cwd",
    must_exist: bool = True,
) -> Path:
    """Resolve a workspace path and prove it sits inside an allowed root.

    An ACP step hands a directory to an external process that will read and
    write it, so this is the boundary that decides what the harness can touch on
    disk. ``allowed_roots`` empty means the feature is off: an operator opts in
    per deployment rather than every install shipping "any directory, please".
    """
    if not raw or not str(raw).strip():
        raise AcpError("config", f"acp_config.{label} is required")
    text = str(raw).strip()
    if ".." in Path(text).parts:
        # Rejected before resolve() so a symlinked parent cannot launder it.
        raise AcpError("config", f"acp_config.{label} must not contain '..': {text}")
    path = Path(text)
    if not path.is_absolute():
        raise AcpError("config", f"acp_config.{label} must be an absolute path: {text}")
    resolved = path.resolve()
    if must_exist and not resolved.is_dir():
        raise AcpError("config", f"acp_config.{label} is not an existing directory: {resolved}")
    if not allowed_roots:
        raise AcpError(
            "config",
            "type: acp is disabled: no workspace roots are configured. Set "
            "SANDCASTLE_ACP_ALLOWED_ROOTS to the directories an external agent "
            "harness may be pointed at.",
        )
    for root in allowed_roots:
        try:
            root_path = Path(str(root)).expanduser().resolve()
        except OSError:  # pragma: no cover - unresolvable root is a config typo
            continue
        if resolved == root_path or root_path in resolved.parents:
            return resolved
    raise AcpError(
        "config",
        f"acp_config.{label} '{resolved}' is outside every configured "
        f"acp_allowed_roots entry ({', '.join(str(r) for r in allowed_roots)})",
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def resolve_permission(cfg: Any, tool_call: dict) -> tuple[str, str]:
    """Decide one ``session/request_permission``. Returns ``(kind, rule)``.

    Rules are ordered and first match wins; anything unmatched falls through to
    ``cfg.permission``. Matching is on the tool call's ``kind`` and ``title``
    and on nothing the agent gets to name freely - in particular never on
    ``optionId``, which is an agent-defined string.
    """
    kind = str(tool_call.get("kind") or "").strip().lower()
    title = str(tool_call.get("title") or "")
    name = str(tool_call.get("toolName") or tool_call.get("tool") or "")

    for index, rule in enumerate(getattr(cfg, "permission_rules", None) or []):
        if not isinstance(rule, dict):
            continue
        want_kind = str(rule.get("kind") or "").strip().lower()
        if want_kind and want_kind != kind:
            continue
        needle = str(rule.get("title_matches") or "")
        if needle and needle.lower() not in title.lower():
            continue
        want_tool = str(rule.get("tool") or "")
        if want_tool and want_tool.lower() not in name.lower():
            continue
        decision = str(rule.get("decision") or "").strip().lower()
        if decision not in PERMISSION_KINDS:
            logger.warning(
                "ACP permission rule %d has an invalid decision '%s'; ignoring",
                index,
                decision,
            )
            continue
        return decision, f"rule[{index}]"

    default = (getattr(cfg, "permission", "") or "reject").strip().lower()
    if default == "reject":
        return "reject_once", "default"
    if default in PERMISSION_KINDS:
        return default, "default"
    if default == "ask":
        # There is no human on this end of an unattended workflow run. "ask"
        # means "the rules decide"; an unmatched request is still a no, because
        # the alternative is an unattended blanket yes.
        return "reject_once", "default(ask,unmatched)"
    return "reject_once", "default"


def _pick_option(options: list[dict], decision_kind: str) -> dict | None:
    """Choose the agent's option whose ``kind`` matches our decision.

    Falls back within the same allow/reject family: a harness that only offers
    ``allow_always`` when we decided ``allow_once`` should still get an answer
    it understands, and a reject decision must never degrade into an allow.
    """
    by_kind = {}
    for option in options:
        if isinstance(option, dict) and option.get("kind"):
            by_kind.setdefault(str(option["kind"]), option)
    if decision_kind in by_kind:
        return by_kind[decision_kind]
    family = "reject" if decision_kind.startswith("reject") else "allow"
    for kind, option in by_kind.items():
        if kind.startswith(family):
            return option
    return None


# ---------------------------------------------------------------------------
# The connection
# ---------------------------------------------------------------------------

class _Connection:
    """Newline-delimited JSON-RPC 2.0 over a subprocess's stdio.

    Both directions at once: we issue requests to the agent and the agent
    issues requests to us on the same pipes, which is why the reader is a
    single task that demultiplexes rather than a per-call read.
    """

    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        handler: Callable[[str, dict], Awaitable[Any]],
        *,
        on_activity: Callable[[], None] | None = None,
    ) -> None:
        self._proc = proc
        self._handler = handler
        self._on_activity = on_activity
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._closed = False
        self._junk_lines: list[str] = []
        self._write_lock = asyncio.Lock()
        # Inbound requests are served on their own tasks so the reader never
        # blocks; they are tracked so close() can cancel any still in flight.
        self._serving: set[asyncio.Task] = set()

    def start(self) -> None:
        self._reader_task = asyncio.create_task(self._read_loop())

    @property
    def junk(self) -> list[str]:
        return self._junk_lines

    async def _read_loop(self) -> None:
        stdout = self._proc.stdout
        assert stdout is not None
        while True:
            try:
                line = await stdout.readline()
            except (asyncio.LimitOverrunError, ValueError) as exc:
                self._fail_all(
                    AcpError("protocol", f"ACP agent sent an oversized message: {exc}")
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - pipe torn down mid-read
                self._fail_all(AcpError("protocol", f"ACP stdout read failed: {exc}"))
                return
            if not line:
                self._fail_all(
                    AcpError("crashed", "ACP agent closed stdout before answering")
                )
                return
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                self._junk_lines.append(text[:500])
                if len(self._junk_lines) > _MAX_JUNK_LINES:
                    self._fail_all(
                        AcpError(
                            "protocol",
                            "ACP agent wrote non-ACP output to stdout "
                            f"({len(self._junk_lines)} lines); first was: "
                            f"{self._junk_lines[0][:200]}",
                        )
                    )
                    return
                logger.debug("Ignoring non-ACP line on agent stdout: %s", text[:200])
                continue
            if self._on_activity is not None:
                self._on_activity()
            if not isinstance(message, dict):
                self._junk_lines.append(text[:500])
                continue
            await self._dispatch(message)

    async def _dispatch(self, message: dict) -> None:
        if "method" in message:
            coro = self._serve(message) if "id" in message else self._serve_notification(message)
            task = asyncio.create_task(coro)
            self._serving.add(task)
            task.add_done_callback(self._serving.discard)
            return
        msg_id = message.get("id")
        future = self._pending.pop(msg_id, None) if isinstance(msg_id, int) else None
        if future is None or future.done():
            return
        if "error" in message:
            err = message.get("error") or {}
            future.set_exception(
                AcpError(
                    "protocol",
                    f"ACP agent returned error {err.get('code')}: {err.get('message')}",
                )
            )
        else:
            future.set_result(message.get("result"))

    async def _serve(self, message: dict) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            result = await self._handler(method, params)
        except _MethodNotFound:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {
                        "code": JSONRPC_METHOD_NOT_FOUND,
                        "message": f"Method not found: {method}",
                    },
                }
            )
            return
        except _RequestRejected as exc:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": JSONRPC_INVALID_PARAMS, "message": str(exc)},
                }
            )
            return
        except Exception as exc:  # noqa: BLE001 - a client bug must not hang the agent
            logger.warning("ACP client handler for '%s' raised: %s", method, exc)
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": JSONRPC_INTERNAL_ERROR, "message": str(exc)},
                }
            )
            return
        await self._send({"jsonrpc": "2.0", "id": message.get("id"), "result": result})

    async def _serve_notification(self, message: dict) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            await self._handler(method, params)
        except _MethodNotFound:
            logger.debug("Ignoring unsupported ACP notification '%s'", method)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ACP client notification '%s' raised: %s", method, exc)

    async def _send(self, payload: dict) -> None:
        if self._closed:
            return
        stdin = self._proc.stdin
        if stdin is None or stdin.is_closing():
            return
        # One object per line, no embedded newlines - that is the whole framing.
        data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                stdin.write(data)
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
                raise AcpError("crashed", f"ACP agent stdin closed: {exc}") from exc

    async def request(self, method: str, params: dict, *, timeout: float | None = None) -> Any:
        self._next_id += 1
        msg_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._send(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        )
        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _fail_all(self, exc: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    async def close(self) -> None:
        self._closed = True
        for task in list(self._serving):
            task.cancel()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        self._fail_all(AcpError("cancelled", "ACP connection closed"))


class _MethodNotFound(Exception):
    """Answer this inbound request with JSON-RPC -32601."""


class _RequestRejected(Exception):
    """Answer this inbound request with JSON-RPC -32602 and a reason."""


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

class SandcastleAcpClient:
    """The client half of an ACP session: what the agent is allowed to do to us.

    Every capability here is one we grant and can therefore revoke - which is
    the structural advantage this step type has over a managed container whose
    filesystem is opaque to the orchestrator.
    """

    def __init__(
        self,
        cfg: Any,
        workspace: Path,
        *,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.workspace = workspace
        self.on_event = on_event
        self.text_parts: list[str] = []
        self.thought_parts: list[str] = []
        self.tool_calls: list[dict] = []
        self._tool_call_index: dict[str, dict] = {}
        self.permissions: list[dict] = []
        self.usage: dict | None = None
        self.plan: Any = None
        self.current_mode: str = ""
        self.update_counts: dict[str, int] = {}
        # Set when the turn is being cancelled: the spec requires every pending
        # permission request to be answered {"outcome": "cancelled"} rather than
        # left hanging or errored.
        self.cancelling = False

    # -- inbound dispatch ---------------------------------------------------

    async def handle(self, method: str, params: dict) -> Any:
        if method == "session/update":
            return await self._on_session_update(params)
        if method == "session/request_permission":
            return await self._on_request_permission(params)
        if method == "fs/read_text_file":
            return await self._on_read_text_file(params)
        if method == "fs/write_text_file":
            return await self._on_write_text_file(params)
        if method == "$/cancel_request":
            return None
        # terminal/*, elicitation/* and everything else: we never advertised the
        # capability, so -32601 is the specified answer. Declining elicitation
        # with a typed response would mean inventing a payload shape we have not
        # verified against a real agent; method-not-found is both correct and
        # honest.
        raise _MethodNotFound(method)

    async def _on_session_update(self, params: dict) -> None:
        update = params.get("update") or {}
        if not isinstance(update, dict):
            return
        kind = str(update.get("sessionUpdate") or "")
        self.update_counts[kind] = self.update_counts.get(kind, 0) + 1

        if kind == "agent_message_chunk":
            text = _content_text(update.get("content"))
            if text:
                self.text_parts.append(text)
                self._emit("agent_message_chunk", {"text": text})
        elif kind == "agent_thought_chunk":
            text = _content_text(update.get("content"))
            if text:
                self.thought_parts.append(text)
        elif kind in ("tool_call", "tool_call_update"):
            self._record_tool_call(update)
        elif kind == "plan":
            self.plan = update.get("entries", update.get("plan"))
        elif kind == "usage_update":
            # `used` is context occupancy, not consumption - see the cost rules
            # in _execute_acp_step. Last update wins; nothing is ever summed.
            self.usage = {
                key: value
                for key, value in update.items()
                if key in ("used", "size", "cost")
            }
        elif kind == "current_mode_update":
            self.current_mode = str(update.get("currentModeId") or update.get("modeId") or "")

    def _record_tool_call(self, update: dict) -> None:
        call_id = str(update.get("toolCallId") or "")
        payload = {
            key: update[key]
            for key in ("toolCallId", "title", "kind", "status", "locations", "rawInput")
            if key in update
        }
        if call_id and call_id in self._tool_call_index:
            self._tool_call_index[call_id].update(payload)
        else:
            if call_id:
                self._tool_call_index[call_id] = payload
            self.tool_calls.append(payload)
        self._emit("tool_call", payload)

    async def _on_request_permission(self, params: dict) -> dict:
        tool_call = params.get("toolCall") or {}
        if not isinstance(tool_call, dict):
            tool_call = {}
        options = [o for o in (params.get("options") or []) if isinstance(o, dict)]

        if self.cancelling:
            # Required by the spec on cancel, and the reason we answer at all:
            # an unanswered request leaves the agent waiting forever.
            self.permissions.append(
                {
                    "toolCallId": tool_call.get("toolCallId", ""),
                    "title": tool_call.get("title", ""),
                    "kind": tool_call.get("kind", ""),
                    "decision": "cancelled",
                    "optionId": "",
                    "rule": "cancelled",
                }
            )
            return {"outcome": {"outcome": "cancelled"}}

        decision, rule = resolve_permission(self.cfg, tool_call)
        option = _pick_option(options, decision)
        record = {
            "toolCallId": tool_call.get("toolCallId", ""),
            "title": tool_call.get("title", ""),
            "kind": tool_call.get("kind", ""),
            "decision": decision,
            "optionId": str(option.get("optionId")) if option else "",
            "rule": rule,
        }
        self.permissions.append(record)
        self._emit("permission", record)
        logger.info(
            "ACP permission: tool_call kind=%s title=%r -> %s (%s)",
            record["kind"],
            record["title"][:80],
            decision,
            rule,
        )
        if option is None:
            # The harness offered nothing we can map onto our decision. Refusing
            # the whole request is the only safe answer; picking one of its
            # options blind is how a reject turns into an allow.
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": option.get("optionId")}}

    # -- filesystem the agent asks *us* to touch ----------------------------

    def _check_fs_path(self, raw: Any) -> Path:
        text = str(raw or "")
        if not text:
            raise _RequestRejected("path is required")
        path = Path(text)
        if not path.is_absolute():
            raise _RequestRejected(f"path must be absolute: {text}")
        resolved = path.resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise _RequestRejected(
                f"path is outside the session workspace {self.workspace}"
            )
        return resolved

    async def _on_read_text_file(self, params: dict) -> dict:
        mode = getattr(self.cfg, "filesystem", "none")
        if mode not in ("read", "readwrite"):
            raise _MethodNotFound("fs/read_text_file")
        path = self._check_fs_path(params.get("path"))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise _RequestRejected(f"cannot read {path}: {exc}") from exc
        line = params.get("line")
        limit = params.get("limit")
        if isinstance(line, int) or isinstance(limit, int):
            lines = content.splitlines(keepends=True)
            start = max(int(line) - 1, 0) if isinstance(line, int) else 0
            end = start + int(limit) if isinstance(limit, int) else len(lines)
            content = "".join(lines[start:end])
        return {"content": content}

    async def _on_write_text_file(self, params: dict) -> dict:
        if getattr(self.cfg, "filesystem", "none") != "readwrite":
            raise _MethodNotFound("fs/write_text_file")
        path = self._check_fs_path(params.get("path"))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(params.get("content") or ""), encoding="utf-8")
        except OSError as exc:
            raise _RequestRejected(f"cannot write {path}: {exc}") from exc
        return {}

    # -- helpers ------------------------------------------------------------

    def client_capabilities(self) -> dict:
        mode = getattr(self.cfg, "filesystem", "none")
        return {
            "fs": {
                "readTextFile": mode in ("read", "readwrite"),
                "writeTextFile": mode == "readwrite",
            },
            # terminal/* is refused outright in 0.45: it is a shell, and a shell
            # inside a step whose whole premise is "somebody else's agent" needs
            # a sandbox story we do not have.
            "terminal": False,
        }

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(kind, payload)
        except Exception as exc:  # noqa: BLE001 - telemetry must not break a turn
            logger.debug("ACP on_event callback failed: %s", exc)


def _content_text(content: Any) -> str:
    """Pull display text out of an ACP ContentBlock."""
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return ""
    if content.get("type") == "text":
        return str(content.get("text") or "")
    # Non-text blocks (image, audio, resource) have no textual answer to
    # reassemble; the tool-call record is where their effect shows up.
    return ""


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------

async def run_acp_turn(
    cfg: Any,
    message: str,
    *,
    workspace: Path,
    env: dict[str, str],
    additional_directories: list[str] | None = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> AcpTurnResult:
    """Spawn a harness, run one prompt turn, and reassemble its answer.

    Returns on ``stopReason``; raises :class:`AcpError` for everything that is
    not a turn outcome. The subprocess is always reaped, including on
    cancellation: a leaked harness keeps spending the user's money after the run
    that started it is gone.
    """
    command, args = resolve_agent_shorthand(cfg)
    if shutil.which(command) is None and not Path(command).exists():
        raise AcpError("spawn", f"ACP agent command not found on PATH: {command}")

    timeout = float(getattr(cfg, "timeout", 900) or 900)
    idle_timeout = float(getattr(cfg, "idle_timeout", 0) or 0)
    started = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
            env=env,
            limit=STDIO_BUFFER_LIMIT_BYTES,
        )
    except (OSError, ValueError) as exc:
        raise AcpError("spawn", f"Could not spawn ACP agent '{command}': {exc}") from exc

    stderr_buf: bytearray = bytearray()
    stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_buf))

    client = SandcastleAcpClient(cfg, workspace, on_event=on_event)
    last_activity = [time.monotonic()]

    def _touch() -> None:
        last_activity[0] = time.monotonic()

    conn = _Connection(proc, client.handle, on_activity=_touch)
    conn.start()

    result = AcpTurnResult()
    try:
        remaining = max(timeout - (time.monotonic() - started), 1.0)
        init = await conn.request(
            "initialize",
            {
                "protocolVersion": int(getattr(cfg, "protocol_version", ACP_PROTOCOL_VERSION)),
                "clientCapabilities": client.client_capabilities(),
                "clientInfo": _client_info(),
            },
            timeout=min(remaining, 120.0),
        )
        init = init if isinstance(init, dict) else {}
        negotiated = init.get("protocolVersion")
        requested = int(getattr(cfg, "protocol_version", ACP_PROTOCOL_VERSION))
        if negotiated != requested:
            # The agent MUST answer with the latest version it supports when it
            # cannot honour ours. Failing fast here is the difference between a
            # clear error and a hang two calls later on a method it does not have.
            if getattr(cfg, "strict_version", True):
                raise AcpError(
                    "version",
                    f"ACP agent negotiated protocol version {negotiated!r}, "
                    f"we require {requested}",
                )
            logger.warning(
                "ACP agent negotiated protocol version %r, we requested %d "
                "(strict_version is off, continuing)",
                negotiated,
                requested,
            )
        result.protocol_version = negotiated if isinstance(negotiated, int) else requested
        result.agent_info = init.get("agentInfo") if isinstance(init.get("agentInfo"), dict) else None
        agent_caps = init.get("agentCapabilities") if isinstance(init.get("agentCapabilities"), dict) else {}

        new_params: dict[str, Any] = {
            "cwd": str(workspace),
            "mcpServers": list(getattr(cfg, "mcp_servers", None) or []),
        }
        if additional_directories:
            session_caps = agent_caps.get("sessionCapabilities")
            supported = bool(
                isinstance(session_caps, dict) and session_caps.get("additionalDirectories")
            )
            if not supported:
                raise AcpError(
                    "capability",
                    "acp_config.additional_directories was set but the agent does "
                    "not advertise agentCapabilities.sessionCapabilities."
                    "additionalDirectories",
                )
            new_params["additionalDirectories"] = list(additional_directories)

        remaining = max(timeout - (time.monotonic() - started), 1.0)
        session = await conn.request("session/new", new_params, timeout=min(remaining, 120.0))
        session = session if isinstance(session, dict) else {}
        session_id = str(session.get("sessionId") or "")
        if not session_id:
            raise AcpError("protocol", "ACP agent returned no sessionId from session/new")
        result.session_id = session_id
        available_modes = session.get("modes")

        mode = (getattr(cfg, "mode", "") or "").strip()
        if mode:
            # modeId is agent-defined and standardizes nothing, so this is an
            # opaque passthrough. Safety comes from our permission handler, never
            # from the name of a mode.
            await conn.request(
                "session/set_mode",
                {"sessionId": session_id, "modeId": mode},
                timeout=30.0,
            )
            client.current_mode = mode
        for config_id, value_id in (getattr(cfg, "config_options", None) or {}).items():
            # UNVERIFIED: the exact param names for session/set_config_option are
            # taken from the method's documented shape and have not been checked
            # against a running harness.
            await conn.request(
                "session/set_config_option",
                {
                    "sessionId": session_id,
                    "configId": str(config_id),
                    "valueId": str(value_id),
                },
                timeout=30.0,
            )

        _touch()
        prompt_task = asyncio.ensure_future(
            conn.request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": message}],
                },
            )
        )
        stop_reason = await _await_prompt(
            conn,
            client,
            prompt_task,
            session_id=session_id,
            deadline=started + timeout,
            idle_timeout=idle_timeout,
            last_activity=last_activity,
            cancel_check=cancel_check,
        )
        result.stop_reason = stop_reason

        if isinstance(available_modes, dict) or client.current_mode:
            result.modes = {
                "current": client.current_mode or _current_mode_of(available_modes),
                "available": _available_modes_of(available_modes),
            }
    except AcpError as exc:
        # stderr is where a harness explains itself when it dies. Give the
        # drain a moment to reach EOF so a crash arrives with its diagnosis
        # attached rather than an empty tail.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(asyncio.shield(stderr_task), timeout=0.5)
        exc.stderr_tail = bytes(stderr_buf).decode("utf-8", "replace")
        raise
    finally:
        # Synchronous cancels first: they always happen, even if an await in
        # here is cut short because the caller is being cancelled.
        stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await conn.close()
        await _reap(proc)

    result.text = "".join(client.text_parts)
    result.thoughts = "".join(client.thought_parts)
    result.tool_calls = client.tool_calls
    result.permissions = client.permissions
    result.usage = client.usage
    result.plan = client.plan
    result.stderr_tail = bytes(stderr_buf).decode("utf-8", "replace")

    max_chars = int(getattr(cfg, "max_output_chars", 0) or 0)
    if max_chars > 0 and len(result.text) > max_chars:
        result.text = result.text[:max_chars]
        result.truncated = True
    if max_chars > 0 and len(result.thoughts) > max_chars:
        result.thoughts = result.thoughts[:max_chars]
        result.truncated = True
    return result


async def _await_prompt(
    conn: _Connection,
    client: SandcastleAcpClient,
    prompt_task: asyncio.Future,
    *,
    session_id: str,
    deadline: float,
    idle_timeout: float,
    last_activity: list[float],
    cancel_check: Callable[[], Awaitable[bool]] | None,
) -> str:
    """Wait for the turn, cancelling it gracefully when we must stop it.

    Graceful is the point. ``session/cancel`` asks the agent to wind down and
    answer ``stopReason: "cancelled"``; killing the process instead leaves a
    half-applied edit with nobody to tell. None of the four existing agent
    integrations can do this, because none of them has a channel back into a
    running turn.
    """
    reason = ""
    outer_cancelled = False
    while True:
        try:
            outcome = await asyncio.wait_for(
                asyncio.shield(prompt_task), timeout=_WATCHDOG_TICK_SECONDS
            )
        except asyncio.TimeoutError:
            now = time.monotonic()
            if now >= deadline:
                reason = "timeout"
            elif idle_timeout > 0 and (now - last_activity[0]) >= idle_timeout:
                reason = "idle"
            elif cancel_check is not None:
                try:
                    if await cancel_check():
                        reason = "cancelled"
                except Exception as exc:  # noqa: BLE001 - a flaky flag store must not kill a turn
                    logger.debug("ACP cancel_check failed: %s", exc)
            if not reason:
                continue
            break
        except asyncio.CancelledError:
            # The outer task went away (an approval pause cancelling siblings,
            # or the whole run being torn down). Still wind the agent down - but
            # the CancelledError has to reach the caller intact, because the
            # effect ledger reads "cancelled mid-flight" as "we do not know
            # whether this landed" rather than as a clean failure.
            outer_cancelled = True
            reason = "cancelled"
            break
        else:
            outcome = outcome if isinstance(outcome, dict) else {}
            stop_reason = str(outcome.get("stopReason") or "")
            if stop_reason not in STOP_REASONS:
                raise AcpError(
                    "protocol",
                    f"ACP agent returned an unknown stopReason {stop_reason!r}",
                )
            return stop_reason

    client.cancelling = True
    with contextlib.suppress(Exception):
        await conn.notify("session/cancel", {"sessionId": session_id})
    try:
        outcome = await asyncio.wait_for(
            asyncio.shield(prompt_task), timeout=_CANCEL_GRACE_SECONDS
        )
        if isinstance(outcome, dict) and outcome.get("stopReason") == "cancelled":
            logger.info("ACP turn cancelled cleanly (%s)", reason)
    except (asyncio.CancelledError, Exception):
        prompt_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await prompt_task

    if outer_cancelled:
        raise asyncio.CancelledError()
    if reason == "timeout":
        raise AcpError("timeout", "ACP turn exceeded its timeout")
    if reason == "idle":
        raise AcpError(
            "idle",
            f"ACP agent sent nothing for {idle_timeout:.0f}s (idle_timeout)",
        )
    raise AcpError("cancelled", "ACP turn cancelled")


async def _drain_stderr(proc: asyncio.subprocess.Process, buf: bytearray) -> None:
    """Keep the last few KB of the harness's stderr for the failure message."""
    stream = proc.stderr
    if stream is None:
        return
    while True:
        try:
            chunk = await stream.read(4096)
        except Exception:  # noqa: BLE001 - pipe closed under us
            return
        if not chunk:
            return
        buf.extend(chunk)
        if len(buf) > _STDERR_TAIL_BYTES:
            del buf[: len(buf) - _STDERR_TAIL_BYTES]


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Terminate, then kill. A surviving harness keeps billing somebody.

    Written to survive being run while the calling task is *already* being
    cancelled, which is the case that matters: an await in that state can raise
    CancelledError immediately, so the signals are sent from a ``finally`` that
    runs whether or not the waits get a chance to complete.
    """
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=_TERMINATE_GRACE_SECONDS)
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(
                    asyncio.shield(proc.wait()), timeout=_TERMINATE_GRACE_SECONDS
                )


def _client_info() -> dict:
    from sandcastle import __version__

    return {"name": "sandcastle", "title": "Sandcastle", "version": __version__}


def _current_mode_of(modes: Any) -> str:
    if isinstance(modes, dict):
        return str(modes.get("currentModeId") or "")
    return ""


def _available_modes_of(modes: Any) -> list[str]:
    if not isinstance(modes, dict):
        return []
    available = modes.get("availableModes")
    if not isinstance(available, list):
        return []
    out: list[str] = []
    for entry in available:
        if isinstance(entry, dict) and entry.get("id"):
            out.append(str(entry["id"]))
        elif isinstance(entry, str):
            out.append(entry)
    return out
