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
            print(
                f"Error: input file must contain a JSON object, got {type(data).__name__}",
                file=sys.stderr,
            )
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
    terminal = {
        "completed", "failed", "cancelled", "error",
        "partial", "budget_exceeded", "awaiting_approval",
    }
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

    if not sys.stdin.isatty():
        print(
            "Error: 'sandcastle init' requires an interactive terminal.",
            file=sys.stderr,
        )
        sys.exit(1)

    env_path = Path(".env")

    print()
    print(_color("  Sandcastle Setup", _C.BOLD))
    print(_color("  ================", _C.BOLD))
    print()

    # Check if .env already exists
    if env_path.exists():
        answer = (
            input(_color("  .env already exists. Overwrite? [y/N]: ", _C.YELLOW)).strip().lower()
        )
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

    print()
    print(_color("  Multi-model support (all optional):", _C.DIM))
    print(_color("    MiniMax:    https://www.minimaxi.com/", _C.DIM))
    print(_color("    OpenAI:     https://platform.openai.com/", _C.DIM))
    print(_color("    OpenRouter: https://openrouter.ai/", _C.DIM))
    print()
    minimax_key = input("  MINIMAX_API_KEY (optional, Enter to skip): ").strip()
    openai_key = input("  OPENAI_API_KEY (optional, Enter to skip): ").strip()
    openrouter_key = input("  OPENROUTER_API_KEY (optional, Enter to skip): ").strip()

    # Sandbox backend selection
    print()
    print(_color("  Sandbox backend:", _C.DIM))
    print(_color("    e2b        - Cloud sandboxes (requires E2B_API_KEY)", _C.DIM))
    print(_color("    docker     - Local Docker containers", _C.DIM))
    print(_color("    cloudflare - Cloudflare edge sandboxes (requires CF Worker)", _C.DIM))
    print(_color("    local      - Direct subprocess (no isolation, dev only)", _C.DIM))
    print()
    sandbox_backend = input("  SANDBOX_BACKEND [e2b]: ").strip().lower() or "e2b"
    if sandbox_backend not in ("e2b", "docker", "local", "cloudflare"):
        print(
            _color(f"  Warning: unknown backend '{sandbox_backend}', using 'e2b'", _C.YELLOW),
        )
        sandbox_backend = "e2b"

    cf_worker_url = ""
    if sandbox_backend == "cloudflare":
        cf_worker_url = input("  CLOUDFLARE_WORKER_URL: ").strip()
        if not cf_worker_url:
            print(
                _color(
                    "  CLOUDFLARE_WORKER_URL is required - falling back to e2b",
                    _C.YELLOW,
                ),
            )
            sandbox_backend = "e2b"

    # License key (optional)
    print()
    print(_color("  License key (optional):", _C.DIM))
    print(_color("    Contact tom@pflanzer.cz for Pro/Enterprise keys", _C.DIM))
    print()
    license_key = input("  LICENSE_KEY (optional, Enter to skip): ").strip()

    # Write .env
    lines = [
        f"ANTHROPIC_API_KEY={anthropic_key}",
        f"E2B_API_KEY={e2b_key}" if e2b_key else "# E2B_API_KEY=",
        "",
        "# Sandbox backend: e2b | docker | local | cloudflare",
        f"SANDBOX_BACKEND={sandbox_backend}",
    ]
    if cf_worker_url:
        lines.append(f"CLOUDFLARE_WORKER_URL={cf_worker_url}")
    lines += [
        "",
        "# Multi-model provider keys (optional)",
        f"MINIMAX_API_KEY={minimax_key}" if minimax_key else "# MINIMAX_API_KEY=",
        f"OPENAI_API_KEY={openai_key}" if openai_key else "# OPENAI_API_KEY=",
        f"OPENROUTER_API_KEY={openrouter_key}" if openrouter_key else "# OPENROUTER_API_KEY=",
        "",
        "# License key (Pro/Enterprise)",
        f"LICENSE_KEY={license_key}" if license_key else "# LICENSE_KEY=",
        "",
        "# Local mode (SQLite + in-process queue) - leave empty",
        "DATABASE_URL=",
        "REDIS_URL=",
    ]
    env_path.write_text("\n".join(lines) + "\n")

    # Create default directories
    from sandcastle.config import _DEFAULT_DATA_DIR, _DEFAULT_WORKFLOWS_DIR

    Path(_DEFAULT_DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(_DEFAULT_WORKFLOWS_DIR).mkdir(parents=True, exist_ok=True)

    print()
    print(_color("  .env created", _C.GREEN))
    print(_color(f"  Data:      {_DEFAULT_DATA_DIR}", _C.DIM))
    print(_color(f"  Workflows: {_DEFAULT_WORKFLOWS_DIR}", _C.DIM))
    print(_color("  Run: sandcastle serve", _C.CYAN))
    print()


def _port_in_use(port: int) -> bool:
    """Check whether a TCP port is already bound on localhost."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_port(port: int) -> bool:
    """Kill the process occupying *port*. Returns True on success."""
    import signal
    import subprocess

    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = result.stdout.strip().split()
        if not pids:
            return False
        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
        # Give processes a moment to exit
        import time

        time.sleep(0.5)
        return not _port_in_use(port)
    except Exception:
        return False


def _cmd_serve(args: argparse.Namespace) -> None:
    """Start the Sandcastle API server."""
    import uvicorn

    port = args.port
    if _port_in_use(port):
        print(
            f"\n  {_color('Port ' + str(port) + ' is already in use.', _C.YELLOW)}"
        )
        try:
            answer = input("  Kill the existing process and restart? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)

        if answer in ("", "y", "yes"):
            if _kill_port(port):
                print(f"  {_color('Freed port ' + str(port), _C.GREEN)}\n")
            else:
                msg = f"Could not free port {port}. Try: sudo lsof -ti :{port} | xargs kill"
                print(f"  {_color(msg, _C.RED)}")
                sys.exit(1)
        else:
            print("  Aborted.")
            sys.exit(0)

    uvicorn.run(
        "sandcastle.main:app",
        host=args.host,
        port=port,
        reload=args.reload,
    )


def _cmd_run(args: argparse.Namespace) -> None:
    """Run a workflow via the SDK client."""
    from pathlib import Path

    client = _get_client(args)

    # Build input data
    input_data: dict[str, Any] = {}
    if args.input_file:
        input_data = _load_input_file(args.input_file)
    # --input pairs override / merge with file data
    input_data.update(_parse_input_pairs(args.input))

    # Detect if the argument is a file path or a workflow name
    workflow_path = Path(args.workflow)
    is_file = workflow_path.suffix in (".yaml", ".yml") and workflow_path.exists()

    try:
        if is_file:
            yaml_content = workflow_path.read_text()
            run = client.run_yaml(
                yaml_content,
                input=input_data,
                wait=False,
                max_cost_usd=args.max_cost,
            )
        else:
            run = client.run(
                args.workflow,
                input=input_data,
                wait=False,
                max_cost_usd=args.max_cost,
            )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    run_id = _attr(run, "run_id", str(run))

    if args.wait:
        result = _wait_for_run(client, run_id)
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_run_detail(result)
        status = _attr(result, "status", "unknown")
        if status in ("failed", "error", "budget_exceeded"):
            sys.exit(2)
    else:
        print(json.dumps({"run_id": run_id}))


def _cmd_status(args: argparse.Namespace) -> None:
    """Show status of a specific run."""
    client = _get_client(args)
    try:
        run = client.get_run(args.run_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(_to_dict(run), indent=2, default=str))
        return

    _print_run_detail(run)


def _cmd_cancel(args: argparse.Namespace) -> None:
    """Cancel a running workflow."""
    client = _get_client(args)
    try:
        result = client.cancel_run(args.run_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(
            result if isinstance(result, dict)
            else {"cancelled": True, "run_id": args.run_id},
            indent=2, default=str,
        ))
        return

    print(f"Cancelled run {args.run_id}")


def _cmd_logs(args: argparse.Namespace) -> None:
    """Stream SSE events for a run."""
    client = _get_client(args)
    terminal_statuses = {
        "completed", "failed", "cancelled", "error",
        "partial", "budget_exceeded", "awaiting_approval",
    }
    try:
        for event in client.stream(args.run_id):
            event_type = _attr(event, "_event", "message")
            ts = time.strftime("%H:%M:%S")
            # The event dict itself is the data (SDK puts event type in _event key)
            display = {k: v for k, v in event.items() if k != "_event"} if isinstance(event, dict) else event
            print(f"{_color(ts, _C.DIM)} [{_color(str(event_type), _C.CYAN)}] {display}")

            if not args.follow:
                status = None
                if isinstance(event, dict):
                    status = event.get("status")
                if status in terminal_statuses:
                    break
    except KeyboardInterrupt:
        print("\nStream interrupted.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_ls(args: argparse.Namespace) -> None:
    """List runs, workflows, or schedules."""
    resource = getattr(args, "resource", None)
    if not resource:
        print("Usage: sandcastle ls {runs,workflows,schedules}", file=sys.stderr)
        sys.exit(1)

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
        rows.append(
            [
                r.get("run_id", "")[:12],
                r.get("workflow_name", ""),
                _status_color(r.get("status", "")),
                f"{r.get('total_cost_usd', 0):.4f}",
                _fmt_time(r.get("started_at")),
            ]
        )
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
        rows.append(
            [
                w.get("name", ""),
                w.get("description", ""),
                str(w.get("steps_count", "")),
            ]
        )
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
        rows.append(
            [
                s.get("id", "")[:12],
                s.get("workflow_name", ""),
                s.get("cron_expression", ""),
                enabled,
                (s.get("last_run_id") or "-")[:12],
            ]
        )
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


def _cmd_worker(args: argparse.Namespace) -> None:
    """Start the arq background worker."""
    import subprocess

    print(_color("  Starting arq worker...", _C.CYAN))
    try:
        subprocess.run(
            [sys.executable, "-m", "arq", "sandcastle.queue.worker.WorkerSettings"],
            check=True,
        )
    except KeyboardInterrupt:
        print("\nWorker stopped.")
    except FileNotFoundError:
        print(
            _color("  Error: arq not found. Install: pip install arq", _C.RED),
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_mcp(args: argparse.Namespace) -> None:
    """Start the MCP (Model Context Protocol) server for desktop AI clients."""
    # Propagate CLI args to env vars so mcp_server.py picks them up
    url = getattr(args, "url", None)
    api_key = getattr(args, "api_key", None)
    if url:
        os.environ["SANDCASTLE_URL"] = url
    if api_key:
        os.environ["SANDCASTLE_API_KEY"] = api_key

    try:
        from sandcastle.mcp_server import main as mcp_main
    except ImportError:
        print(
            "Error: MCP support requires the 'mcp' package.\n"
            "Install it with: pip install sandcastle-ai[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    mcp_main()


def _cmd_doctor(args: argparse.Namespace) -> None:
    """Run local diagnostics - no running server needed."""
    import importlib
    import socket
    from pathlib import Path

    failures = 0

    def _pass(msg: str) -> None:
        print(f"  {_color('[PASS]', _C.GREEN)} {msg}")

    def _warn(msg: str) -> None:
        print(f"  {_color('[WARN]', _C.YELLOW)} {msg}")

    def _fail(msg: str) -> None:
        nonlocal failures
        failures += 1
        print(f"  {_color('[FAIL]', _C.RED)} {msg}")

    # --- Section 1: Configuration ---
    print()
    print(_color("  Configuration", _C.BOLD))
    print(_color("  -------------", _C.BOLD))

    env_path = Path(".env")
    if env_path.exists():
        _pass(".env file found")
    else:
        _warn(".env file not found (using defaults/environment)")

    try:
        from sandcastle.config import Settings

        cfg = Settings()
        _pass("Settings loaded successfully")
    except Exception as e:
        _fail(f"Settings failed to load: {e}")
        cfg = None

    if cfg:
        if cfg.anthropic_api_key:
            _pass(f"ANTHROPIC_API_KEY configured ({cfg.anthropic_api_key[:8]}...)")
        else:
            _fail("ANTHROPIC_API_KEY not set")

        if cfg.e2b_api_key:
            _pass(f"E2B_API_KEY configured ({cfg.e2b_api_key[:8]}...)")
        else:
            _warn("E2B_API_KEY not set (needed for e2b backend)")

    # --- Section 2: Providers & Models ---
    print()
    print(_color("  Providers & Models", _C.BOLD))
    print(_color("  ------------------", _C.BOLD))

    from sandcastle.engine.providers import (
        PROVIDER_REGISTRY,
        get_api_key,
        get_failover,
    )

    failover = get_failover()
    status = failover.get_status()

    for model_name, model_info in PROVIDER_REGISTRY.items():
        key = get_api_key(model_info)
        if key:
            avail = "available" if failover.is_available(model_info.api_key_env) else "on cooldown"
            _pass(f"{model_name} ({model_info.provider}) - {avail}")
        else:
            _warn(f"{model_name} ({model_info.provider}) - key not configured")

    if status["active_cooldowns"]:
        for env_key, remaining in status["active_cooldowns"].items():
            _warn(f"{env_key} on cooldown ({remaining}s remaining)")

    # --- Section 3: Sandbox Backend ---
    print()
    print(_color("  Sandbox Backend", _C.BOLD))
    print(_color("  ---------------", _C.BOLD))

    backend = cfg.sandbox_backend if cfg else "e2b"
    _pass(f"Backend: {backend}")

    if backend == "e2b":
        try:
            import e2b  # noqa: F401

            _pass("E2B SDK installed")
        except ImportError:
            _fail("E2B SDK not installed (pip install e2b)")
    elif backend == "docker":
        try:
            import aiodocker  # noqa: F401

            _pass("aiodocker installed")
        except ImportError:
            _fail("aiodocker not installed (pip install aiodocker)")
        import shutil

        if shutil.which("docker"):
            _pass("Docker CLI found")
        else:
            _warn("Docker CLI not found in PATH")
    elif backend == "local":
        import shutil

        if shutil.which("node"):
            _pass("Node.js found")
        else:
            _fail("Node.js not found in PATH (required for runners)")
    elif backend == "cloudflare":
        if cfg and cfg.cloudflare_worker_url:
            _pass(f"CF Worker URL: {cfg.cloudflare_worker_url}")
        else:
            _fail("CLOUDFLARE_WORKER_URL not configured")

    # --- Section 4: Dependencies ---
    print()
    print(_color("  Dependencies", _C.BOLD))
    print(_color("  ------------", _C.BOLD))

    required_pkgs = ["fastapi", "uvicorn", "httpx", "pydantic", "pydantic_settings", "httpx_sse"]
    optional_pkgs = {"mcp": "MCP server", "aiodocker": "Docker backend", "arq": "Redis worker"}

    for pkg in required_pkgs:
        try:
            importlib.import_module(pkg)
            _pass(f"{pkg}")
        except ImportError:
            _fail(f"{pkg} not installed")

    for pkg, desc in optional_pkgs.items():
        try:
            importlib.import_module(pkg)
            _pass(f"{pkg} ({desc})")
        except ImportError:
            _warn(f"{pkg} not installed ({desc})")

    # --- Section 5: Network ---
    print()
    print(_color("  Network", _C.BOLD))
    print(_color("  -------", _C.BOLD))

    port = 8080
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", port))
            if result != 0:
                _pass(f"Port {port} is available")
            else:
                _warn(f"Port {port} is already in use")
    except OSError:
        _pass(f"Port {port} is available")

    # --- Section 6: Telemetry ---
    print()
    print(_color("  Telemetry", _C.BOLD))
    print(_color("  ---------", _C.BOLD))

    if cfg and cfg.telemetry_enabled and cfg.sentry_dsn:
        try:
            importlib.import_module("sentry_sdk")
            _pass(f"Sentry DSN configured ({cfg.sentry_dsn[:20]}...)")
        except ImportError:
            _fail("sentry-sdk not installed (pip install sandcastle-ai[telemetry])")
    elif cfg and cfg.telemetry_enabled:
        _warn("TELEMETRY_ENABLED=true but SENTRY_DSN not set")
    else:
        _pass("Telemetry disabled (opt-in with TELEMETRY_ENABLED=true)")

    # --- Section 7: License ---
    print()
    print(_color("  License", _C.BOLD))
    print(_color("  -------", _C.BOLD))

    if cfg:
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        lic = validate_license_key(cfg.license_key)
        if lic.status == LicenseStatus.valid:
            _pass(
                f"{lic.tier.value.capitalize()} license - {lic.licensee}"
                + (f" (expires {lic.expires})" if lic.expires else "")
            )
        elif lic.status == LicenseStatus.missing:
            _warn("No license key (community mode)")
        elif lic.status == LicenseStatus.expired:
            _fail(f"License expired: {lic.detail}")
        else:
            _fail(f"License invalid: {lic.detail}")

    # --- Section 8: Directories ---
    print()
    print(_color("  Directories", _C.BOLD))
    print(_color("  -----------", _C.BOLD))

    if cfg:
        wf_dir = Path(cfg.workflows_dir)
        if wf_dir.exists():
            yamls = list(wf_dir.glob("*.yaml")) + list(wf_dir.glob("*.yml"))
            _pass(f"Workflows dir: {wf_dir} ({len(yamls)} workflow(s))")
        else:
            _warn(
                f"Workflows dir does not exist: {wf_dir}\n"
                "           Run 'sandcastle init' to create it"
            )

        data_path = Path(cfg.data_dir)
        if data_path.exists():
            _pass(f"Data dir: {data_path}")
        else:
            _warn(
                f"Data dir does not exist: {data_path}\n"
                "           Run 'sandcastle init' to create it"
            )

    # --- Summary ---
    print()
    if failures == 0:
        print(_color("  All checks passed!", _C.GREEN))
    else:
        print(_color(f"  {failures} check(s) failed", _C.RED))
    print()

    sys.exit(1 if failures else 0)


def _cmd_health(args: argparse.Namespace) -> None:
    """Check the API server health."""
    client = _get_client(args)
    try:
        h = client.health()
        data = _to_dict(h)
        status = data.get("status", "unknown")
        print(f"Status:    {_status_color(status)}")
        print(f"Runtime:   {'ok' if data.get('runtime') else 'not configured'}")
        if data.get("redis") is not None:
            print(f"Redis:     {'ok' if data.get('redis') else 'unreachable'}")
        print(f"Database:  {'ok' if data.get('database') else 'unreachable'}")
        if status not in ("ok", "healthy"):
            sys.exit(1)
    except Exception as exc:
        print(f"Error: cannot reach API - {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_generate(args: argparse.Namespace) -> None:
    """Generate a workflow from a natural language description."""
    from sandcastle.engine.generator import generate_workflow_sync

    description = getattr(args, "description", None) or ""
    if not description:
        try:
            description = input(f"{_color('Describe your workflow:', _C.CYAN)} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
    if not description:
        print("No description provided.", file=sys.stderr)
        sys.exit(1)

    output_file = getattr(args, "output", None)
    refine = getattr(args, "refine", False)

    # Generate
    _spinner_print("Generating workflow...")
    try:
        result = generate_workflow_sync(description)
    except ValueError as exc:
        print(f"\n  {_color('[FAIL]', _C.RED)} {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n  {_color('[FAIL]', _C.RED)} Generation failed: {exc}")
        sys.exit(1)

    if result.validation_errors:
        print(
            f"\r  {_color('[WARN]', _C.YELLOW)} Generated with "
            f"{len(result.validation_errors)} validation issue(s)"
        )
        for err in result.validation_errors:
            print(f"    - {err}")
    else:
        print(
            f'\r  {_color("[OK]", _C.GREEN)} Generated "{result.name}" ({result.steps_count} steps)'
        )

    # Refinement loop
    if refine:
        while True:
            try:
                instruction = input(f"\n{_color('Refine (empty to finish):', _C.CYAN)} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not instruction:
                break
            _spinner_print("Refining workflow...")
            try:
                result = generate_workflow_sync(
                    description,
                    refine_from=result.yaml_content,
                    refine_instruction=instruction,
                )
            except Exception as exc:
                print(f"\n  {_color('[FAIL]', _C.RED)} Refinement failed: {exc}")
                continue
            if result.validation_errors:
                print(
                    f"\r  {_color('[WARN]', _C.YELLOW)} Refined "
                    f"with {len(result.validation_errors)} issue(s)"
                )
            else:
                print(
                    f'\r  {_color("[OK]", _C.GREEN)} Refined '
                    f'"{result.name}" ({result.steps_count} steps)'
                )

    # Output
    if output_file:
        from pathlib import Path

        Path(output_file).write_text(result.yaml_content)
        print(f"\n  Saved to {output_file}")
    else:
        print(f"\n{_color('--- Generated YAML ---', _C.DIM)}")
        print(result.yaml_content)


def _cmd_eval(args: argparse.Namespace) -> None:
    """Run an eval suite against a workflow."""
    import asyncio

    from sandcastle.engine.eval import parse_eval_suite, run_eval_suite, save_eval_run

    suite_path = args.suite
    concurrency = getattr(args, "concurrency", 1)
    tags = getattr(args, "tag", None)
    verbose = getattr(args, "verbose", False)

    try:
        suite = parse_eval_suite(suite_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print()
    print(_color("  Eval Suite: ", _C.BOLD) + (suite.description or suite.workflow))
    print(_color("  Workflow:   ", _C.BOLD) + suite.workflow)
    case_count = len(suite.cases)
    if tags:
        tag_set = set(tags)
        case_count = sum(1 for c in suite.cases if tag_set.intersection(c.tags))
    print(_color("  Cases:      ", _C.BOLD) + str(case_count))
    print()

    _spinner_print("Running eval suite...")

    try:
        result = asyncio.run(run_eval_suite(suite, tag_filter=tags, concurrency=concurrency))
    except Exception as exc:
        print(f"\n  {_color('[FAIL]', _C.RED)} Eval suite failed: {exc}")
        sys.exit(1)

    # Clear spinner line
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

    _print_eval_results(result, verbose=verbose)

    # Try to save results to DB
    try:
        from pathlib import Path

        suite_yaml = Path(suite_path).read_text()
        asyncio.run(save_eval_run(result, suite_yaml=suite_yaml))
    except Exception:
        pass  # DB might not be available in CLI-only mode

    # Exit with non-zero if any case failed
    if result.failed > 0:
        sys.exit(1)


def _print_eval_results(result: Any, *, verbose: bool = False) -> None:
    """Print eval suite results as an ASCII table."""
    headers = ["CASE", "STATUS", "ASSERTIONS", "COST ($)", "DURATION (s)"]
    rows: list[list[str]] = []

    for case in result.cases:
        status = _color("PASS", _C.GREEN) if case.passed else _color("FAIL", _C.RED)
        passed_count = sum(1 for a in case.assertions if a.passed)
        total_count = len(case.assertions)
        assertion_str = f"{passed_count}/{total_count}"
        if total_count > 0 and passed_count < total_count:
            assertion_str = _color(assertion_str, _C.RED)
        elif total_count > 0:
            assertion_str = _color(assertion_str, _C.GREEN)

        rows.append(
            [
                case.name,
                status,
                assertion_str,
                f"{case.cost_usd:.4f}",
                f"{case.duration_seconds:.1f}",
            ]
        )

    print(_table(headers, rows))

    # Verbose: show assertion details for failed cases
    if verbose:
        for case in result.cases:
            if not case.passed:
                print()
                print(f"  {_color(case.name, _C.BOLD)} - assertions:")
                for a in case.assertions:
                    mark = _color("PASS", _C.GREEN) if a.passed else _color("FAIL", _C.RED)
                    msg = f"    [{mark}] {a.type}"
                    if a.message:
                        msg += f" - {a.message}"
                    print(msg)
                if case.error:
                    print(f"    {_color('Error:', _C.RED)} {case.error}")

    # Summary line
    print()
    pass_str = _color(str(result.passed), _C.GREEN) if result.passed > 0 else "0"
    fail_str = _color(str(result.failed), _C.RED) if result.failed > 0 else "0"
    rate_color = (
        _C.GREEN if result.pass_rate >= 0.8 else (_C.YELLOW if result.pass_rate >= 0.5 else _C.RED)
    )
    rate_str = _color(f"{result.pass_rate * 100:.0f}%", rate_color)

    print(
        f"  {result.total} cases | "
        f"{pass_str} passed | "
        f"{fail_str} failed | "
        f"{rate_str} pass rate | "
        f"${result.total_cost_usd:.4f} | "
        f"{result.total_duration_seconds:.1f}s"
    )
    print()


def _cmd_templates(args: argparse.Namespace) -> None:
    """List available workflow templates."""
    import httpx

    base = getattr(args, "url", None) or os.getenv("SANDCASTLE_URL", "http://localhost:8080")
    try:
        resp = httpx.get(f"{base}/api/templates", timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    body = resp.json()
    items = body.get("data", [])

    # Filter by category if requested
    category = getattr(args, "category", None)
    if category:
        items = [t for t in items if t.get("category", "").lower() == category.lower()]

    if getattr(args, "json", False):
        print(json.dumps(items, indent=2))
        return

    if not items:
        print("No templates found.")
        return

    headers = ["NAME", "CATEGORY", "STEPS", "DESCRIPTION"]
    rows: list[list[str]] = []
    for t in items:
        rows.append(
            [
                t.get("name", ""),
                t.get("category", ""),
                str(t.get("step_count", "")),
                t.get("description", ""),
            ]
        )
    print(_table(headers, rows))


def _cmd_replay(args: argparse.Namespace) -> None:
    """Replay a workflow run from a specific step."""
    import httpx

    base = getattr(args, "url", None) or os.getenv("SANDCASTLE_URL", "http://localhost:8080")
    api_key = getattr(args, "api_key", None) or os.getenv("SANDCASTLE_API_KEY", "")
    headers_dict: dict[str, str] = {}
    if api_key:
        headers_dict["Authorization"] = f"Bearer {api_key}"

    from_step = getattr(args, "from_step", None)
    if not from_step:
        print("Error: --from-step is required", file=sys.stderr)
        sys.exit(1)

    payload = {"from_step": from_step}

    try:
        resp = httpx.post(
            f"{base}/api/runs/{args.run_id}/replay",
            json=payload,
            headers=headers_dict,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    body = resp.json()
    data = body.get("data", {})

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return

    new_id = data.get("new_run_id", "?")
    status = data.get("status", "queued")
    print(f"Replay started: {new_id} [{_status_color(status)}]")
    print(f"  Parent: {data.get('parent_run_id', '?')}")
    print(f"  From step: {data.get('replay_from_step', '?')}")


def _cmd_fork(args: argparse.Namespace) -> None:
    """Fork a run with optional overrides."""
    import httpx

    base = getattr(args, "url", None) or os.getenv("SANDCASTLE_URL", "http://localhost:8080")
    api_key = getattr(args, "api_key", None) or os.getenv("SANDCASTLE_API_KEY", "")
    headers_dict: dict[str, str] = {}
    if api_key:
        headers_dict["Authorization"] = f"Bearer {api_key}"

    from_step = getattr(args, "from_step", None)
    if not from_step:
        print("Error: --from-step is required", file=sys.stderr)
        sys.exit(1)

    # Build changes dict from --change args
    changes: dict[str, Any] = {}
    change_args = getattr(args, "change", None)
    if change_args:
        for pair in change_args:
            if "=" not in pair:
                print(
                    f"Error: invalid change format '{pair}' - expected KEY=VALUE", file=sys.stderr
                )
                sys.exit(1)
            key, _, value = pair.partition("=")
            try:
                changes[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                changes[key] = value

    payload: dict[str, Any] = {"from_step": from_step}
    if changes:
        payload["changes"] = changes

    try:
        resp = httpx.post(
            f"{base}/api/runs/{args.run_id}/fork",
            json=payload,
            headers=headers_dict,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    body = resp.json()
    data = body.get("data", {})

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return

    new_id = data.get("new_run_id", "?")
    status = data.get("status", "queued")
    print(f"Fork started: {new_id} [{_status_color(status)}]")
    print(f"  Parent: {data.get('parent_run_id', '?')}")
    print(f"  From step: {data.get('fork_from_step', '?')}")
    if data.get("changes"):
        print(f"  Changes: {json.dumps(data['changes'])}")


def _cmd_approve(args: argparse.Namespace) -> None:
    """Approve a paused approval step."""
    import httpx

    base = getattr(args, "url", None) or os.getenv("SANDCASTLE_URL", "http://localhost:8080")
    api_key = getattr(args, "api_key", None) or os.getenv("SANDCASTLE_API_KEY", "")
    headers_dict: dict[str, str] = {}
    if api_key:
        headers_dict["Authorization"] = f"Bearer {api_key}"

    # The CLI accepts a run_id, but the API uses approval_id.
    # First, find the pending approval for this run.
    approval_id = _find_pending_approval(base, headers_dict, args.run_id)
    if not approval_id:
        print(f"Error: no pending approval found for run {args.run_id}", file=sys.stderr)
        sys.exit(1)

    payload: dict[str, Any] = {}
    data_arg = getattr(args, "data", None)
    if data_arg:
        try:
            payload["edited_data"] = json.loads(data_arg)
        except json.JSONDecodeError as exc:
            print(f"Error: invalid JSON for --data: {exc}", file=sys.stderr)
            sys.exit(1)

    try:
        resp = httpx.post(
            f"{base}/api/approvals/{approval_id}/approve",
            json=payload if payload else None,
            headers=headers_dict,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    body = resp.json()
    data = body.get("data", {})

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return

    print(f"Approved run {data.get('run_id', args.run_id)}")


def _cmd_reject(args: argparse.Namespace) -> None:
    """Reject a paused approval step."""
    import httpx

    base = getattr(args, "url", None) or os.getenv("SANDCASTLE_URL", "http://localhost:8080")
    api_key = getattr(args, "api_key", None) or os.getenv("SANDCASTLE_API_KEY", "")
    headers_dict: dict[str, str] = {}
    if api_key:
        headers_dict["Authorization"] = f"Bearer {api_key}"

    # Find the pending approval for this run
    approval_id = _find_pending_approval(base, headers_dict, args.run_id)
    if not approval_id:
        print(f"Error: no pending approval found for run {args.run_id}", file=sys.stderr)
        sys.exit(1)

    payload: dict[str, Any] = {}
    reason = getattr(args, "reason", None)
    if reason:
        payload["comment"] = reason

    try:
        resp = httpx.post(
            f"{base}/api/approvals/{approval_id}/reject",
            json=payload if payload else None,
            headers=headers_dict,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    body = resp.json()
    data = body.get("data", {})

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return

    print(f"Rejected run {data.get('run_id', args.run_id)}")
    if reason:
        print(f"  Reason: {reason}")


def _cmd_runs(args: argparse.Namespace) -> None:
    """List recent workflow runs."""
    import httpx

    base = getattr(args, "url", None) or os.getenv("SANDCASTLE_URL", "http://localhost:8080")
    api_key = getattr(args, "api_key", None) or os.getenv("SANDCASTLE_API_KEY", "")
    headers_dict: dict[str, str] = {}
    if api_key:
        headers_dict["Authorization"] = f"Bearer {api_key}"

    params: dict[str, Any] = {"limit": getattr(args, "limit", 20)}
    status_filter = getattr(args, "status", None)
    if status_filter:
        params["status"] = status_filter

    try:
        resp = httpx.get(
            f"{base}/api/runs",
            params=params,
            headers=headers_dict,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    body = resp.json()
    items = body.get("data", [])

    if getattr(args, "json", False):
        print(json.dumps(body, indent=2))
        return

    if not items:
        print("No runs found.")
        return

    headers = ["RUN ID", "WORKFLOW", "STATUS", "COST ($)", "STARTED"]
    rows: list[list[str]] = []
    for r in items:
        if isinstance(r, dict):
            rows.append(
                [
                    r.get("run_id", "")[:12],
                    r.get("workflow_name", ""),
                    _status_color(r.get("status", "")),
                    f"{r.get('total_cost_usd', 0):.4f}",
                    _fmt_time(r.get("started_at")),
                ]
            )
    print(_table(headers, rows))


def _find_pending_approval(base: str, headers: dict[str, str], run_id: str) -> str | None:
    """Find the pending approval_id for a given run_id by querying the API."""
    import httpx

    try:
        resp = httpx.get(
            f"{base}/api/approvals",
            params={"status": "pending"},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        return None

    body = resp.json()
    for item in body.get("data", []):
        if isinstance(item, dict) and item.get("run_id") == run_id:
            return item.get("id")
    return None


def _spinner_print(msg: str) -> None:
    """Print a message with a spinner-like prefix."""
    sys.stdout.write(f"  ... {msg}")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _to_dicts(data: Any) -> list[dict[str, Any]]:
    """Normalize an API response to a list of plain dicts."""
    # PaginatedList (from sdk.py) has an .items attribute
    if hasattr(data, "items") and isinstance(data.items, list):
        return [_to_dict(i) for i in data.items]
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
            rows.append(
                [
                    s.get("step_id", ""),
                    _status_color(s.get("status", "")),
                    f"{s.get('cost_usd', 0):.4f}",
                    f"{s.get('duration_seconds', 0):.1f}",
                    str(s.get("attempt", 1)),
                ]
            )
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
# Community Hub commands
# ---------------------------------------------------------------------------

_HUB_REGISTRY_URL = "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/registry.json"


def _fetch_hub_registry() -> dict:
    """Fetch the community hub registry."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(_HUB_REGISTRY_URL, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(
            f"{_color('Error', _C.RED)}: Failed to fetch hub registry: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_hub_search(args: argparse.Namespace) -> None:
    """Search community workflows by query."""
    registry = _fetch_hub_registry()
    templates = registry.get("templates", [])
    query = getattr(args, "query", "").strip().lower()

    if not query:
        print("Error: search query cannot be empty", file=sys.stderr)
        sys.exit(1)

    # Filter by query - match name, description, tags
    results = []
    for t in templates:
        name = t.get("name", "").lower()
        desc = t.get("description", "").lower()
        tags = [tag.lower() for tag in t.get("tags", [])]
        if query in name or query in desc or any(query in tag for tag in tags):
            results.append(t)

    # Filter by category
    category = getattr(args, "category", None)
    if category:
        results = [t for t in results if t.get("category", "").lower() == category.lower()]

    if getattr(args, "json", False):
        print(json.dumps(results, indent=2))
        return

    if not results:
        print(f"No workflows found for query '{query}'.")
        return

    headers = ["SLUG", "NAME", "CATEGORY", "STEPS", "AUTHOR"]
    rows: list[list[str]] = []
    for t in results:
        rows.append(
            [
                t.get("slug", ""),
                t.get("name", ""),
                t.get("category", ""),
                str(t.get("step_count", "")),
                t.get("author", ""),
            ]
        )
    print(_table(headers, rows))
    print(f"\n{_color(str(len(results)), _C.CYAN)} result(s) found.")


def _sanitize_hub_filename(slug: str) -> str:
    """Sanitize a hub slug to a safe filename (no path traversal)."""
    import re as _re

    # Take only the last component and strip any path separators
    base = slug.split("/")[-1]
    # Remove any character that is not alphanumeric, hyphen, or underscore
    safe = _re.sub(r"[^a-zA-Z0-9_\-]", "_", base)
    # Ensure it does not start with a dot (hidden file) or be empty
    safe = safe.lstrip(".")
    if not safe:
        safe = "workflow"
    return safe + ".yaml"


def _validate_hub_download_url(url: str) -> bool:
    """Validate that a hub download URL points to a trusted source."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    # Only allow downloads from GitHub raw content
    trusted_hosts = (
        "raw.githubusercontent.com",
        "github.com",
        "objects.githubusercontent.com",
    )
    return parsed.hostname in trusted_hosts


def _validate_hub_yaml(yaml_content: str) -> tuple[bool, str]:
    """Validate that downloaded hub YAML is safe and well-formed."""
    import yaml as _yaml

    # Size limit
    if len(yaml_content) > 512 * 1024:  # 512KB
        return False, "YAML content too large (max 512KB)"

    try:
        data = _yaml.safe_load(yaml_content)
    except _yaml.YAMLError as exc:
        return False, f"Invalid YAML: {exc}"

    if not isinstance(data, dict):
        return False, "YAML must be a mapping"

    if "name" not in data:
        return False, "YAML must contain a 'name' field"

    if "steps" not in data or not isinstance(data.get("steps"), list):
        return False, "YAML must contain a 'steps' list"

    return True, ""


def _cmd_hub_install(args: argparse.Namespace) -> None:
    """Install a community workflow by slug."""
    import urllib.request
    from pathlib import Path

    registry = _fetch_hub_registry()
    templates = registry.get("templates", [])
    slug = getattr(args, "slug", "")

    # Find template by slug
    template = None
    for t in templates:
        if t.get("slug") == slug:
            template = t
            break

    if template is None:
        print(
            f"{_color('Error', _C.RED)}: Workflow '{slug}' not found in the community hub.",
            file=sys.stderr,
        )
        sys.exit(1)

    download_url = template.get("download_url")
    if not download_url:
        print(
            f"{_color('Error', _C.RED)}: No download URL for '{slug}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate download URL points to a trusted source
    if not _validate_hub_download_url(download_url):
        print(
            f"{_color('Error', _C.RED)}: Download URL is not from a trusted source: {download_url}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Fetch the YAML content
    try:
        with urllib.request.urlopen(download_url, timeout=10) as resp:
            yaml_content = resp.read().decode("utf-8")
    except Exception as exc:
        print(
            f"{_color('Error', _C.RED)}: Failed to download workflow: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate downloaded YAML
    valid, err_msg = _validate_hub_yaml(yaml_content)
    if not valid:
        print(
            f"{_color('Error', _C.RED)}: Invalid workflow YAML: {err_msg}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Security scan
    from sandcastle.engine.hub_scanner import (
        compute_sha256,
        scan_template,
        verify_checksum,
    )

    scan = scan_template(yaml_content)
    if scan.errors:
        print(f"{_color('Security scan BLOCKED', _C.RED)}:")
        for err in scan.errors:
            step_info = f" (step: {err.step})" if err.step else ""
            print(f"  {_color('X', _C.RED)} [{err.code}] {err.message}{step_info}")
        sys.exit(1)

    force = getattr(args, "force", False)
    if scan.warnings and not force:
        print(f"{_color('Security warnings', _C.YELLOW)}:")
        for w in scan.warnings:
            step_info = f" (step: {w.step})" if w.step else ""
            print(f"  {_color('!', _C.YELLOW)} [{w.code}] {w.message}{step_info}")
        answer = input("Continue anyway? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Installation cancelled.")
            sys.exit(0)
    elif scan.warnings and force:
        n = len(scan.warnings)
        print(f"{_color('Security warnings (--force)', _C.YELLOW)}: {n} warning(s) skipped")

    # Verify checksum if registry has one
    registry_sha = template.get("sha256")
    if registry_sha:
        if not verify_checksum(yaml_content, registry_sha):
            actual = compute_sha256(yaml_content)
            print(
                f"{_color('Error', _C.RED)}: Checksum mismatch"
                " - content may have been tampered with.\n"
                f"  Expected: {registry_sha}\n"
                f"  Got:      {actual}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print(f"  {_color('Note', _C.DIM)}: No checksum in registry (not verified)")

    print(f"{_color('Security scan passed', _C.GREEN)}")

    # Save to target directory with sanitized filename
    target_dir = Path(getattr(args, "dir", None) or "./workflows/")
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = _sanitize_hub_filename(slug)
    target_path = target_dir / filename

    # Final path traversal guard
    resolved_target = target_path.resolve()
    resolved_dir = target_dir.resolve()
    if not str(resolved_target).startswith(str(resolved_dir)):
        print(
            f"{_color('Error', _C.RED)}: Path traversal detected in slug: {slug}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        target_path.write_text(yaml_content)
    except OSError as exc:
        print(
            f"{_color('Error', _C.RED)}: Failed to write file: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"{_color('Installed', _C.GREEN)}: {slug} -> {target_path}")
    print(f"  Name: {template.get('name', '')}")
    print(f"  Steps: {template.get('step_count', '?')}")
    print(f"  Category: {template.get('category', '')}")


def _cmd_hub_list(args: argparse.Namespace) -> None:
    """List community workflows."""
    registry = _fetch_hub_registry()
    templates = registry.get("templates", [])

    # Filter by category
    category = getattr(args, "category", None)
    if category:
        templates = [t for t in templates if t.get("category", "").lower() == category.lower()]

    limit = getattr(args, "limit", 20)
    templates = templates[:limit]

    if getattr(args, "json", False):
        print(json.dumps(templates, indent=2))
        return

    if not templates:
        print("No community workflows found.")
        return

    headers = ["SLUG", "NAME", "CATEGORY", "STEPS", "AUTHOR"]
    rows: list[list[str]] = []
    for t in templates:
        rows.append(
            [
                t.get("slug", ""),
                t.get("name", ""),
                t.get("category", ""),
                str(t.get("step_count", "")),
                t.get("author", ""),
            ]
        )
    print(_table(headers, rows))

    stats = registry.get("stats", {})
    total = stats.get("total_templates", len(templates))
    categories = registry.get("categories", [])
    print(f"\n{_color(str(total), _C.CYAN)} total workflows in {len(categories)} categories.")


def _cmd_hub_collections(args: argparse.Namespace) -> None:
    """List curated workflow collections."""
    registry = _fetch_hub_registry()
    collections = registry.get("collections", [])

    if getattr(args, "json", False):
        print(json.dumps(collections, indent=2))
        return

    if not collections:
        print("No collections available.")
        return

    # Icon mapping
    icons: dict[str, str] = {
        "target": "[TARGET]",
        "pen-tool": "[CONTENT]",
        "terminal": "[DEVOPS]",
        "headphones": "[SUPPORT]",
        "users": "[HR]",
    }

    for col in collections:
        icon = icons.get(col.get("icon", ""), "[*]")
        name = col.get("name", "")
        desc = col.get("description", "")
        slugs = col.get("template_slugs", [])
        downloads = col.get("downloads", 0)

        print(f"\n  {_color(icon, _C.CYAN)} {_color(name, _C.BOLD)}")
        print(f"     {desc}")
        print(
            f"     {_color(str(len(slugs)), _C.GREEN)} templates"
            f" | {_color(str(downloads), _C.CYAN)} downloads"
        )
        print(f"     Install: sandcastle hub install-collection {col.get('id', '')}")

    print(f"\n{_color(str(len(collections)), _C.CYAN)} collection(s) available.")


def _cmd_hub_install_collection(args: argparse.Namespace) -> None:
    """Install all workflows from a collection."""
    import urllib.request
    from pathlib import Path

    registry = _fetch_hub_registry()
    collections = registry.get("collections", [])
    collection_id = getattr(args, "collection_id", "")

    col = None
    for c in collections:
        if c.get("id") == collection_id:
            col = c
            break

    if not col:
        print(
            f"{_color('Error', _C.RED)}: Collection '{collection_id}' not found.",
            file=sys.stderr,
        )
        sys.exit(1)

    templates = registry.get("templates", [])
    templates_by_slug: dict[str, dict] = {t["slug"]: t for t in templates}

    target_dir = Path(getattr(args, "dir", None) or "./workflows/")
    target_dir.mkdir(parents=True, exist_ok=True)

    from sandcastle.engine.hub_scanner import scan_template, verify_checksum

    force = getattr(args, "force", False)
    installed = 0
    for slug in col.get("template_slugs", []):
        t = templates_by_slug.get(slug)
        if not t or not t.get("download_url"):
            print(f"  {_color('Skip', _C.YELLOW)}: {slug} (not found)")
            continue

        dl_url = t["download_url"]
        if not _validate_hub_download_url(dl_url):
            print(f"  {_color('Skip', _C.YELLOW)}: {slug} (untrusted download URL)")
            continue

        try:
            with urllib.request.urlopen(dl_url, timeout=10) as resp:
                yaml_content = resp.read().decode("utf-8")
            valid, err_msg = _validate_hub_yaml(yaml_content)
            if not valid:
                print(f"  {_color('Skip', _C.YELLOW)}: {slug} (invalid YAML: {err_msg})")
                continue

            # Security scan
            scan = scan_template(yaml_content)
            if scan.errors:
                msgs = "; ".join(e.message for e in scan.errors[:3])
                print(f"  {_color('BLOCKED', _C.RED)}: {slug} ({msgs})")
                continue
            if scan.warnings and not force:
                msgs = "; ".join(w.message for w in scan.warnings[:3])
                print(f"  {_color('Skip', _C.YELLOW)}: {slug} (warnings: {msgs}; use --force)")
                continue

            # Checksum verification
            registry_sha = t.get("sha256")
            if registry_sha and not verify_checksum(yaml_content, registry_sha):
                print(f"  {_color('BLOCKED', _C.RED)}: {slug} (checksum mismatch)")
                continue

            filename = _sanitize_hub_filename(slug)
            file_path = target_dir / filename
            # Path traversal guard
            if not str(file_path.resolve()).startswith(str(target_dir.resolve())):
                print(f"  {_color('Skip', _C.YELLOW)}: {slug} (path traversal)")
                continue
            file_path.write_text(yaml_content)
            print(f"  {_color('OK', _C.GREEN)}: {slug} -> {file_path}")
            installed += 1
        except Exception as exc:
            print(f"  {_color('Error', _C.RED)}: {slug} - {exc}")

    print(
        f"\n{_color(str(installed), _C.GREEN)} workflow(s) installed from '{col.get('name', '')}'."
    )


def _cmd_hub_publish(args: argparse.Namespace) -> None:
    """Publish a workflow to the community hub."""
    import webbrowser
    from pathlib import Path

    import yaml

    file_path = Path(getattr(args, "file", ""))
    if not file_path.exists():
        print(
            f"{_color('Error', _C.RED)}: File '{file_path}' not found.",
            file=sys.stderr,
        )
        sys.exit(1)

    content = file_path.read_text()
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        print(
            f"{_color('Error', _C.RED)}: Invalid YAML: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not data or not isinstance(data, dict):
        print(
            f"{_color('Error', _C.RED)}: YAML file must contain"
            " a mapping with name, description, and steps.",
            file=sys.stderr,
        )
        sys.exit(1)

    name = data.get("name") or file_path.stem
    steps = data.get("steps", [])
    if not steps:
        print(
            f"{_color('Warning', _C.YELLOW)}: Workflow has no steps defined.",
            file=sys.stderr,
        )

    # Security scan before publish guidance
    from sandcastle.engine.hub_scanner import compute_sha256, scan_template

    scan = scan_template(content)
    if scan.errors:
        print(f"\n{_color('Security scan found issues', _C.RED)}:")
        for err in scan.errors:
            step_info = f" (step: {err.step})" if err.step else ""
            print(f"  {_color('X', _C.RED)} [{err.code}] {err.message}{step_info}")
        print("\nPlease fix these issues before publishing.")
    if scan.warnings:
        print(f"\n{_color('Security warnings', _C.YELLOW)}:")
        for w in scan.warnings:
            step_info = f" (step: {w.step})" if w.step else ""
            print(f"  {_color('!', _C.YELLOW)} [{w.code}] {w.message}{step_info}")
    if not scan.errors and not scan.warnings:
        print(f"\n{_color('Security scan passed', _C.GREEN)}")

    print(f"\n{_color('SHA-256', _C.DIM)}: {compute_sha256(content)}")

    slug = file_path.stem.replace("_", "-")
    print(f"\n{_color('Workflow details', _C.BOLD)}:")
    print(f"  Name: {name}")
    print(f"  Slug: {slug}")
    print(f"  Steps: {len(steps)}")
    print()
    print("To publish your workflow to the community hub:")
    print(f"  1. Fork {_color('github.com/gizmax/Sandcastle', _C.CYAN)}")
    print(f"  2. Add your workflow to {_color('hub/community/<your-username>/', _C.CYAN)}")
    print("  3. Open a pull request")

    try:
        webbrowser.open("https://github.com/gizmax/Sandcastle")
    except Exception:
        pass


def _cmd_hub(args: argparse.Namespace) -> None:
    """Route hub sub-commands."""
    action = getattr(args, "hub_action", None)
    hub_dispatch: dict[str, Any] = {
        "search": _cmd_hub_search,
        "install": _cmd_hub_install,
        "list": _cmd_hub_list,
        "publish": _cmd_hub_publish,
        "collections": _cmd_hub_collections,
        "install-collection": _cmd_hub_install_collection,
    }
    handler = hub_dispatch.get(action)
    if handler:
        handler(args)
    else:
        print(
            "Usage: sandcastle hub {search,install,list,publish,collections,install-collection}",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# API helpers (shared by new command groups)
# ---------------------------------------------------------------------------


def _api_headers(args: argparse.Namespace) -> dict[str, str]:
    """Build HTTP headers from CLI args (api-key, etc.)."""
    api_key = getattr(args, "api_key", None) or os.getenv("SANDCASTLE_API_KEY", "")
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _api_base(args: argparse.Namespace) -> str:
    """Return the base API URL from CLI args or env."""
    return getattr(args, "url", None) or os.getenv("SANDCASTLE_URL", "http://localhost:8080")


def _api_get(
    args: argparse.Namespace,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform a GET request to the API and return parsed JSON body."""
    import httpx

    base = _api_base(args)
    try:
        resp = httpx.get(
            f"{base}{path}",
            params=params,
            headers=_api_headers(args),
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


def _api_post(
    args: argparse.Namespace,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform a POST request to the API and return parsed JSON body."""
    import httpx

    base = _api_base(args)
    try:
        resp = httpx.post(
            f"{base}{path}",
            json=payload,
            headers=_api_headers(args),
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


def _api_put(
    args: argparse.Namespace,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform a PUT request to the API and return parsed JSON body."""
    import httpx

    base = _api_base(args)
    try:
        resp = httpx.put(
            f"{base}{path}",
            json=payload,
            headers=_api_headers(args),
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


def _api_delete(
    args: argparse.Namespace,
    path: str,
) -> dict[str, Any]:
    """Perform a DELETE request to the API and return parsed JSON body."""
    import httpx

    base = _api_base(args)
    try:
        resp = httpx.delete(
            f"{base}{path}",
            headers=_api_headers(args),
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


def _json_out(data: Any) -> None:
    """Print data as formatted JSON."""
    print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# keys - API Key management
# ---------------------------------------------------------------------------


def _cmd_keys_list(args: argparse.Namespace) -> None:
    """List all API keys (masked)."""
    body = _api_get(args, "/api/api-keys")
    items = body.get("data", [])

    if getattr(args, "json", False):
        _json_out(body)
        return

    if not items:
        print("No API keys found.")
        return

    headers = ["ID", "PREFIX", "NAME", "TENANT", "COST LIMIT", "CREATED", "LAST USED"]
    rows: list[list[str]] = []
    for k in items:
        if isinstance(k, dict):
            cost = k.get("max_cost_per_run_usd")
            cost_str = f"${cost:.2f}" if cost else "-"
            rows.append(
                [
                    k.get("id", "")[:12],
                    k.get("key_prefix", ""),
                    k.get("name", "") or "-",
                    k.get("tenant_id", "") or "-",
                    cost_str,
                    _fmt_time(k.get("created_at")),
                    _fmt_time(k.get("last_used_at")),
                ]
            )
    print(_table(headers, rows))


def _cmd_keys_create(args: argparse.Namespace) -> None:
    """Create a new API key."""
    payload: dict[str, Any] = {"name": args.name}
    tenant = getattr(args, "tenant", None)
    if tenant:
        payload["tenant_id"] = tenant
    cost_limit = getattr(args, "cost_limit", None)
    if cost_limit is not None:
        payload["max_cost_per_run_usd"] = cost_limit

    body = _api_post(args, "/api/api-keys", payload)
    data = body.get("data", {})

    if getattr(args, "json", False):
        _json_out(body)
        return

    plaintext = data.get("key", "")
    print(f"API key created: {data.get('id', '?')}")
    print(f"  Name:   {data.get('name', '-')}")
    print(f"  Prefix: {data.get('key_prefix', '')}")
    if data.get("tenant_id"):
        print(f"  Tenant: {data['tenant_id']}")
    print()
    print(_color("  Key (shown ONCE - save it now!):", _C.YELLOW))
    print(f"  {_color(plaintext, _C.GREEN)}")
    print()


def _cmd_keys_delete(args: argparse.Namespace) -> None:
    """Delete (deactivate) an API key."""
    # Safety confirmation for destructive operation
    force = getattr(args, "force", False)
    if not force and sys.stdin.isatty():
        try:
            answer = input(
                f"  Deactivate API key {args.key_id}? This cannot be undone. [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if answer not in ("y", "yes"):
            print("  Aborted.")
            return

    body = _api_delete(args, f"/api/api-keys/{args.key_id}")
    data = body.get("data", {})

    if getattr(args, "json", False):
        _json_out(body)
        return

    if data.get("deactivated"):
        print(f"API key deactivated: {args.key_id}")
    else:
        print(f"Unexpected response: {data}")


def _cmd_keys_rotate(args: argparse.Namespace) -> None:
    """Rotate an API key (create new + deactivate old).

    Since there is no dedicated rotate endpoint, this performs
    the operation client-side: create a new key, then deactivate
    the old one.
    """
    # First, get the old key details to copy name/tenant
    list_body = _api_get(args, "/api/api-keys")
    old_key = None
    for k in list_body.get("data", []):
        if isinstance(k, dict) and k.get("id", "").startswith(args.key_id):
            old_key = k
            break

    # Build create payload from old key metadata
    create_payload: dict[str, Any] = {
        "name": (old_key.get("name", "") if old_key else "") + " (rotated)",
    }
    if old_key and old_key.get("tenant_id"):
        create_payload["tenant_id"] = old_key["tenant_id"]
    if old_key and old_key.get("max_cost_per_run_usd"):
        create_payload["max_cost_per_run_usd"] = old_key["max_cost_per_run_usd"]

    # Create replacement key
    create_body = _api_post(args, "/api/api-keys", create_payload)
    new_data = create_body.get("data", {})

    # Deactivate old key
    _api_delete(args, f"/api/api-keys/{args.key_id}")

    if getattr(args, "json", False):
        _json_out(
            {
                "rotated": True,
                "old_key_id": args.key_id,
                "new_key_id": new_data.get("id"),
                "new_key": new_data.get("key"),
            }
        )
        return

    print(f"Key rotated: {args.key_id} -> {new_data.get('id', '?')}")
    print("  Old key deactivated.")
    print()
    print(_color("  New key (shown ONCE - save it now!):", _C.YELLOW))
    print(f"  {_color(new_data.get('key', ''), _C.GREEN)}")
    print()


def _cmd_keys(args: argparse.Namespace) -> None:
    """Route keys sub-commands."""
    action = getattr(args, "keys_action", None)
    keys_dispatch: dict[str, Any] = {
        "list": _cmd_keys_list,
        "create": _cmd_keys_create,
        "delete": _cmd_keys_delete,
        "rotate": _cmd_keys_rotate,
    }
    handler = keys_dispatch.get(action)
    if handler:
        handler(args)
    else:
        print("Usage: sandcastle keys {list,create,delete,rotate}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# dlq - Dead Letter Queue management
# ---------------------------------------------------------------------------


def _cmd_dlq_list(args: argparse.Namespace) -> None:
    """List dead letter queue items."""
    params: dict[str, Any] = {}
    if getattr(args, "resolved", False):
        params["resolved"] = "true"

    body = _api_get(args, "/api/dead-letter", params=params)
    items = body.get("data", [])

    if getattr(args, "json", False):
        _json_out(body)
        return

    if not items:
        print("No dead letter queue items found.")
        return

    headers = ["ID", "RUN ID", "STEP", "ERROR", "ATTEMPTS", "CREATED", "RESOLVED"]
    rows: list[list[str]] = []
    for item in items:
        if isinstance(item, dict):
            resolved = item.get("resolved_at")
            resolved_str = _fmt_time(resolved) if resolved else "-"
            rows.append(
                [
                    item.get("id", "")[:12],
                    item.get("run_id", "")[:12],
                    item.get("step_id", ""),
                    (item.get("error", "") or "")[:40],
                    str(item.get("attempts", 0)),
                    _fmt_time(item.get("created_at")),
                    resolved_str,
                ]
            )
    print(_table(headers, rows))


def _cmd_dlq_retry(args: argparse.Namespace) -> None:
    """Retry a dead letter queue item."""
    body = _api_post(args, f"/api/dead-letter/{args.item_id}/retry")
    data = body.get("data", {})

    if getattr(args, "json", False):
        _json_out(body)
        return

    if data.get("retried"):
        print(f"DLQ item retried: {args.item_id}")
        print(f"  New run: {data.get('new_run_id', '?')}")
    else:
        print(f"Unexpected response: {data}")


def _cmd_dlq_resolve(args: argparse.Namespace) -> None:
    """Manually resolve a dead letter queue item."""
    body = _api_post(args, f"/api/dead-letter/{args.item_id}/resolve")
    data = body.get("data", {})

    if getattr(args, "json", False):
        _json_out(body)
        return

    resolved_at = data.get("resolved_at")
    if resolved_at:
        print(f"DLQ item resolved: {args.item_id}")
        print(f"  Resolved by: {data.get('resolved_by', 'manual')}")
    else:
        print(f"Unexpected response: {data}")


def _cmd_dlq(args: argparse.Namespace) -> None:
    """Route dlq sub-commands."""
    action = getattr(args, "dlq_action", None)
    dlq_dispatch: dict[str, Any] = {
        "list": _cmd_dlq_list,
        "retry": _cmd_dlq_retry,
        "resolve": _cmd_dlq_resolve,
    }
    handler = dlq_dispatch.get(action)
    if handler:
        handler(args)
    else:
        print("Usage: sandcastle dlq {list,retry,resolve}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# violations - Policy violations
# ---------------------------------------------------------------------------


def _cmd_violations_list(args: argparse.Namespace) -> None:
    """List policy violations."""
    params: dict[str, Any] = {}
    severity = getattr(args, "severity", None)
    if severity:
        params["severity"] = severity

    body = _api_get(args, "/api/violations", params=params)
    items = body.get("data", [])

    if getattr(args, "json", False):
        _json_out(body)
        return

    if not items:
        print("No policy violations found.")
        return

    headers = ["ID", "RUN ID", "STEP", "POLICY", "SEVERITY", "ACTION", "CREATED"]
    rows: list[list[str]] = []
    for v in items:
        if isinstance(v, dict):
            sev = v.get("severity", "")
            # Colorize severity levels
            if sev == "critical":
                sev_str = _color(sev, _C.RED)
            elif sev == "high":
                sev_str = _color(sev, _C.RED)
            elif sev == "medium":
                sev_str = _color(sev, _C.YELLOW)
            else:
                sev_str = _color(sev, _C.DIM) if sev else "-"
            rows.append(
                [
                    v.get("id", "")[:12],
                    v.get("run_id", "")[:12],
                    v.get("step_id", ""),
                    v.get("policy_id", ""),
                    sev_str,
                    v.get("action_taken", ""),
                    _fmt_time(v.get("created_at")),
                ]
            )
    print(_table(headers, rows))


def _cmd_violations(args: argparse.Namespace) -> None:
    """Route violations sub-commands."""
    action = getattr(args, "violations_action", None)
    violations_dispatch: dict[str, Any] = {
        "list": _cmd_violations_list,
    }
    handler = violations_dispatch.get(action)
    if handler:
        handler(args)
    else:
        print("Usage: sandcastle violations {list}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# tools - Tool/connector management
# ---------------------------------------------------------------------------


def _cmd_tools_list(args: argparse.Namespace) -> None:
    """List available tool connectors."""
    params: dict[str, Any] = {}
    category = getattr(args, "category", None)
    if category:
        params["category"] = category

    body = _api_get(args, "/api/tools", params=params)
    raw = body.get("data", {})
    # The response wraps tools in a ToolListResponse with a 'tools' key
    items = raw.get("tools", []) if isinstance(raw, dict) else raw

    if getattr(args, "json", False):
        _json_out(body)
        return

    if not items:
        print("No tools found.")
        return

    headers = ["NAME", "CATEGORY", "STATUS", "CONNECTIONS"]
    rows: list[list[str]] = []
    for t in items:
        if isinstance(t, dict):
            configured = t.get("credentials_configured", [])
            missing = t.get("credentials_missing", [])
            if not missing:
                status_str = _color("configured", _C.GREEN)
            elif configured:
                status_str = _color("partial", _C.YELLOW)
            else:
                status_str = _color("not configured", _C.RED)
            conns = t.get("connections", [])
            conn_str = str(len(conns)) if conns else "0"
            rows.append(
                [
                    t.get("name", ""),
                    t.get("category", ""),
                    status_str,
                    conn_str,
                ]
            )
    print(_table(headers, rows))


def _cmd_tools_configure(args: argparse.Namespace) -> None:
    """Set credential env vars for a tool connector."""
    tool_name = args.tool_name
    env_pairs = getattr(args, "env", None)

    if not env_pairs:
        print("Error: at least one --env KEY=VALUE is required", file=sys.stderr)
        sys.exit(1)

    credentials: dict[str, str] = {}
    for pair in env_pairs:
        if "=" not in pair:
            print(f"Error: invalid format '{pair}' - expected KEY=VALUE", file=sys.stderr)
            sys.exit(1)
        key, _, value = pair.partition("=")
        credentials[key] = value

    body = _api_put(args, f"/api/tools/{tool_name}/credentials", {"credentials": credentials})
    data = body.get("data", {})

    if getattr(args, "json", False):
        _json_out(body)
        return

    print(f"Credentials updated for tool: {tool_name}")
    configured = data.get("credentials_configured", [])
    missing = data.get("credentials_missing", [])
    if configured:
        print(f"  Configured: {', '.join(configured)}")
    if missing:
        print(f"  Still missing: {_color(', '.join(missing), _C.YELLOW)}")


def _cmd_tools(args: argparse.Namespace) -> None:
    """Route tools sub-commands."""
    action = getattr(args, "tools_action", None)
    tools_dispatch: dict[str, Any] = {
        "list": _cmd_tools_list,
        "configure": _cmd_tools_configure,
    }
    handler = tools_dispatch.get(action)
    if handler:
        handler(args)
    else:
        print("Usage: sandcastle tools {list,configure}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# runs compare - Side-by-side run comparison
# ---------------------------------------------------------------------------


def _cmd_runs_compare(args: argparse.Namespace) -> None:
    """Compare two runs side by side."""
    body = _api_get(
        args,
        "/api/runs/compare",
        params={
            "run_a": args.run_a,
            "run_b": args.run_b,
        },
    )
    data = body.get("data", {})

    if getattr(args, "json", False):
        _json_out(body)
        return

    run_a = data.get("run_a", {})
    run_b = data.get("run_b", {})

    # Header
    print()
    print(_color("  Run Comparison", _C.BOLD))
    print(_color("  ==============", _C.BOLD))
    print()

    # Summary table
    summary_headers = ["", "RUN A", "RUN B", "DELTA"]
    summary_rows: list[list[str]] = []

    summary_rows.append(
        [
            "Run ID",
            run_a.get("run_id", "")[:12],
            run_b.get("run_id", "")[:12],
            "",
        ]
    )
    summary_rows.append(
        [
            "Workflow",
            run_a.get("workflow_name", ""),
            run_b.get("workflow_name", ""),
            _color("same", _C.GREEN)
            if data.get("same_workflow")
            else _color("different", _C.YELLOW),
        ]
    )
    summary_rows.append(
        [
            "Status",
            _status_color(run_a.get("status", "")),
            _status_color(run_b.get("status", "")),
            "",
        ]
    )

    cost_a = data.get("total_cost_a", 0)
    cost_b = data.get("total_cost_b", 0)
    cost_delta = data.get("total_cost_delta", 0)
    delta_color = _C.GREEN if cost_delta <= 0 else _C.RED
    summary_rows.append(
        [
            "Cost",
            f"${cost_a:.4f}",
            f"${cost_b:.4f}",
            _color(f"{cost_delta:+.4f}", delta_color),
        ]
    )

    dur_a = data.get("total_duration_a")
    dur_b = data.get("total_duration_b")
    dur_delta = data.get("total_duration_delta")
    dur_a_str = f"{dur_a:.1f}s" if dur_a is not None else "-"
    dur_b_str = f"{dur_b:.1f}s" if dur_b is not None else "-"
    dur_delta_str = (
        _color(f"{dur_delta:+.1f}s", _C.GREEN if (dur_delta or 0) <= 0 else _C.RED)
        if dur_delta is not None
        else "-"
    )
    summary_rows.append(
        [
            "Duration",
            dur_a_str,
            dur_b_str,
            dur_delta_str,
        ]
    )

    print(_table(summary_headers, summary_rows))

    # Step diffs table
    steps = data.get("steps", [])
    if steps:
        print()
        print(_color("  Step Differences", _C.BOLD))
        print()
        step_headers = ["STEP", "PRESENCE", "STATUS A", "STATUS B", "COST A", "COST B", "CHANGED"]
        step_rows: list[list[str]] = []
        for s in steps:
            if isinstance(s, dict):
                presence = s.get("presence", "")
                if presence == "both":
                    pres_str = "both"
                elif presence == "only_a":
                    pres_str = _color("only A", _C.YELLOW)
                else:
                    pres_str = _color("only B", _C.YELLOW)

                output_changed = s.get("output_changed", False)
                config_changed = s.get("config_changed", False)
                changes = []
                if output_changed:
                    changes.append("output")
                if config_changed:
                    changes.append("config")
                change_str = _color(", ".join(changes), _C.YELLOW) if changes else "-"

                step_rows.append(
                    [
                        s.get("step_id", ""),
                        pres_str,
                        _status_color(s.get("status_a", "") or "-"),
                        _status_color(s.get("status_b", "") or "-"),
                        f"${s.get('cost_a', 0):.4f}",
                        f"${s.get('cost_b', 0):.4f}",
                        change_str,
                    ]
                )
        print(_table(step_headers, step_rows))

    print()


# ---------------------------------------------------------------------------
# autopilot - AutoPilot experiment management
# ---------------------------------------------------------------------------


def _cmd_autopilot_list(args: argparse.Namespace) -> None:
    """List AutoPilot experiments."""
    params: dict[str, Any] = {}
    status_filter = getattr(args, "status", None)
    if status_filter:
        params["status"] = status_filter

    body = _api_get(args, "/api/autopilot/experiments", params=params)
    items = body.get("data", [])

    if getattr(args, "json", False):
        _json_out(body)
        return

    if not items:
        print("No autopilot experiments found.")
        return

    headers = ["ID", "WORKFLOW", "STEP", "STATUS", "OPTIMIZE FOR", "DEPLOYED", "CREATED"]
    rows: list[list[str]] = []
    for e in items:
        if isinstance(e, dict):
            status = e.get("status", "")
            deployed = e.get("deployed_variant_id")
            deployed_str = deployed[:12] if deployed else "-"
            rows.append(
                [
                    e.get("id", "")[:12],
                    e.get("workflow_name", ""),
                    e.get("step_id", ""),
                    _status_color(status),
                    e.get("optimize_for", ""),
                    deployed_str,
                    _fmt_time(e.get("created_at")),
                ]
            )
    print(_table(headers, rows))


def _cmd_autopilot_deploy(args: argparse.Namespace) -> None:
    """Deploy the winning variant from an experiment."""
    body = _api_post(args, f"/api/autopilot/experiments/{args.experiment_id}/deploy")
    data = body.get("data", {})

    if getattr(args, "json", False):
        _json_out(body)
        return

    if data.get("deployed"):
        print(f"Experiment deployed: {args.experiment_id}")
        print(f"  Winning variant: {data.get('deployed_variant_id', '?')}")
        if data.get("workflow_name"):
            print(f"  Workflow: {data['workflow_name']}")
        if data.get("step_id"):
            print(f"  Step: {data['step_id']}")
    else:
        print(f"Deploy response: {data}")


def _cmd_autopilot(args: argparse.Namespace) -> None:
    """Route autopilot sub-commands."""
    action = getattr(args, "autopilot_action", None)
    autopilot_dispatch: dict[str, Any] = {
        "list": _cmd_autopilot_list,
        "deploy": _cmd_autopilot_deploy,
    }
    handler = autopilot_dispatch.get(action)
    if handler:
        handler(args)
    else:
        print("Usage: sandcastle autopilot {list,deploy}", file=sys.stderr)
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
    from sandcastle import __version__

    parser = argparse.ArgumentParser(
        prog="sandcastle",
        description="Sandcastle - workflow orchestrator CLI",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"sandcastle {__version__}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output raw JSON instead of formatted tables",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- init ---
    subparsers.add_parser("init", help="Interactive setup wizard (create .env)")

    # --- serve ---
    p_serve = subparsers.add_parser("serve", help="Start the API server")
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    p_serve.add_argument(
        "--reload", action="store_true", default=False, help="Enable auto-reload for development"
    )

    # --- run ---
    p_run = subparsers.add_parser("run", help="Run a workflow")
    p_run.add_argument("workflow", help="Workflow name or path to .yaml file")
    p_run.add_argument(
        "--input",
        "-i",
        action="append",
        metavar="KEY=VALUE",
        help="Input key=value pair (repeatable)",
    )
    p_run.add_argument("--input-file", "-f", metavar="FILE", help="JSON file with input data")
    p_run.add_argument(
        "--wait", "-w", action="store_true", help="Wait for completion and print result"
    )
    p_run.add_argument(
        "--max-cost", type=float, default=None, metavar="USD", help="Maximum cost limit in USD"
    )
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
    p_logs.add_argument(
        "--follow", "-f", action="store_true", help="Keep streaming after terminal state"
    )
    _add_connection_args(p_logs)

    # --- ls ---
    p_ls = subparsers.add_parser("ls", help="List resources")
    ls_sub = p_ls.add_subparsers(dest="resource", help="Resource type")

    p_ls_runs = ls_sub.add_parser("runs", help="List runs")
    p_ls_runs.add_argument(
        "--status", "-s", default=None, help="Filter by status (queued, running, completed, failed)"
    )
    p_ls_runs.add_argument(
        "--limit", "-n", type=int, default=20, help="Max number of results (default: 20)"
    )
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
    p_sched_create.add_argument(
        "--input",
        "-i",
        action="append",
        metavar="KEY=VALUE",
        help="Input key=value pair (repeatable)",
    )
    _add_connection_args(p_sched_create)

    p_sched_delete = sched_sub.add_parser("delete", help="Delete a schedule")
    p_sched_delete.add_argument("id", help="Schedule ID to delete")
    _add_connection_args(p_sched_delete)

    # --- db ---
    p_db = subparsers.add_parser("db", help="Database management")
    db_sub = p_db.add_subparsers(dest="db_action", help="Database action")
    db_sub.add_parser("migrate", help="Run Alembic migrations (PostgreSQL only)")

    # --- worker ---
    subparsers.add_parser("worker", help="Start the arq background worker")

    # --- health ---
    p_health = subparsers.add_parser("health", help="Check API health")
    _add_connection_args(p_health)

    # --- mcp ---
    p_mcp = subparsers.add_parser(
        "mcp",
        help="Start MCP server for Claude Desktop / Cursor / Windsurf",
    )
    _add_connection_args(p_mcp)

    # --- doctor ---
    subparsers.add_parser("doctor", help="Run local diagnostics")

    # --- eval ---
    p_eval = subparsers.add_parser("eval", help="Run an eval suite against a workflow")
    p_eval.add_argument("suite", help="Path to eval suite YAML file")
    p_eval.add_argument(
        "--concurrency", "-c", type=int, default=1, help="Max concurrent test cases (default: 1)"
    )
    p_eval.add_argument("--tag", "-t", action="append", help="Filter cases by tag (repeatable)")
    p_eval.add_argument(
        "--verbose", "-v", action="store_true", help="Show assertion details for failed cases"
    )

    # --- generate ---
    p_gen = subparsers.add_parser("generate", help="Generate workflow from natural language")
    p_gen.add_argument("--description", "-d", help="What the workflow should do")
    p_gen.add_argument(
        "--output", "-o", metavar="FILE", help="Write YAML to file instead of stdout"
    )
    p_gen.add_argument(
        "--refine", action="store_true", help="Enter refinement loop after generation"
    )

    # --- templates ---
    p_templates = subparsers.add_parser("templates", help="List workflow templates")
    p_templates.add_argument("--category", "-c", default=None, help="Filter by category")
    _add_connection_args(p_templates)

    # --- replay ---
    p_replay = subparsers.add_parser("replay", help="Replay a workflow run from a step")
    p_replay.add_argument("run_id", help="Run ID to replay")
    p_replay.add_argument(
        "--from-step", required=True, dest="from_step", help="Step ID to replay from"
    )
    _add_connection_args(p_replay)

    # --- fork ---
    p_fork = subparsers.add_parser("fork", help="Fork a run with modifications")
    p_fork.add_argument("run_id", help="Run ID to fork")
    p_fork.add_argument("--from-step", required=True, dest="from_step", help="Step ID to fork from")
    p_fork.add_argument(
        "--change",
        action="append",
        metavar="KEY=VALUE",
        help="Step override key=value (repeatable)",
    )
    _add_connection_args(p_fork)

    # --- approve ---
    p_approve = subparsers.add_parser("approve", help="Approve a paused approval step")
    p_approve.add_argument("run_id", help="Run ID with pending approval")
    p_approve.add_argument("--data", default=None, help="JSON data to pass with approval")
    _add_connection_args(p_approve)

    # --- reject ---
    p_reject = subparsers.add_parser("reject", help="Reject a paused approval step")
    p_reject.add_argument("run_id", help="Run ID with pending approval")
    p_reject.add_argument("--reason", default=None, help="Rejection reason")
    _add_connection_args(p_reject)

    # --- runs ---
    p_runs = subparsers.add_parser("runs", help="List or compare workflow runs")
    runs_sub = p_runs.add_subparsers(dest="runs_action")

    # runs list (default when no sub-command given)
    p_runs_list = runs_sub.add_parser("list", help="List recent workflow runs")
    p_runs_list.add_argument(
        "--status", "-s", default=None, help="Filter by status (queued, running, completed, failed)"
    )
    p_runs_list.add_argument(
        "--limit", "-n", type=int, default=20, help="Max number of results (default: 20)"
    )
    _add_connection_args(p_runs_list)

    # runs compare
    p_runs_compare = runs_sub.add_parser("compare", help="Compare two runs side by side")
    p_runs_compare.add_argument("run_a", help="First run ID")
    p_runs_compare.add_argument("run_b", help="Second run ID")
    _add_connection_args(p_runs_compare)

    # Keep backward compat: 'sandcastle runs --status ...' still works
    p_runs.add_argument(
        "--status", "-s", default=None, help="Filter by status (queued, running, completed, failed)"
    )
    p_runs.add_argument(
        "--limit", "-n", type=int, default=20, help="Max number of results (default: 20)"
    )
    _add_connection_args(p_runs)

    # --- keys ---
    p_keys = subparsers.add_parser("keys", help="Manage API keys")
    keys_sub = p_keys.add_subparsers(dest="keys_action")

    p_keys_list = keys_sub.add_parser("list", help="List all API keys (masked)")
    _add_connection_args(p_keys_list)

    p_keys_create = keys_sub.add_parser("create", help="Create a new API key")
    p_keys_create.add_argument("--name", required=True, help="Name for the API key")
    p_keys_create.add_argument("--tenant", default=None, help="Tenant ID to scope the key")
    p_keys_create.add_argument(
        "--cost-limit", type=float, default=None, dest="cost_limit", help="Max cost per run in USD"
    )
    _add_connection_args(p_keys_create)

    p_keys_delete = keys_sub.add_parser("delete", help="Delete (deactivate) an API key")
    p_keys_delete.add_argument("key_id", help="API key ID to delete")
    p_keys_delete.add_argument(
        "--force", "-f", action="store_true", help="Skip confirmation prompt"
    )
    _add_connection_args(p_keys_delete)

    p_keys_rotate = keys_sub.add_parser(
        "rotate", help="Rotate an API key (create new + deactivate old)"
    )
    p_keys_rotate.add_argument("key_id", help="API key ID to rotate")
    _add_connection_args(p_keys_rotate)

    # --- dlq ---
    p_dlq = subparsers.add_parser("dlq", help="Dead letter queue management")
    dlq_sub = p_dlq.add_subparsers(dest="dlq_action")

    p_dlq_list = dlq_sub.add_parser("list", help="List failed items")
    p_dlq_list.add_argument("--resolved", action="store_true", help="Include resolved items")
    _add_connection_args(p_dlq_list)

    p_dlq_retry = dlq_sub.add_parser("retry", help="Retry a failed item")
    p_dlq_retry.add_argument("item_id", help="DLQ item ID to retry")
    _add_connection_args(p_dlq_retry)

    p_dlq_resolve = dlq_sub.add_parser("resolve", help="Mark a failed item as resolved")
    p_dlq_resolve.add_argument("item_id", help="DLQ item ID to resolve")
    _add_connection_args(p_dlq_resolve)

    # --- violations ---
    p_violations = subparsers.add_parser("violations", help="Policy violations")
    violations_sub = p_violations.add_subparsers(dest="violations_action")

    p_violations_list = violations_sub.add_parser("list", help="List policy violations")
    p_violations_list.add_argument(
        "--severity",
        default=None,
        choices=["critical", "high", "medium", "low"],
        help="Filter by severity level",
    )
    _add_connection_args(p_violations_list)

    # --- tools ---
    p_tools = subparsers.add_parser("tools", help="Tool/connector management")
    tools_sub = p_tools.add_subparsers(dest="tools_action")

    p_tools_list = tools_sub.add_parser("list", help="List available tools/connectors")
    p_tools_list.add_argument("--category", default=None, help="Filter by category")
    _add_connection_args(p_tools_list)

    p_tools_configure = tools_sub.add_parser("configure", help="Set credentials for a tool")
    p_tools_configure.add_argument("tool_name", help="Tool name to configure")
    p_tools_configure.add_argument(
        "--env",
        action="append",
        metavar="KEY=VALUE",
        help="Credential env var (repeatable, e.g. --env SLACK_TOKEN=xoxb-...)",
    )
    _add_connection_args(p_tools_configure)

    # --- autopilot ---
    p_autopilot = subparsers.add_parser("autopilot", help="AutoPilot experiment management")
    autopilot_sub = p_autopilot.add_subparsers(dest="autopilot_action")

    p_autopilot_list = autopilot_sub.add_parser("list", help="List experiments")
    p_autopilot_list.add_argument("--status", "-s", default=None, help="Filter by status")
    _add_connection_args(p_autopilot_list)

    p_autopilot_deploy = autopilot_sub.add_parser("deploy", help="Deploy winning variant")
    p_autopilot_deploy.add_argument("experiment_id", help="Experiment ID to deploy")
    _add_connection_args(p_autopilot_deploy)

    # --- hub ---
    p_hub = subparsers.add_parser("hub", help="Community workflow hub")
    hub_sub = p_hub.add_subparsers(dest="hub_action")

    hub_search = hub_sub.add_parser("search", help="Search community workflows")
    hub_search.add_argument("query", help="Search query")
    hub_search.add_argument("--category", "-c", help="Filter by category")

    hub_install = hub_sub.add_parser("install", help="Install a community workflow")
    hub_install.add_argument("slug", help="Workflow slug (e.g. gizmax/lead-scoring)")
    hub_install.add_argument(
        "--dir", "-d", default=None, help="Target directory (default: ./workflows/)"
    )
    hub_install.add_argument(
        "--force", action="store_true", help="Skip security warnings (errors still block)"
    )

    hub_list = hub_sub.add_parser("list", help="List community workflows")
    hub_list.add_argument("--category", "-c", help="Filter by category")
    hub_list.add_argument("--limit", "-n", type=int, default=20, help="Max results")

    hub_publish = hub_sub.add_parser("publish", help="Publish a workflow to the community hub")
    hub_publish.add_argument("file", help="Path to workflow YAML file")

    hub_sub.add_parser("collections", help="List curated workflow collections")

    hub_install_col = hub_sub.add_parser(
        "install-collection", help="Install all workflows from a collection"
    )
    hub_install_col.add_argument("collection_id", help="Collection ID")
    hub_install_col.add_argument(
        "--dir", "-d", default=None, help="Target directory (default: ./workflows/)"
    )
    hub_install_col.add_argument(
        "--force", action="store_true", help="Skip security warnings (errors still block)"
    )

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

    # Commands with sub-commands must be routed BEFORE the simple dispatch
    # so that sub-actions like 'runs compare' are not shadowed.

    # --- runs (with sub-commands + backward compat) ---
    if args.command == "runs":
        action = getattr(args, "runs_action", None)
        if action == "compare":
            _cmd_runs_compare(args)
        else:
            # Default: list runs (backward compat for 'sandcastle runs')
            _cmd_runs(args)
        return

    # --- db ---
    if args.command == "db":
        if getattr(args, "db_action", None) == "migrate":
            _cmd_db_migrate(args)
        else:
            print("Usage: sandcastle db migrate", file=sys.stderr)
            sys.exit(1)
        return

    # --- schedule ---
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

    # Simple dispatch for commands without sub-routing conflicts
    dispatch: dict[str, Any] = {
        "init": _cmd_init,
        "serve": _cmd_serve,
        "run": _cmd_run,
        "status": _cmd_status,
        "cancel": _cmd_cancel,
        "logs": _cmd_logs,
        "ls": _cmd_ls,
        "worker": _cmd_worker,
        "health": _cmd_health,
        "mcp": _cmd_mcp,
        "doctor": _cmd_doctor,
        "generate": _cmd_generate,
        "eval": _cmd_eval,
        "templates": _cmd_templates,
        "replay": _cmd_replay,
        "fork": _cmd_fork,
        "approve": _cmd_approve,
        "reject": _cmd_reject,
        "hub": _cmd_hub,
        "keys": _cmd_keys,
        "dlq": _cmd_dlq,
        "violations": _cmd_violations,
        "tools": _cmd_tools,
        "autopilot": _cmd_autopilot,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
