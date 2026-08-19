#!/usr/bin/env python3
"""A fake ACP agent: newline-delimited JSON-RPC 2.0 on stdio, one mode per behaviour.

Deliberately hand-rolled rather than built on any SDK. The point of a fake
server here is not only to give the tests something to talk to - it is to pin
our reading of the wire format from the *other* side. If Sandcastle's client and
this fixture ever agree on something the spec does not say, that is a bug we
want two independent implementations to disagree about.

Usage::

    python tests/fixtures/fake_acp_agent.py <mode>

``FAKE_ACP_LOG`` (env) names a file the agent appends JSON lines to, one per
notable event. That is how a test observes things the client never returns -
what outcome our permission answer carried, whether ``session/cancel`` arrived,
what error code came back for a probe, which env vars the harness was given.
"""

from __future__ import annotations

import json
import os
import sys
import time

PROTOCOL_VERSION = 1


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def log(event: str, **fields) -> None:
    path = os.environ.get("FAKE_ACP_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": event, **fields}) + "\n")
        fh.flush()


class Agent:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.next_id = 1000
        self.cancelled = False
        self.session_id = "sess_fake_1"

    # -- plumbing -----------------------------------------------------------

    def read(self) -> dict | None:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            return {}
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            log("client_sent_non_json", line=line[:200])
            return {}

    def call(self, method: str, params: dict, *, timeout: float = 10.0) -> dict:
        """Issue a request to the client and wait for its response.

        Notifications that arrive meanwhile (``session/cancel`` in particular)
        are handled inline, because a client is allowed to cancel while one of
        our requests is still outstanding.
        """
        self.next_id += 1
        msg_id = self.next_id
        send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.read()
            if message is None:
                return {"error": {"code": -1, "message": "client closed stdin"}}
            if not message:
                continue
            if message.get("id") == msg_id:
                return message
            self.handle_inbound(message)
        return {"error": {"code": -2, "message": "timed out waiting for client"}}

    def handle_inbound(self, message: dict) -> None:
        method = message.get("method")
        if method == "session/cancel":
            self.cancelled = True
            log("session_cancel", params=message.get("params"))

    # -- ACP methods --------------------------------------------------------

    def initialize(self, message: dict) -> None:
        params = message.get("params") or {}
        log(
            "initialize",
            protocolVersion=params.get("protocolVersion"),
            clientCapabilities=params.get("clientCapabilities"),
            clientInfo=params.get("clientInfo"),
            env_has_secret=bool(os.environ.get("FAKE_SECRET")),
            env_has_passthrough=bool(os.environ.get("FAKE_PASSTHROUGH")),
            env_keys=sorted(os.environ.keys()),
        )
        version = 99 if self.mode == "version-mismatch" else PROTOCOL_VERSION
        caps: dict = {}
        if self.mode == "extra-dirs":
            caps = {"sessionCapabilities": {"additionalDirectories": True}}
        send(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "protocolVersion": version,
                    "agentCapabilities": caps,
                    "agentInfo": {"name": "fake-acp-agent", "version": "0.0.1"},
                },
            }
        )

    def new_session(self, message: dict) -> None:
        params = message.get("params") or {}
        log("session_new", cwd=params.get("cwd"), mcpServers=params.get("mcpServers"),
            additionalDirectories=params.get("additionalDirectories"))
        if self.mode == "no-session":
            send({"jsonrpc": "2.0", "id": message.get("id"), "result": {}})
            return
        send(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "sessionId": self.session_id,
                    "modes": {
                        "currentModeId": "code",
                        "availableModes": [{"id": "ask"}, {"id": "code"}],
                    },
                },
            }
        )

    def chunk(self, text: str, *, thought: bool = False) -> None:
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": self.session_id,
                    "update": {
                        "sessionUpdate": "agent_thought_chunk" if thought else "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            }
        )

    def update(self, payload: dict) -> None:
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": self.session_id, "update": payload},
            }
        )

    def finish(self, message: dict, stop_reason: str) -> None:
        send({"jsonrpc": "2.0", "id": message.get("id"), "result": {"stopReason": stop_reason}})

    # -- the turn -----------------------------------------------------------

    def prompt(self, message: dict) -> None:
        params = message.get("params") or {}
        log("prompt", prompt=params.get("prompt"))
        mode = self.mode

        if mode in ("echo", "dirty-stdout", "extra-dirs"):
            self.chunk("Hello ")
            self.chunk("from ")
            self.chunk("the fake agent.")
            self.finish(message, "end_turn")
            return

        if mode == "thoughts":
            self.chunk("thinking hard", thought=True)
            self.chunk("answer")
            self.finish(message, "end_turn")
            return

        if mode == "json":
            self.chunk('{"verdict": ')
            self.chunk('"accept"}')
            self.finish(message, "end_turn")
            return

        if mode == "bad-json":
            self.chunk("not json at all")
            self.finish(message, "end_turn")
            return

        if mode == "long":
            self.chunk("x" * 5000)
            self.finish(message, "end_turn")
            return

        if mode == "refusal":
            self.chunk("I will not do that.")
            self.finish(message, "refusal")
            return

        if mode == "max-tokens":
            self.chunk("partial")
            self.finish(message, "max_tokens")
            return

        if mode == "bad-stop-reason":
            self.finish(message, "banana")
            return

        if mode == "tool-calls":
            self.update(
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc_1",
                    "title": "Write src/foo.py",
                    "kind": "edit",
                    "status": "pending",
                }
            )
            self.update(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tc_1",
                    "status": "completed",
                    "locations": [{"path": "/abs/src/foo.py"}],
                }
            )
            self.update({"sessionUpdate": "plan", "entries": [{"content": "step one"}]})
            self.chunk("done")
            self.finish(message, "end_turn")
            return

        if mode == "permission":
            reply = self.call(
                "session/request_permission",
                {
                    "sessionId": self.session_id,
                    "toolCall": {
                        "toolCallId": "tc_exec",
                        "title": "Run rm -rf /",
                        "kind": "execute",
                        "status": "pending",
                    },
                    # Deliberately non-obvious option ids: a client that matches
                    # on the id string instead of the kind gets this wrong.
                    "options": [
                        {"optionId": "opt-7a", "name": "Yes", "kind": "allow_once"},
                        {"optionId": "opt-9z", "name": "No", "kind": "reject_once"},
                    ],
                },
            )
            log("permission_reply", reply=reply)
            self.chunk("ok")
            self.finish(message, "end_turn")
            return

        if mode == "permission-edit":
            reply = self.call(
                "session/request_permission",
                {
                    "sessionId": self.session_id,
                    "toolCall": {
                        "toolCallId": "tc_edit",
                        "title": "Write src/foo.py",
                        "kind": "edit",
                        "status": "pending",
                    },
                    "options": [
                        {"optionId": "yy", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "nn", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            )
            log("permission_reply", reply=reply)
            self.chunk("edited")
            self.finish(message, "end_turn")
            return

        if mode == "fs-probe":
            target = os.environ.get("FAKE_ACP_FS_TARGET", "/etc/passwd")
            reply = self.call(
                "fs/read_text_file",
                {"sessionId": self.session_id, "path": target},
            )
            log("fs_read_reply", reply=reply)
            self.chunk("probed")
            self.finish(message, "end_turn")
            return

        if mode == "fs-write":
            target = os.environ.get("FAKE_ACP_FS_TARGET", "/tmp/acp-write-probe.txt")
            reply = self.call(
                "fs/write_text_file",
                {"sessionId": self.session_id, "path": target, "content": "written"},
            )
            log("fs_write_reply", reply=reply)
            self.chunk("probed")
            self.finish(message, "end_turn")
            return

        if mode == "terminal-probe":
            reply = self.call(
                "terminal/create",
                {"sessionId": self.session_id, "command": "id", "args": []},
            )
            log("terminal_reply", reply=reply)
            self.chunk("probed")
            self.finish(message, "end_turn")
            return

        if mode == "usage":
            self.update({"sessionUpdate": "usage_update", "used": 10000, "size": 200000})
            self.update(
                {
                    "sessionUpdate": "usage_update",
                    "used": 53000,
                    "size": 200000,
                    "cost": {"amount": 0.12, "currency": "USD"},
                }
            )
            self.chunk("billed")
            self.finish(message, "end_turn")
            return

        if mode == "usage-eur":
            self.update(
                {
                    "sessionUpdate": "usage_update",
                    "used": 53000,
                    "size": 200000,
                    "cost": {"amount": 0.99, "currency": "EUR"},
                }
            )
            self.chunk("billed in euros")
            self.finish(message, "end_turn")
            return

        if mode == "slow":
            time.sleep(float(os.environ.get("FAKE_ACP_SLEEP", "30")))
            self.finish(message, "end_turn")
            return

        if mode == "idle":
            self.chunk("one chunk then silence")
            time.sleep(float(os.environ.get("FAKE_ACP_SLEEP", "30")))
            self.finish(message, "end_turn")
            return

        if mode == "cancel":
            self.chunk("working")
            deadline = time.monotonic() + 30
            while not self.cancelled and time.monotonic() < deadline:
                incoming = self.read()
                if incoming is None:
                    return
                if incoming:
                    self.handle_inbound(incoming)
            # The spec requires a client to answer every *pending* permission
            # request with {"outcome": "cancelled"} once a turn is cancelled.
            reply = self.call(
                "session/request_permission",
                {
                    "sessionId": self.session_id,
                    "toolCall": {"toolCallId": "tc_late", "title": "Late ask", "kind": "edit"},
                    "options": [{"optionId": "a", "name": "Allow", "kind": "allow_once"}],
                },
                timeout=5.0,
            )
            log("cancel_permission_reply", reply=reply)
            self.finish(message, "cancelled")
            return

        if mode == "fail-once":
            # Fails on the first process, succeeds on the second. The marker
            # file makes "first" mean the first *attempt*, not the first prompt.
            marker = os.environ.get("FAKE_ACP_MARKER", "")
            if marker and not os.path.exists(marker):
                with open(marker, "w", encoding="utf-8") as fh:
                    fh.write("1")
                sys.stderr.write("fake agent: transient boom\n")
                sys.stderr.flush()
                raise SystemExit(1)
            self.chunk("second time lucky")
            self.finish(message, "end_turn")
            return

        self.chunk(f"unknown mode {mode}")
        self.finish(message, "end_turn")

    # -- main ---------------------------------------------------------------

    def run(self) -> None:
        if self.mode == "dirty-stdout":
            # Real CLIs do this: an npm banner, a login notice, a deprecation
            # warning - all on stdout, all before the handshake.
            sys.stdout.write("npm warn exec the following package was not found\n")
            sys.stdout.write("Fake ACP Agent v0.0.1 - starting\n")
            sys.stdout.flush()
        if self.mode == "crash":
            sys.stdout.write("<<<not json>>>\n")
            sys.stdout.flush()
            sys.stderr.write("fake agent: fatal, config missing\n")
            sys.stderr.flush()
            raise SystemExit(1)
        if self.mode == "junk":
            for i in range(500):
                sys.stdout.write(f"noise line {i}\n")
            sys.stdout.flush()
            time.sleep(30)
            return

        while True:
            message = self.read()
            if message is None:
                return
            if not message:
                continue
            method = message.get("method")
            if method == "initialize":
                self.initialize(message)
            elif method == "session/new":
                self.new_session(message)
            elif method == "session/set_mode":
                log("set_mode", params=message.get("params"))
                send({"jsonrpc": "2.0", "id": message.get("id"), "result": {}})
            elif method == "session/set_config_option":
                log("set_config_option", params=message.get("params"))
                send({"jsonrpc": "2.0", "id": message.get("id"), "result": {}})
            elif method == "session/prompt":
                self.prompt(message)
                return
            elif method == "session/cancel":
                self.handle_inbound(message)
            elif method is not None and "id" in message:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FAKE_ACP_MODE", "echo")
    try:
        Agent(mode).run()
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
