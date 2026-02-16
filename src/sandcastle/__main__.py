"""CLI entrypoint for `python -m sandcastle` / `sandcastle` command."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------

class _C:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"
    WHITE = "\033[97m"

    @staticmethod
    def supports_color() -> bool:
        """Check whether the terminal supports ANSI colors."""
        if os.getenv("NO_COLOR"):
            return False
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _color(text: str, color: str) -> str:
    """Wrap *text* with an ANSI color code if the terminal supports it."""
    if not _C.supports_color():
        return text
    return f"{color}{text}{_C.RESET}"


def _status_color(status: str) -> str:
    """Return a colorized status string."""
    s = status.lower()
    if s in ("completed", "success"):
        return _color(status, _C.GREEN)
    if s in ("failed", "error"):
        return _color(status, _C.RED)
    if s in ("running", "pending"):
        return _color(status, _C.YELLOW)
    if s in ("queued", "cancelled"):
        return _color(status, _C.GRAY)
    return status


# ---------------------------------------------------------------------------
# Simple table formatter
# ---------------------------------------------------------------------------

def _table(headers: list[str], rows: list[list[str]], *, max_col: int = 40) -> str:
    """Format a simple ASCII table with aligned columns.

    Each column is at most *max_col* characters wide.  Values longer than
    that are truncated with an ellipsis.
    """
    if not rows:
        return "(no data)"

    def _trunc(val: str) -> str:
        if len(val) > max_col:
            return val[: max_col - 1] + "\u2026"
        return val

    # Compute column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(_trunc(cell)))
    widths = [min(w, max_col) for w in widths]

    # Build lines
    lines: list[str] = []
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(_color(header_line, _C.BOLD))
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            cells.append(_trunc(cell).ljust(widths[i]))
        lines.append("  ".join(cells))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Client helper
# ---------------------------------------------------------------------------

def _get_client(args: argparse.Namespace) -> Any:
    """Create a SandcastleClient from parsed CLI arguments.

    Imports the SDK lazily so that commands like 'serve' and 'db migrate'
    never touch the SDK module.
    """
    from sandcastle.sdk import SandcastleClient  # lazy import

    url = getattr(args, "url", None) or os.getenv("SANDCASTLE_URL", "http://localhost:8080")
    api_key = getattr(args, "api_key", None) or os.getenv("SANDCASTLE_API_KEY", "")
    return SandcastleClient(base_url=url, api_key=api_key)


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    """Get attribute from a dict or object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ---------------------------------------------------------------------------
# Input parsing helpers
# ---------------------------------------------------------------------------

def _parse_input_pairs(pairs: list[str] | None) -> dict[str, Any]:
    """Parse KEY=VALUE pairs into a dict.

    Values that look like JSON are parsed as JSON; everything else stays a
    string.
    """
    if not pairs:
        return {}
    result: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            print(f"Error: invalid input format '{pair}' - expected KEY=VALUE", file=sys.stderr)
            sys.exit(1)
        key, _, value = pair.partition("=")
        # Try to parse JSON values (numbers, booleans, arrays, objects)
        try:
            result[key] = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            result[key] = value
    return result


def _load_input_file(path: str) -> dict[str, Any]:
    """Load workflow input data from a JSON file."""
    try:
        with open(path) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            print(f"Error: input file must contain a JSON object, got {type(data).__name__}",
                  file=sys.stderr)
            sys.exit(1)
        return data
    except FileNotFoundError:
        print(f"Error: input file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in input file: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Spinner / progress
# ---------------------------------------------------------------------------

def _wait_for_run(client: Any, run_id: str) -> dict[str, Any]:
    """Poll until a run reaches a terminal state, showing a simple spinner."""
    frames = ["|", "/", "-", "\\"]
    idx = 0
    terminal = {"completed", "failed", "cancelled", "error"}
    while True:
        run = client.get_run(run_id)
        status = _attr(run, "status", "unknown")
        if status in terminal:
            # Clear spinner line
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()
            return _to_dict(run)
        # Show spinner
        frame = frames[idx % len(frames)]
        label = _status_color(status)
        msg = f"\r  {_color(frame, _C.CYAN)} Waiting for {run_id[:12]}... [{label}]"
        sys.stdout.write(msg)
        sys.stdout.flush()
        idx += 1
        time.sleep(1.5)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_init(args: argparse.Namespace) -> None:
    """Interactive setup wizard - create .env and workflows directory."""
    from pathlib import Path

    env_path = Path(".env")

    print()
    print(_color("  Sandcastle Setup", _C.BOLD))
    print(_color("  ================", _C.BOLD))
    print()

    # Check if .env already exists
    if env_path.exists():
        answer = input(
            _color("  .env already exists. Overwrite? [y/N]: ", _C.YELLOW)
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("  Aborted.")
            return

    # Collect API keys
    print(_color("  Get keys at:", _C.DIM))
    print(_color("    Anthropic: https://console.anthropic.com/", _C.DIM))
    print(_color("    E2B:       https://e2b.dev/", _C.DIM))
    print()
    anthropic_key = input("  ANTHROPIC_API_KEY: ").strip()
    if not anthropic_key:
        print(
            _color("  Error: ANTHROPIC_API_KEY is required.", _C.RED),
            file=sys.stderr,
        )
        sys.exit(1)

    e2b_key = input("  E2B_API_KEY (optional, Enter to skip): ").strip()

    # Determine Sandstorm port
    sandstorm_port = getattr(args, "sandstorm_port", 3001)

    # Write .env
    lines = [
        f"ANTHROPIC_API_KEY={anthropic_key}",
        f"E2B_API_KEY={e2b_key}" if e2b_key else "# E2B_API_KEY=",
        f"SANDSTORM_URL=http://localhost:{sandstorm_port}",
        "",
        "# Local mode (SQLite + in-process queue) - leave empty",
        "DATABASE_URL=",
        "REDIS_URL=",
    ]
    env_path.write_text("\n".join(lines) + "\n")

    # Create workflows directory
    wf_dir = Path("workflows")
    wf_dir.mkdir(exist_ok=True)

    print()
    print(_color("  .env created", _C.GREEN))
    print(_color("  Run: sandcastle serve", _C.CYAN))
    print()


def _cmd_serve(args: argparse.Namespace) -> None:
    """Start the Sandcastle API server (with optional Sandstorm auto-start)."""
    import atexit
    import signal
    import subprocess

    import uvicorn

    sandstorm_proc = None

    # Load .env if present so we can read SANDSTORM_URL
    from sandcastle.config import settings

    sandstorm_url = settings.sandstorm_url

    # Check if Sandstorm is already running
    sandstorm_running = False
    try:
        import urllib.request

        req = urllib.request.Request(f"{sandstorm_url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            sandstorm_running = True
    except Exception:
        pass

    if not sandstorm_running:
        # Try to start Sandstorm
        # Extract port from URL
        try:
            from urllib.parse import urlparse

            parsed = urlparse(sandstorm_url)
            ds_port = str(parsed.port or 3001)
        except Exception:
            ds_port = "3001"

        try:
            sandstorm_proc = subprocess.Popen(
                ["ds", "serve", "--port", ds_port],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(
                _color(f"  Started Sandstorm on port {ds_port}", _C.GREEN)
            )
        except FileNotFoundError:
            print(
                _color(
                    "  Sandstorm not found. Install: pipx install duvo-sandstorm",
                    _C.YELLOW,
                )
            )

    # Cleanup Sandstorm on exit
    def _cleanup():
        if sandstorm_proc and sandstorm_proc.poll() is None:
            sandstorm_proc.terminate()
            try:
                sandstorm_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sandstorm_proc.kill()

    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(0)))

    uvicorn.run(
        "sandcastle.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def _cmd_run(args: argparse.Namespace) -> None:
    """Run a workflow via the SDK client."""
    client = _get_client(args)

    # Build input data
    input_data: dict[str, Any] = {}
    if args.input_file:
        input_data = _load_input_file(args.input_file)
    # --input pairs override / merge with file data
    input_data.update(_parse_input_pairs(args.input))

    try:
        run = client.run(
            args.workflow,
            input=input_data,
            wait=False,
            max_cost=args.max_cost,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    run_id = _attr(run, "run_id", str(run))

    if args.wait:
        result = _wait_for_run(client, run_id)
        _print_run_detail(result)
        status = _attr(result, "status", "unknown")
        if status == "failed":
            sys.exit(2)
    else:
        # Quick JSON output for scripting
        print(json.dumps({"run_id": run_id}))


def _cmd_status(args: argparse.Namespace) -> None:
    """Show status of a specific run."""
    client = _get_client(args)
    try:
        run = client.get_run(args.run_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_run_detail(run)


def _cmd_cancel(args: argparse.Namespace) -> None:
    """Cancel a running workflow."""
    client = _get_client(args)
    try:
        client.cancel_run(args.run_id)
        print(f"Cancelled run {args.run_id}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_logs(args: argparse.Namespace) -> None:
    """Stream SSE events for a run."""
    client = _get_client(args)
    try:
        for event in client.stream(args.run_id):
            event_type = _attr(event, "event", "message")
            data = _attr(event, "data", event)
            ts = time.strftime("%H:%M:%S")
            print(f"{_color(ts, _C.DIM)} [{_color(str(event_type), _C.CYAN)}] {data}")

            if not args.follow:
                status = None
                if isinstance(data, dict):
                    status = data.get("status")
                elif isinstance(data, str):
                    try:
                        parsed = json.loads(data)
                        status = parsed.get("status") if isinstance(parsed, dict) else None
                    except (json.JSONDecodeError, ValueError):
                        pass
                if status in ("completed", "failed", "cancelled", "error"):
                    break
    except KeyboardInterrupt:
        print("\nStream interrupted.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_ls(args: argparse.Namespace) -> None:
    """List runs, workflows, or schedules."""
    resource = args.resource
    client = _get_client(args)

    try:
        if resource == "runs":
            _ls_runs(client, args)
        elif resource == "workflows":
            _ls_workflows(client)
        elif resource == "schedules":
            _ls_schedules(client)
        else:
            print(f"Unknown resource: {resource}. Use: runs, workflows, schedules", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _ls_runs(client: Any, args: argparse.Namespace) -> None:
    """List runs with optional status filter."""
    status_filter = getattr(args, "status", None)
    limit = getattr(args, "limit", 20)
    runs = client.list_runs(status=status_filter, limit=limit)

    # Normalize to list of dicts
    items = _to_dicts(runs)
    if not items:
        print("No runs found.")
        return

    headers = ["RUN ID", "WORKFLOW", "STATUS", "COST ($)", "STARTED"]
    rows: list[list[str]] = []
    for r in items:
        rows.append([
            r.get("run_id", "")[:12],
            r.get("workflow_name", ""),
            _status_color(r.get("status", "")),
            f"{r.get('total_cost_usd', 0):.4f}",
            _fmt_time(r.get("started_at")),
        ])
    print(_table(headers, rows))


def _ls_workflows(client: Any) -> None:
    """List available workflows."""
    workflows = client.list_workflows()
    items = _to_dicts(workflows)
    if not items:
        print("No workflows found.")
        return

    headers = ["NAME", "DESCRIPTION", "STEPS"]
    rows: list[list[str]] = []
    for w in items:
        rows.append([
            w.get("name", ""),
            w.get("description", ""),
            str(w.get("steps_count", "")),
        ])
    print(_table(headers, rows))


def _ls_schedules(client: Any) -> None:
    """List active schedules."""
    schedules = client.list_schedules()
    items = _to_dicts(schedules)
    if not items:
        print("No schedules found.")
        return

    headers = ["ID", "WORKFLOW", "CRON", "ENABLED", "LAST RUN"]
    rows: list[list[str]] = []
    for s in items:
        enabled = _color("yes", _C.GREEN) if s.get("enabled") else _color("no", _C.RED)
        rows.append([
            s.get("id", "")[:12],
            s.get("workflow_name", ""),
            s.get("cron_expression", ""),
            enabled,
            s.get("last_run_id", "-") or "-",
        ])
    print(_table(headers, rows))


def _cmd_schedule_create(args: argparse.Namespace) -> None:
    """Create a new schedule."""
    client = _get_client(args)
    input_data = _parse_input_pairs(args.input)
    try:
        schedule = client.create_schedule(args.workflow, args.cron, input=input_data)
        sid = _attr(schedule, "id", str(schedule))
        print(f"Schedule created: {sid}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_schedule_delete(args: argparse.Namespace) -> None:
    """Delete a schedule."""
    client = _get_client(args)
    try:
        client.delete_schedule(args.id)
        print(f"Schedule deleted: {args.id}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_db_migrate(args: argparse.Namespace) -> None:
    """Run Alembic database migrations (PostgreSQL only)."""
    _run_migrations()


def _cmd_health(args: argparse.Namespace) -> None:
    """Check the API server health."""
    client = _get_client(args)
    try:
        h = client.health()
        data = _to_dict(h)
        status = data.get("status", "unknown")
        print(f"Status:    {_status_color(status)}")
        print(f"Sandstorm: {'ok' if data.get('sandstorm') else 'unreachable'}")
        if data.get("redis") is not None:
            print(f"Redis:     {'ok' if data.get('redis') else 'unreachable'}")
        print(f"Database:  {'ok' if data.get('database') else 'unreachable'}")
        if status not in ("ok", "healthy"):
            sys.exit(1)
    except Exception as exc:
        print(f"Error: cannot reach API - {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _to_dicts(data: Any) -> list[dict[str, Any]]:
    """Normalize an API response to a list of plain dicts."""
    if isinstance(data, dict):
        # Might be wrapped in {"data": [...]}
        if "data" in data and isinstance(data["data"], list):
            return [_to_dict(i) for i in data["data"]]
        return [data]
    if isinstance(data, list):
        return [_to_dict(i) for i in data]
    return []


def _to_dict(obj: Any) -> dict[str, Any]:
    """Coerce an object to a dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return {"value": str(obj)}


def _fmt_time(val: Any) -> str:
    """Format a datetime value for display."""
    if val is None:
        return "-"
    if isinstance(val, str):
        # Trim to minutes
        return val[:16].replace("T", " ")
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d %H:%M")
    return str(val)


def _print_run_detail(run: Any) -> None:
    """Pretty-print a full run with step details."""
    r = _to_dict(run)

    print()
    print(f"  {_color('Run', _C.BOLD)}:      {r.get('run_id', '?')}")
    print(f"  {_color('Workflow', _C.BOLD)}:  {r.get('workflow_name', '?')}")
    print(f"  {_color('Status', _C.BOLD)}:    {_status_color(r.get('status', 'unknown'))}")
    print(f"  {_color('Cost', _C.BOLD)}:      ${r.get('total_cost_usd', 0):.4f}")
    print(f"  {_color('Started', _C.BOLD)}:   {_fmt_time(r.get('started_at'))}")
    print(f"  {_color('Completed', _C.BOLD)}: {_fmt_time(r.get('completed_at'))}")

    if r.get("error"):
        print(f"  {_color('Error', _C.RED)}:     {r['error']}")

    # Steps table
    steps = r.get("steps")
    if steps:
        print()
        headers = ["STEP", "STATUS", "COST ($)", "DURATION (s)", "ATTEMPT"]
        rows: list[list[str]] = []
        for s in steps:
            s = _to_dict(s)
            rows.append([
                s.get("step_id", ""),
                _status_color(s.get("status", "")),
                f"{s.get('cost_usd', 0):.4f}",
                f"{s.get('duration_seconds', 0):.1f}",
                str(s.get("attempt", 1)),
            ])
        print(_table(headers, rows))

    # Outputs
    outputs = r.get("outputs")
    if outputs:
        print()
        print(_color("  Outputs:", _C.BOLD))
        print(json.dumps(outputs, indent=2, default=str))

    print()


# ---------------------------------------------------------------------------
# Migrations (preserved from original)
# ---------------------------------------------------------------------------

def _run_migrations() -> None:
    """Run Alembic migrations (PostgreSQL only)."""
    from sandcastle.config import settings

    if settings.is_local_mode:
        print("Migrations are not needed in local mode (SQLite tables are created automatically).")
        return

    try:
        from alembic.config import Config

        from alembic import command

        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        print("Migrations applied successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    """Add --url and --api-key arguments to a subparser."""
    parser.add_argument(
        "--url",
        default=None,
        help="Sandcastle API URL (default: $SANDCASTLE_URL or http://localhost:8080)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        dest="api_key",
        help="API key for authentication (default: $SANDCASTLE_API_KEY)",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the full CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="sandcastle",
        description="Sandcastle - workflow orchestrator CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- init ---
    subparsers.add_parser("init", help="Interactive setup wizard (create .env)")

    # --- serve ---
    p_serve = subparsers.add_parser("serve", help="Start the API server")
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    p_serve.add_argument("--reload", action="store_true", default=True,
                         help="Enable auto-reload (default: on)")
    p_serve.add_argument("--no-reload", action="store_false", dest="reload",
                         help="Disable auto-reload")

    # --- run ---
    p_run = subparsers.add_parser("run", help="Run a workflow")
    p_run.add_argument("workflow", help="Workflow name or path to .yaml file")
    p_run.add_argument("--input", "-i", action="append", metavar="KEY=VALUE",
                       help="Input key=value pair (repeatable)")
    p_run.add_argument("--input-file", "-f", metavar="FILE",
                       help="JSON file with input data")
    p_run.add_argument("--wait", "-w", action="store_true",
                       help="Wait for completion and print result")
    p_run.add_argument("--max-cost", type=float, default=None, metavar="USD",
                       help="Maximum cost limit in USD")
    _add_connection_args(p_run)

    # --- status ---
    p_status = subparsers.add_parser("status", help="Show run status and step details")
    p_status.add_argument("run_id", help="Run ID to check")
    _add_connection_args(p_status)

    # --- cancel ---
    p_cancel = subparsers.add_parser("cancel", help="Cancel a running workflow")
    p_cancel.add_argument("run_id", help="Run ID to cancel")
    _add_connection_args(p_cancel)

    # --- logs ---
    p_logs = subparsers.add_parser("logs", help="Stream run events (SSE)")
    p_logs.add_argument("run_id", help="Run ID to stream")
    p_logs.add_argument("--follow", "-f", action="store_true",
                        help="Keep streaming after terminal state")
    _add_connection_args(p_logs)

    # --- ls ---
    p_ls = subparsers.add_parser("ls", help="List resources")
    ls_sub = p_ls.add_subparsers(dest="resource", help="Resource type")

    p_ls_runs = ls_sub.add_parser("runs", help="List runs")
    p_ls_runs.add_argument("--status", "-s", default=None,
                           help="Filter by status (queued, running, completed, failed)")
    p_ls_runs.add_argument("--limit", "-n", type=int, default=20,
                           help="Max number of results (default: 20)")
    _add_connection_args(p_ls_runs)

    p_ls_wf = ls_sub.add_parser("workflows", help="List available workflows")
    _add_connection_args(p_ls_wf)

    p_ls_sched = ls_sub.add_parser("schedules", help="List schedules")
    _add_connection_args(p_ls_sched)

    # --- schedule ---
    p_sched = subparsers.add_parser("schedule", help="Manage schedules")
    sched_sub = p_sched.add_subparsers(dest="schedule_action", help="Schedule action")

    p_sched_create = sched_sub.add_parser("create", help="Create a new schedule")
    p_sched_create.add_argument("workflow", help="Workflow name")
    p_sched_create.add_argument("cron", help="Cron expression (e.g. '0 9 * * *')")
    p_sched_create.add_argument("--input", "-i", action="append", metavar="KEY=VALUE",
                                help="Input key=value pair (repeatable)")
    _add_connection_args(p_sched_create)

    p_sched_delete = sched_sub.add_parser("delete", help="Delete a schedule")
    p_sched_delete.add_argument("id", help="Schedule ID to delete")
    _add_connection_args(p_sched_delete)

    # --- db ---
    p_db = subparsers.add_parser("db", help="Database management")
    db_sub = p_db.add_subparsers(dest="db_action", help="Database action")
    db_sub.add_parser("migrate", help="Run Alembic migrations (PostgreSQL only)")

    # --- health ---
    p_health = subparsers.add_parser("health", help="Check API health")
    _add_connection_args(p_health)

    return parser


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Route CLI commands."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        # No command given - default to serve (backwards compatible)
        parser.print_help()
        sys.exit(0)

    dispatch: dict[str, Any] = {
        "init": _cmd_init,
        "serve": _cmd_serve,
        "run": _cmd_run,
        "status": _cmd_status,
        "cancel": _cmd_cancel,
        "logs": _cmd_logs,
        "ls": _cmd_ls,
        "health": _cmd_health,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
        return

    # Sub-commands that need further routing
    if args.command == "db":
        if getattr(args, "db_action", None) == "migrate":
            _cmd_db_migrate(args)
        else:
            print("Usage: sandcastle db migrate", file=sys.stderr)
            sys.exit(1)
        return

    if args.command == "schedule":
        action = getattr(args, "schedule_action", None)
        if action == "create":
            _cmd_schedule_create(args)
        elif action == "delete":
            _cmd_schedule_delete(args)
        else:
            print("Usage: sandcastle schedule {create,delete}", file=sys.stderr)
            sys.exit(1)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
