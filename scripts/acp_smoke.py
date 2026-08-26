#!/usr/bin/env python3
"""Run one ACP turn against a real agent harness and print what came back.

Deliberately outside pytest: this needs `npx`, network access and the harness's
own credentials, none of which belong in CI. It exists so the 0.45
definition-of-done item ("a recorded manual smoke run against a real registry
agent") can be performed and pasted into ``docs/acp.md``.

    SANDCASTLE_ACP_ALLOWED_ROOTS='["/srv/checkouts"]' \\
      python scripts/acp_smoke.py --agent claude --cwd /srv/checkouts/myrepo \\
      --message "List the files in this directory and stop."

Two things worth reading in the output, because both are marked UNVERIFIED in
docs/acp.md:

  * ``usage``  - does this harness report ``cost`` at all, and in which currency?
  * ``stderr`` - did it print anything to stdout before the handshake? (A banner
    is tolerated; the tolerance is a guess and this is how it gets checked.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sandcastle.engine.acp_client import (  # noqa: E402
    AcpError,
    build_acp_env,
    run_acp_turn,
)
from sandcastle.engine.dag import AcpConfig  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="", help="claude | codex | gemini | goose")
    parser.add_argument("--command", default="", help="explicit argv[0] instead of --agent")
    parser.add_argument("--arg", action="append", default=[], help="repeatable argv entry")
    parser.add_argument("--cwd", required=True, help="absolute workspace path")
    parser.add_argument("--message", required=True)
    parser.add_argument("--permission", default="reject")
    parser.add_argument("--filesystem", default="none", choices=["none", "read", "readwrite"])
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--idle-timeout", type=int, default=120)
    parser.add_argument(
        "--pass-env",
        action="append",
        default=["ANTHROPIC_API_KEY"],
        help="repeatable; parent env var names to forward to the harness",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    cfg = AcpConfig(
        agent=args.agent,
        command=args.command,
        args=list(args.arg),
        cwd=args.cwd,
        env_passthrough=[n for n in args.pass_env if os.environ.get(n)],
        permission=args.permission,
        filesystem=args.filesystem,
        timeout=args.timeout,
        idle_timeout=args.idle_timeout,
        output_format="full",
    )

    workspace = Path(args.cwd).resolve()
    if not workspace.is_dir():
        print(f"not a directory: {workspace}", file=sys.stderr)
        return 2

    def on_event(kind: str, payload: dict) -> None:
        if kind == "agent_message_chunk":
            sys.stderr.write(payload.get("text", ""))
            sys.stderr.flush()
        elif kind == "tool_call":
            sys.stderr.write(f"\n[tool_call] {payload.get('kind')} {payload.get('title')}\n")
        elif kind == "permission":
            sys.stderr.write(f"\n[permission] {payload}\n")

    try:
        turn = await run_acp_turn(
            cfg,
            args.message,
            workspace=workspace,
            env=build_acp_env(cfg),
            on_event=on_event,
        )
    except AcpError as exc:
        print(f"\nACP failed ({exc.kind}): {exc}", file=sys.stderr)
        if exc.stderr_tail:
            print(f"--- agent stderr tail ---\n{exc.stderr_tail}", file=sys.stderr)
        return 1

    print("\n=== result ===")
    print(
        json.dumps(
            {
                "stop_reason": turn.stop_reason,
                "session_id": turn.session_id,
                "agent": turn.agent_info,
                "protocol_version": turn.protocol_version,
                "modes": turn.modes,
                "usage": turn.usage,
                "permissions": turn.permissions,
                "tool_calls": turn.tool_calls,
                "truncated": turn.truncated,
                "text": turn.text,
            },
            indent=2,
        )
    )
    if turn.stderr_tail:
        print("\n=== agent stderr tail ===")
        print(turn.stderr_tail)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
