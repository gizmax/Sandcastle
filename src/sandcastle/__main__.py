"""CLI entrypoint for `python -m sandcastle` / `sandcastle` command."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
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

    # Pad rows that are shorter than headers; truncate rows that are longer
    rows = [r[:len(headers)] + [""] * max(0, len(headers) - len(r)) for r in rows]

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
# .env loader
# ---------------------------------------------------------------------------

_dot_env_loaded = False


def _load_dot_env() -> None:
    """Load .env file into os.environ if it exists (idempotent).

    This ensures CLI commands that read os.getenv() directly (e.g. for
    SANDCASTLE_URL, SANDCASTLE_API_KEY) pick up values from .env without
    requiring pydantic-settings or the full config module.
    """
    global _dot_env_loaded
    if _dot_env_loaded:
        return
    _dot_env_loaded = True

    from pathlib import Path

    env_path = Path(".env")
    if not env_path.is_file():
        return

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Handle 'export KEY=VALUE' syntax
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Only set if not already in environment (env vars take precedence)
            if key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


# ---------------------------------------------------------------------------
# UUID validation helper
# ---------------------------------------------------------------------------


def _validate_run_id(run_id: str) -> None:
    """Validate that run_id looks like a valid UUID and exit with a clear
    message if it is obviously invalid (empty or contains spaces/slashes).
    For borderline cases, print a warning but let the request proceed
    (the server will return 404 for unknown IDs).
    """
    import re

    # Hard-reject obviously invalid inputs
    if not run_id or not run_id.strip():
        print("Error: run ID cannot be empty.", file=sys.stderr)
        sys.exit(1)

    # Block path traversal, whitespace, and control characters (incl. null bytes)
    if "/" in run_id or "\\" in run_id or " " in run_id or "\x00" in run_id:
        print(
            f"Error: '{run_id}' is not a valid run ID (contains invalid characters).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Block any non-printable / control characters
    if any(ord(c) < 32 for c in run_id):
        print(
            "Error: run ID contains control characters.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Warn (but don't block) if it doesn't look like a UUID
    uuid_pattern = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    if not uuid_pattern.match(run_id):
        print(
            f"Warning: '{run_id}' does not look like a valid UUID "
            f"(expected format: 550e8400-e29b-41d4-a716-446655440000)",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Improved error formatting
# ---------------------------------------------------------------------------


def _format_cli_error(exc: Exception) -> str:
    """Format an exception into a user-friendly CLI error message."""
    exc_type = type(exc).__name__

    # SDK structured errors - show the API error code and message clearly
    if exc_type == "SandcastleError":
        status = getattr(exc, "status_code", 0)
        code = getattr(exc, "code", "UNKNOWN")
        message = getattr(exc, "message", str(exc))
        if status == 401:
            return f"Authentication failed (HTTP 401): {message}\n  Check your API key."
        if status == 403:
            return f"Access denied (HTTP 403): {message}\n  Your API key may lack the required permissions."
        if status == 404:
            return f"Not found (HTTP 404): {message}"
        if status == 422:
            return f"Validation error (HTTP 422): {message}"
        if status >= 500:
            return f"Server error (HTTP {status}): {message}"
        if status > 0:
            return f"API error (HTTP {status}) [{code}]: {message}"
        # Non-HTTP errors (e.g. connection errors wrapped in SandcastleError)
        return f"API error [{code}]: {message}"

    # httpx connection errors
    if "ConnectError" in exc_type or "ConnectionError" in exc_type:
        return (
            f"Connection failed: {exc}\n"
            "  Is the Sandcastle server running? Start it with: sandcastle serve\n"
            "  Or run without a server: sandcastle run --local <workflow.yaml>"
        )
    if "ConnectTimeout" in exc_type or "TimeoutException" in exc_type:
        return f"Connection timed out: {exc}\n  Check that the server URL is correct."
    if "ConnectionRefusedError" in exc_type or "ConnectionRefused" in str(exc):
        return (
            f"Connection refused: {exc}\n"
            "  Is the Sandcastle server running? Start it with: sandcastle serve"
        )

    # HTTP status errors (fallback for non-SDK HTTP errors)
    msg = str(exc)
    if "401" in msg or "Unauthorized" in msg:
        return "Authentication failed (HTTP 401). Check your API key."
    if "403" in msg or "Forbidden" in msg:
        return "Access denied (HTTP 403). Your API key may lack the required permissions."
    if "404" in msg or "Not Found" in msg:
        return f"Resource not found (HTTP 404): {exc}"
    if "422" in msg:
        return f"Validation error (HTTP 422): {exc}"
    if "500" in msg or "Internal Server Error" in msg:
        return f"Server error (HTTP 500): {exc}"

    # File errors
    if isinstance(exc, FileNotFoundError):
        return f"File not found: {exc}"
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc}"

    return f"Error: {exc}"


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
        if not key or not key.strip():
            print(f"Error: empty key in input pair '={value}'", file=sys.stderr)
            sys.exit(1)
        # Try to parse JSON values (numbers, booleans, arrays, objects)
        try:
            result[key] = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            result[key] = value
    return result


_MAX_INPUT_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _load_input_file(path: str) -> dict[str, Any]:
    """Load workflow input data from a JSON file."""
    try:
        import os as _os

        file_size = _os.path.getsize(path)
        if file_size > _MAX_INPUT_FILE_SIZE:
            print(
                f"Error: input file too large ({file_size} bytes, max {_MAX_INPUT_FILE_SIZE}).",
                file=sys.stderr,
            )
            sys.exit(1)
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
    start = time.monotonic()
    max_wait = 3600
    try:
        while True:
            if time.monotonic() - start > max_wait:
                print(f"\r\033[KTimeout: run did not complete within {max_wait}s")
                return {"id": run_id, "status": "timeout"}
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
    except KeyboardInterrupt:
        print("\r\033[KInterrupted.")
        print("\n(Note: the run is still executing on the server)")
        return {"id": run_id, "status": "interrupted"}


def _stream_run(client: Any, run_id: str) -> None:
    """Poll a run every 2s and print step-by-step progress with colors.

    Shows each step's status as it transitions (running -> done/failed),
    then prints a summary line when the run reaches a terminal state.
    Ctrl+C prints a note that the run continues in the background.
    """
    terminal = {
        "completed", "failed", "cancelled", "error",
        "partial", "budget_exceeded", "awaiting_approval",
    }
    start = time.monotonic()
    max_wait = 3600
    printed_steps: dict[str, str] = {}  # step_id -> last printed status
    workflow_name: str | None = None
    total_steps: int = 0

    def _step_line(idx: int, total: int, step: dict, is_update: bool = False) -> str:
        """Format a single step progress line."""
        step_id = step.get("step_id", "?")
        status = step.get("status", "unknown").lower()
        cost = step.get("cost_usd", 0) or 0
        duration = step.get("duration_seconds", 0) or 0
        responsibility = step.get("responsibility", "")

        prefix = f"  [{idx}/{total}] {step_id}..."

        if status in ("completed", "success"):
            detail = f"done ({duration:.1f}s, ${cost:.2f})"
            if responsibility:
                detail += f" [{responsibility}]"
            return f"\r\033[K{prefix} {_color(detail, _C.GREEN)}"
        elif status in ("failed", "error"):
            detail = f"FAILED ({duration:.1f}s)"
            error_msg = step.get("error", "")
            if error_msg:
                detail += f" - {error_msg[:60]}"
            return f"\r\033[K{prefix} {_color(detail, _C.RED)}"
        elif status == "running":
            detail = "running..."
            if responsibility:
                detail += f" [{responsibility}]"
            return f"\r\033[K{prefix} {_color(detail, _C.YELLOW)}"
        elif status == "skipped":
            return f"\r\033[K{prefix} {_color('skipped', _C.GRAY)}"
        else:
            return f"\r\033[K{prefix} {status}"

    try:
        while True:
            if time.monotonic() - start > max_wait:
                print(f"\nTimeout: run did not complete within {max_wait}s")
                return

            run = client.get_run(run_id)
            r = _to_dict(run)
            run_status = _attr(run, "status", "unknown")

            if workflow_name is None:
                workflow_name = r.get("workflow_name", "workflow")
                print(f"\n{_color('Running:', _C.BOLD)} {workflow_name}")

            steps = r.get("steps") or []
            if total_steps == 0 and steps:
                total_steps = len(steps)

            for i, s_raw in enumerate(steps, 1):
                s = _to_dict(s_raw)
                sid = s.get("step_id", f"step-{i}")
                s_status = s.get("status", "pending").lower()

                prev = printed_steps.get(sid)

                # Print if new step or status changed
                if prev != s_status and s_status != "pending":
                    # If updating from running -> done, overwrite
                    if prev == "running":
                        sys.stdout.write("\033[A")  # move cursor up
                    line = _step_line(i, total_steps or len(steps), s)
                    print(line)
                    printed_steps[sid] = s_status

            if run_status in terminal:
                elapsed = time.monotonic() - start
                total_cost = r.get("total_cost_usd", 0) or 0
                passed = sum(
                    1 for s_raw in steps
                    if _to_dict(s_raw).get("status", "").lower()
                    in ("completed", "success")
                )
                step_total = len(steps)

                print()
                if run_status in ("completed", "success"):
                    summary = (
                        f"{_color('Completed', _C.GREEN)} in {elapsed:.1f}s"
                        f" | Cost: ${total_cost:.2f}"
                        f" | {passed}/{step_total} steps passed"
                    )
                elif run_status in ("failed", "error"):
                    summary = (
                        f"{_color('Failed', _C.RED)} after {elapsed:.1f}s"
                        f" | Cost: ${total_cost:.2f}"
                        f" | {passed}/{step_total} steps passed"
                    )
                    err = r.get("error", "")
                    if err:
                        summary += f"\n  {_color('Error:', _C.RED)} {err}"
                else:
                    summary = (
                        f"{_status_color(run_status)} after {elapsed:.1f}s"
                        f" | Cost: ${total_cost:.2f}"
                        f" | {passed}/{step_total} steps passed"
                    )
                print(summary)
                print()

                if run_status in ("failed", "error", "budget_exceeded"):
                    sys.exit(2)
                return

            time.sleep(2)

    except KeyboardInterrupt:
        print(f"\n\n{_color('Run continues in background.', _C.YELLOW)}"
              f" Check status with: sandcastle status {run_id}")


# ---------------------------------------------------------------------------
# Retro banner & boot sequence
# ---------------------------------------------------------------------------


def _print_spark_banner() -> None:
    """Print the Spark Mode unlock banner (only on a detected DGX Spark)."""
    if not _C.supports_color() or not sys.stdout.isatty():
        return

    from sandcastle.config import settings
    from sandcastle.engine.spark import get_spark_info

    info = get_spark_info()
    RST = "\033[0m"
    DIM = "\033[2m"
    MAG = "\033[1;35m"
    GRN = "\033[1;32m"

    hw = []
    if info.gpu_name:
        hw.append(info.gpu_name)
    if info.unified_mem_gb:
        hw.append(f"{info.unified_mem_gb}GB unified")
    hw_line = " · ".join(hw) if hw else "DGX Spark"

    print()
    print(f"  {MAG}⚡ SPARK MODE{RST}  {DIM}— {hw_line}{RST}")
    print(f"  {DIM}{'─' * 48}{RST}")
    workers = settings.max_concurrent_sandboxes
    print(f"  {GRN}[✓]{RST} Local models · {GRN}$0.00/run{RST} · data stays on-box")
    print(f"  {GRN}[✓]{RST} Concurrency auto-tuned to {workers} workers")
    print()


def _print_banner() -> None:
    """Print 80s retro startup banner with Knight Rider boot sequence."""
    if not _C.supports_color() or not sys.stdout.isatty():
        return

    from sandcastle import __version__

    RST = "\033[0m"
    DIM = "\033[2m"
    AMB = "\033[33m"
    BAMB = "\033[1;33m"
    CYN = "\033[36m"
    BCYN = "\033[1;36m"
    WHT = "\033[1;97m"
    GRN = "\033[1;32m"
    MAG = "\033[35m"

    # --- Sand castle ---
    print()
    # Castle towers with window glow
    tw = f"{BAMB}█{DIM}░{RST}{BAMB}█{RST}"  # tower window
    waves = "~\u2248" * 10 + "~"
    art = [
        f"              {AMB}▄█▄{RST}     {AMB}▄█▄{RST}",
        f"              {AMB}███ ▄█▄ ███{RST}",
        f"              {tw} {tw} {tw}",
        f"            {AMB}▄▄███▄███▄███▄▄{RST}",
        f"            {BAMB}█{DIM}░░░░░{BAMB}▫▫{DIM}░░░░░{BAMB}█{RST}",
        f"            {AMB}▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀{RST}",
        f"         {CYN}{waves}{RST}",
    ]
    for line in art:
        print(line)
        time.sleep(0.025)

    # --- SAND in block text (amber gradient) ---
    print()
    sand = [
        " ███████╗ █████╗ ███╗   ██╗██████╗ ",
        " ██╔════╝██╔══██╗████╗  ██║██╔══██╗",
        " ███████╗███████║██╔██╗ ██║██║  ██║",
        " ╚════██║██╔══██║██║╚██╗██║██║  ██║",
        " ███████║██║  ██║██║ ╚████║██████╔╝",
        " ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═════╝",
    ]
    # --- CASTLE in block text (bright white) ---
    castle = [
        "  ██████╗ █████╗ ███████╗████████╗██╗     ███████╗",
        " ██╔════╝██╔══██╗██╔════╝╚══██╔══╝██║     ██╔════╝",
        " ██║     ███████║███████╗   ██║   ██║     █████╗  ",
        " ██║     ██╔══██║╚════██║   ██║   ██║     ██╔══╝  ",
        " ╚██████╗██║  ██║███████║   ██║   ███████╗███████╗",
        "  ╚═════╝╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝╚══════╝",
    ]

    # Gradient: top rows bright amber, bottom rows dim amber
    colors_s = [BAMB, BAMB, BAMB, AMB, AMB, f"{DIM}{AMB}"]
    for i, line in enumerate(sand):
        print(f"  {colors_s[i]}{line}{RST}")
        time.sleep(0.018)
    colors_c = [WHT, WHT, WHT, f"{DIM}{WHT}", f"{DIM}{WHT}", f"{DIM}"]
    for i, line in enumerate(castle):
        print(f"  {colors_c[i]}{line}{RST}")
        time.sleep(0.018)

    print()
    print(f"  {DIM}{'─' * 54}{RST}")
    print(f"  {DIM}workflow orchestrator{RST}{'':>20}{CYN}v{__version__}{RST}")
    print()

    # --- Knight Rider scanner (KITT) ---
    bar_w = 42
    for frame in range(bar_w * 2):
        pos = frame if frame < bar_w else bar_w * 2 - frame - 1
        chars: list[str] = []
        for i in range(bar_w):
            d = abs(i - pos)
            if d == 0:
                chars.append(f"{WHT}█{RST}")
            elif d == 1:
                chars.append(f"{BAMB}▓{RST}")
            elif d == 2:
                chars.append(f"{AMB}▒{RST}")
            elif d == 3:
                chars.append(f"{DIM}░{RST}")
            else:
                chars.append(f"{DIM}·{RST}")
        sys.stdout.write(f"\r  [{(''.join(chars))}{RST}]")
        sys.stdout.flush()
        time.sleep(0.014)

    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

    # --- Boot sequence with progress bars ---
    systems = [
        ("SANDBOX ENGINE", AMB),
        ("WORKFLOW DAG", CYN),
        ("TEMPLATE REGISTRY", MAG),
        ("API SERVER", BCYN),
        ("DASHBOARD", BAMB),
    ]
    seg = 16
    for name, clr in systems:
        dots = "\u00b7" * (22 - len(name))
        for s in range(seg + 1):
            pct = int(s / seg * 100)
            filled = f"{clr}{'█' * s}{RST}"
            empty = f"{DIM}{'░' * (seg - s)}{RST}"
            sys.stdout.write(
                f"\r  {DIM}{name}{RST} {DIM}{dots}{RST} [{filled}{empty}] {DIM}{pct:3d}%{RST}"
            )
            sys.stdout.flush()
            time.sleep(0.010)
        bar_full = f"{clr}{'█' * seg}{RST}"
        sys.stdout.write(
            f"\r  {DIM}{name}{RST} {DIM}{dots}{RST} [{bar_full}] {GRN} OK {RST}\n"
        )
        sys.stdout.flush()

    print(f"\n  {GRN}ALL SYSTEMS ONLINE{RST}\n")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> None:
    """Interactive setup wizard - create .env and workflows directory."""
    if not sys.stdin.isatty():
        print(
            "Error: 'sandcastle init' requires an interactive terminal.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        _cmd_init_inner(args)
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled.")
        return


def _cmd_init_inner(args: argparse.Namespace) -> None:
    from pathlib import Path

    env_path = Path(".env")

    _print_banner()
    print(_color("  SETUP WIZARD", _C.BOLD))
    print(_color("  ────────────", _C.BOLD))
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
    def _quote(val: str) -> str:
        """Wrap a value in double quotes, escaping embedded quotes."""
        return '"' + val.replace('\\', '\\\\').replace('"', '\\"') + '"'

    lines = [
        f"ANTHROPIC_API_KEY={_quote(anthropic_key)}",
        f"E2B_API_KEY={_quote(e2b_key)}" if e2b_key else "# E2B_API_KEY=",
        "",
        "# Sandbox backend: e2b | docker | local | cloudflare",
        f"SANDBOX_BACKEND={_quote(sandbox_backend)}",
    ]
    if cf_worker_url:
        lines.append(f"CLOUDFLARE_WORKER_URL={_quote(cf_worker_url)}")
    lines += [
        "",
        "# Multi-model provider keys (optional)",
        f"MINIMAX_API_KEY={_quote(minimax_key)}" if minimax_key else "# MINIMAX_API_KEY=",
        f"OPENAI_API_KEY={_quote(openai_key)}" if openai_key else "# OPENAI_API_KEY=",
        f"OPENROUTER_API_KEY={_quote(openrouter_key)}" if openrouter_key else "# OPENROUTER_API_KEY=",
        "",
        "# License key (Pro/Enterprise)",
        f"LICENSE_KEY={_quote(license_key)}" if license_key else "# LICENSE_KEY=",
        "",
        "# Local mode (SQLite + in-process queue) - leave empty",
        "DATABASE_URL=",
        "REDIS_URL=",
    ]
    env_path.write_text("\n".join(lines) + "\n")
    # Restrict .env file permissions to owner-only (contains secrets)
    try:
        env_path.chmod(0o600)
    except OSError:
        pass  # chmod may fail on some filesystems (e.g. FAT32)

    # Create default directories with restricted permissions
    from sandcastle.config import _DEFAULT_DATA_DIR, _DEFAULT_WORKFLOWS_DIR

    for dir_path in (_DEFAULT_DATA_DIR, _DEFAULT_WORKFLOWS_DIR):
        p = Path(dir_path)
        p.mkdir(parents=True, exist_ok=True)
        try:
            p.chmod(0o700)
        except OSError:
            pass  # chmod may fail on some filesystems

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

    from sandcastle.config import settings

    _print_banner()
    if settings.spark_mode:
        _print_spark_banner()

    # Non-blocking update check (at most once every 24h)
    try:
        auto_check = os.getenv("AUTO_UPDATE_CHECK", "true").lower() not in ("0", "false", "no")
        if auto_check:
            _check_for_updates_on_startup()
    except Exception:
        pass  # Never let update check crash the server

    port = args.port
    if not (1 <= port <= 65535):
        print(
            f"  {_color('Error: port must be between 1 and 65535, got ' + str(port), _C.RED)}",
            file=sys.stderr,
        )
        sys.exit(1)

    if _port_in_use(port):
        print(
            f"\n  {_color('Port ' + str(port) + ' is already in use.', _C.YELLOW)}"
        )

        if not sys.stdin.isatty():
            # Non-interactive mode - cannot prompt, exit with clear message
            print(
                f"  {_color('Cannot prompt in non-interactive mode. Free port ' + str(port) + ' or use --port to pick another.', _C.RED)}",
                file=sys.stderr,
            )
            sys.exit(1)

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

    # Check memory backend health
    try:
        import asyncio

        from sandcastle.engine.memory import memory_health_check

        health = asyncio.run(memory_health_check())
        if health.get("status") == "ok":
            print(
                f"  {_color('Agent Memory', _C.DIM)} .......... "
                f"{_color('OK', _C.GREEN)} {_color(str(health.get('latency_ms', '?')) + 'ms', _C.DIM)}"
            )
        else:
            print(
                f"  {_color('Agent Memory', _C.DIM)} .......... "
                f"{_color('UNAVAILABLE', _C.YELLOW)} - {health.get('error', 'unknown')}"
            )
    except Exception as e:
        print(
            f"  {_color('Agent Memory', _C.DIM)} .......... "
            f"{_color('UNAVAILABLE', _C.YELLOW)} - {e}"
        )

    uvicorn.run(
        "sandcastle.main:app",
        host=args.host,
        port=port,
        reload=args.reload,
    )


async def _node_join_loop(
    coordinator: str,
    token: str,
    name: str,
    base_url: str,
    capabilities: list[str],
    heartbeat_seconds: int,
) -> None:
    """Register with the coordinator and heartbeat forever.

    Re-registers automatically when the coordinator forgets us (404 on
    heartbeat, e.g. after a coordinator DB reset).
    """
    import asyncio

    import httpx

    headers = {"X-Mesh-Token": token}
    register_body = {"name": name, "base_url": base_url, "capabilities": capabilities}

    async with httpx.AsyncClient(timeout=15) as client:

        async def _register() -> str | None:
            try:
                resp = await client.post(
                    f"{coordinator}/api/mesh/register", json=register_body, headers=headers
                )
            except httpx.HTTPError as exc:
                print(f"  {_color('Coordinator unreachable: ' + str(exc), _C.YELLOW)}")
                return None
            if resp.status_code != 200:
                print(
                    f"  {_color('Registration rejected: HTTP ' + str(resp.status_code), _C.RED)}"
                    f" {resp.text[:200]}"
                )
                return None
            node = resp.json().get("data") or {}
            print(
                f"  {_color('Joined mesh', _C.GREEN)} as "
                f"{_color(name, _C.BOLD)} ({base_url}) "
                f"caps={_color(','.join(capabilities), _C.CYAN)} id={node.get('id')}"
            )
            return node.get("id")

        node_id = None
        while node_id is None:
            node_id = await _register()
            if node_id is None:
                await asyncio.sleep(heartbeat_seconds)

        while True:
            await asyncio.sleep(heartbeat_seconds)
            try:
                resp = await client.post(
                    f"{coordinator}/api/mesh/heartbeat",
                    json={"node_id": node_id},
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                print(f"  {_color('Heartbeat failed: ' + str(exc), _C.YELLOW)}")
                continue
            if resp.status_code == 404:
                print(f"  {_color('Coordinator forgot us - re-registering', _C.YELLOW)}")
                node_id = await _register() or node_id
            elif resp.status_code != 200:
                print(
                    f"  {_color('Heartbeat rejected: HTTP ' + str(resp.status_code), _C.YELLOW)}"
                )


def _cmd_node(args: argparse.Namespace) -> None:
    """Sandcastle Mesh node commands (currently: join)."""
    if getattr(args, "node_action", None) != "join":
        print("Usage: sandcastle node join <coordinator-url> --token <t>", file=sys.stderr)
        sys.exit(1)

    import asyncio
    import socket

    coordinator = args.coordinator.rstrip("/")
    token = args.token or os.getenv("MESH_TOKEN", "")
    if not token:
        print(
            f"  {_color('A mesh token is required: --token <t> or MESH_TOKEN env.', _C.RED)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Enable the mesh plane on this node's own API server BEFORE the app
    # starts, so /api/mesh/execute-step accepts the coordinator's token.
    os.environ["MESH_ENABLED"] = "true"
    os.environ["MESH_TOKEN"] = token
    from sandcastle.config import settings

    settings.mesh_enabled = True
    settings.mesh_token = token

    if args.capabilities:
        capabilities = [
            c.strip().lower() for c in args.capabilities.split(",") if c.strip()
        ]
    else:
        from sandcastle.engine.mesh import detect_local_capabilities

        capabilities = detect_local_capabilities()

    hostname = socket.gethostname()
    name = args.name or hostname
    base_url = (args.base_url or f"http://{hostname}:{args.port}").rstrip("/")
    heartbeat_seconds = args.heartbeat or settings.mesh_heartbeat_seconds

    _print_banner()
    print(f"  {_color('Sandcastle Mesh node', _C.BOLD)}")
    print(f"  Coordinator ........ {coordinator}")
    print(f"  Node ............... {name} ({base_url})")
    print(f"  Capabilities ....... {_color(','.join(capabilities), _C.CYAN)}")
    print(f"  Heartbeat .......... every {heartbeat_seconds}s\n")

    if not args.no_serve:
        # Serve this node's own API (the /api/mesh/execute-step surface)
        # in a background thread; the heartbeat loop owns the foreground.
        import threading

        import uvicorn

        config = uvicorn.Config(
            "sandcastle.main:app", host=args.host, port=args.port, log_level="warning"
        )
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True, name="mesh-node-api").start()
        print(f"  {_color('Node API serving on port ' + str(args.port), _C.GREEN)}")

    try:
        asyncio.run(
            _node_join_loop(
                coordinator, token, name, base_url, capabilities, heartbeat_seconds
            )
        )
    except KeyboardInterrupt:
        print(f"\n  {_color('Node left the mesh.', _C.DIM)}")


def _run_local(
    workflow: str,
    input_data: dict[str, Any],
    max_cost: float | None,
    record: str | None = None,
    replay: str | None = None,
) -> None:
    """Execute a workflow in-process, without a running server.

    Resolves *workflow* as a path to a .yaml file or as a built-in template name,
    builds the plan, and runs it through the engine executor directly. The local
    caller is trusted, so code/transform steps are allowed. Run-step persistence is
    best-effort and silently skipped when no database is configured. With *record* /
    *replay* the run is taped to / replayed from a deterministic cassette file.
    """
    import asyncio
    import tempfile
    from pathlib import Path

    from sandcastle.engine.cassette import CassetteStore
    from sandcastle.engine.dag import build_plan, parse_yaml_string
    from sandcastle.engine.executor import execute_workflow
    from sandcastle.engine.storage import LocalStorage

    cassette = None
    cassette_mode: str | None = None
    try:
        if record:
            cassette, cassette_mode = CassetteStore(record, "record"), "record"
        elif replay:
            cassette, cassette_mode = CassetteStore(replay, "replay"), "replay"
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    wf_path = Path(workflow)
    try:
        if wf_path.suffix in (".yaml", ".yml") and wf_path.exists():
            content = wf_path.read_text()
        else:
            from sandcastle.templates import get_template

            content, _ = get_template(workflow)
        wf = parse_yaml_string(content)
        plan = build_plan(wf)
    except FileNotFoundError:
        print(
            f"Error: workflow '{workflow}' not found as a file or a built-in template.",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(_format_cli_error(exc), file=sys.stderr)
        sys.exit(1)

    storage = LocalStorage(tempfile.mkdtemp(prefix="sandcastle-local-"))
    print(_color(f"Running '{wf.name}' locally (in-process, no server)...", _C.CYAN))

    async def _local_async() -> Any:
        import uuid

        run_id = str(uuid.uuid4())
        persisted = False
        # Best-effort: set up the local DB and a parent run row so step/audit
        # persistence works and the run shows up in the dashboard. When no DB is
        # available this is silently skipped and the run still executes.
        try:
            from sandcastle.models.db import Run, RunStatus, async_session, init_db

            await init_db()
            async with async_session() as session:
                session.add(
                    Run(
                        id=uuid.UUID(run_id),
                        workflow_name=wf.name,
                        status=RunStatus.RUNNING,
                        input_data=input_data,
                        max_cost_usd=max_cost,
                    )
                )
                await session.commit()
            persisted = True
        except Exception:
            persisted = False

        wf_result = await execute_workflow(
            workflow=wf,
            plan=plan,
            input_data=input_data,
            run_id=run_id if persisted else None,
            storage=storage,
            max_cost_usd=max_cost,
            admin_trusted=True,
            cassette=cassette,
            cassette_mode=cassette_mode,
        )

        if persisted:
            try:
                from sandcastle.models.db import Run, RunStatus, async_session

                status_map = {
                    "completed": RunStatus.COMPLETED,
                    "failed": RunStatus.FAILED,
                    "error": RunStatus.FAILED,
                    "partial": RunStatus.PARTIAL,
                    "cancelled": RunStatus.CANCELLED,
                    "budget_exceeded": RunStatus.BUDGET_EXCEEDED,
                }
                async with async_session() as session:
                    db_run = await session.get(Run, uuid.UUID(run_id))
                    if db_run is not None:
                        db_run.status = status_map.get(
                            str(_attr(wf_result, "status", "")), RunStatus.COMPLETED
                        )
                        db_run.total_cost_usd = _attr(wf_result, "total_cost_usd", 0.0)
                        db_run.output_data = _attr(wf_result, "outputs", {})
                        db_run.error = _attr(wf_result, "error", None)
                        await session.commit()
            except Exception:
                pass
        return wf_result

    try:
        result = asyncio.run(_local_async())
    except Exception as exc:
        print(_format_cli_error(exc), file=sys.stderr)
        sys.exit(1)

    status = _attr(result, "status", "unknown")
    run_id = _attr(result, "run_id", "")
    cost = _attr(result, "total_cost_usd", 0.0)
    error = _attr(result, "error", None)
    outputs = _attr(result, "outputs", {}) or {}

    print(f"{_status_color(str(status))}  run {run_id}  cost ${float(cost or 0.0):.4f}")
    if cassette_mode == "record" and cassette is not None:
        print(_color(f"Recorded cassette -> {record} ({len(cassette.records)} steps)", _C.GREEN))
    elif cassette_mode == "replay" and cassette is not None:
        verified = "verified" if cassette.verify() else "SIGNATURE MISMATCH"
        print(
            _color(
                f"Replayed from {replay} ({cassette.replay_hits} hits, "
                f"{cassette.replay_misses} live misses, signature {verified})",
                _C.GREEN,
            )
        )
    if error:
        print(_color(f"Error: {error}", _C.RED), file=sys.stderr)
    if outputs:
        print(_color("Outputs:", _C.BOLD))
        print(json.dumps(outputs, indent=2, default=str))
    if str(status) in ("failed", "error", "budget_exceeded"):
        sys.exit(2)


def _cmd_run(args: argparse.Namespace) -> None:
    """Run a workflow via the SDK client."""
    from pathlib import Path

    # Validate max_cost if provided
    if args.max_cost is not None and args.max_cost < 0:
        print("Error: --max-cost cannot be negative.", file=sys.stderr)
        sys.exit(1)

    # Validate workflow name - block path traversal for non-file names
    workflow_name = args.workflow
    if not workflow_name or not workflow_name.strip():
        print("Error: workflow name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    # Local in-process execution path - no running server required.
    record = getattr(args, "record", None)
    replay = getattr(args, "replay", None)
    if record and replay:
        print("Error: use only one of --record / --replay.", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "local", False) or record or replay:
        input_data: dict[str, Any] = {}
        if args.input_file:
            input_data = _load_input_file(args.input_file)
        input_data.update(_parse_input_pairs(args.input))
        _run_local(workflow_name, input_data, args.max_cost, record=record, replay=replay)
        return

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
        print(_format_cli_error(exc), file=sys.stderr)
        sys.exit(1)

    run_id = _attr(run, "run_id", str(run))

    if getattr(args, "stream", False):
        _stream_run(client, run_id)
    elif args.wait:
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
    _validate_run_id(args.run_id)
    client = _get_client(args)
    try:
        run = client.get_run(args.run_id)
    except Exception as exc:
        print(_format_cli_error(exc), file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(_to_dict(run), indent=2, default=str))
        return

    _print_run_detail(run)


def _cmd_cancel(args: argparse.Namespace) -> None:
    """Cancel a running workflow."""
    _validate_run_id(args.run_id)
    client = _get_client(args)
    try:
        result = client.cancel_run(args.run_id)
    except Exception as exc:
        print(_format_cli_error(exc), file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(
            result if isinstance(result, dict)
            else {"cancelled": True, "run_id": args.run_id},
            indent=2, default=str,
        ))
        return

    print(f"Cancelled run {args.run_id}")


def _cmd_share(args: argparse.Namespace) -> None:
    """Mint (or revoke) a public, scrubbed share permalink for a run."""
    _validate_run_id(args.run_id)
    client = _get_client(args)
    base = getattr(client, "_base_url", "")
    revoke = getattr(args, "revoke", False)
    try:
        if revoke:
            resp = client._client.delete(f"/api/runs/{args.run_id}/share")
        else:
            resp = client._client.post(f"/api/runs/{args.run_id}/share")
    except Exception as exc:
        print(_format_cli_error(exc), file=sys.stderr)
        sys.exit(1)

    if resp.status_code >= 400:
        print(f"Error: HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        sys.exit(1)

    if revoke:
        print(_color("Share link revoked.", _C.GREEN))
        return

    data = resp.json().get("data", {})
    print(_color(f"Public run link: {base}{data.get('share_path', '')}", _C.GREEN))
    print(_color("Secrets and PII are scrubbed on the public page.", _C.GRAY))


def _cmd_logs(args: argparse.Namespace) -> None:
    """Stream SSE events for a run."""
    _validate_run_id(args.run_id)
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
            display = (
                {k: v for k, v in event.items() if k != "_event"}
                if isinstance(event, dict)
                else event
            )
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
        print(_format_cli_error(exc), file=sys.stderr)
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
    if limit < 1:
        print("Error: --limit must be at least 1.", file=sys.stderr)
        sys.exit(1)
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


def _cmd_publish_mcp(args: argparse.Namespace) -> None:
    """List or emit MCP client config for publishable workflows.

    With no workflow argument: prints a JSON list of every workflow that
    can be exposed as an MCP tool, with the tool name + description.

    With a workflow name: prints a Claude Desktop / Cursor 'mcpServers'
    config snippet (JSON to stdout) and short paste instructions to stderr.
    """
    try:
        from sandcastle.mcp_server import (
            build_client_config,
            discover_publishable_workflows,
        )
    except ImportError:
        print(
            "Error: MCP support requires the 'mcp' package.\n"
            "Install it with: pip install sandcastle-ai[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    workflow = getattr(args, "workflow", None)
    if not workflow:
        # Listing mode - emit JSON-parseable inventory.
        rows = [
            {
                "name": wf["name"],
                "tool_name": wf["tool_name"],
                "description": wf["description"],
                "source_path": wf["source_path"],
            }
            for wf in discover_publishable_workflows()
        ]
        print(json.dumps(rows, indent=2))
        return

    # Snippet mode - emit a ready-to-paste mcpServers config block.
    available = discover_publishable_workflows()
    names = {wf["name"] for wf in available}
    if workflow not in names:
        print(
            f"Error: workflow '{workflow}' is not publishable. "
            f"Run 'sandcastle publish-mcp' to see the list.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = build_client_config(workflow)
    print(json.dumps(config, indent=2))
    print(
        "\nPaste the snippet above into your MCP client config:\n"
        "  - Claude Desktop: ~/Library/Application Support/Claude/"
        "claude_desktop_config.json (macOS)\n"
        "                    %APPDATA%/Claude/claude_desktop_config.json (Windows)\n"
        "  - Cursor:         ~/.cursor/mcp.json\n"
        "  - Windsurf:       ~/.codeium/windsurf/mcp_config.json\n"
        "\nThen restart the client. Sandcastle will appear as a new MCP server.",
        file=sys.stderr,
    )


def _cmd_publish_skills(args: argparse.Namespace) -> None:
    """Publish workflows as Anthropic Skills.

    Without ``--upload``: dry-run, prints a JSON list of workflows that would
    be published (one entry per .yaml/.yml file under the workflows dir).
    With ``--upload``: invokes ``publish_workflows_as_skills(dry_run=False)``
    so each skill is POSTed via :class:`AnthropicSkillsClient`. ``--dir``
    overrides ``settings.workflows_dir``.
    """
    import asyncio

    from sandcastle.config import settings
    from sandcastle.engine.agent_skills import (
        AnthropicSkillsClient,
        SkillValidationError,
        publish_workflows_as_skills,
    )

    workflow_dir = getattr(args, "dir", None) or settings.workflows_dir
    upload = bool(getattr(args, "upload", False))

    async def _run() -> list[dict]:
        client: AnthropicSkillsClient | None = None
        if upload:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is required for --upload"
                )
            client = AnthropicSkillsClient(api_key=api_key)
        return await publish_workflows_as_skills(
            workflow_dir=workflow_dir,
            dry_run=not upload,
            client=client,
        )

    try:
        results = asyncio.run(_run())
    except SkillValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(results, indent=2, default=str))


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

    port = int(os.getenv("PORT", "8080"))
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

    # --- Section 6: Database & Redis ---
    print()
    print(_color("  Database & Redis", _C.BOLD))
    print(_color("  ----------------", _C.BOLD))

    if cfg:
        db_url = cfg.database_url
        if not db_url or db_url.startswith("sqlite"):
            db_label = db_url or "sqlite (default, in-memory/local)"
            # SQLite requires no connectivity check
            _pass(f"Database: {db_label}")
        elif db_url.startswith("postgresql"):
            _pass("Database: PostgreSQL configured")
            try:
                import sqlalchemy  # noqa: F401
                _pass("SQLAlchemy installed")
            except ImportError:
                _fail("SQLAlchemy not installed (needed for PostgreSQL)")
            # Try to connect
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(3)
                    # Parse host:port from database URL
                    from urllib.parse import urlparse
                    parsed = urlparse(db_url)
                    db_host = parsed.hostname or "localhost"
                    db_port = parsed.port or 5432
                    result = s.connect_ex((db_host, db_port))
                    if result == 0:
                        _pass(f"PostgreSQL reachable at {db_host}:{db_port}")
                    else:
                        _warn(f"PostgreSQL unreachable at {db_host}:{db_port}")
            except Exception as e:
                _warn(f"Could not check PostgreSQL connectivity: {e}")
        else:
            _pass(f"Database: {db_url[:30]}...")

        redis_url = cfg.redis_url
        if redis_url:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(redis_url)
                redis_host = parsed.hostname or "localhost"
                redis_port = parsed.port or 6379
            except Exception:
                redis_host = "unknown"
                redis_port = 6379
            _pass(f"Redis URL configured ({redis_host}:{redis_port})")
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(3)
                    result = s.connect_ex((redis_host, redis_port))
                    if result == 0:
                        _pass(f"Redis reachable at {redis_host}:{redis_port}")
                    else:
                        _warn(f"Redis unreachable at {redis_host}:{redis_port}")
            except Exception as e:
                _warn(f"Could not check Redis connectivity: {e}")
        else:
            _pass("Redis not configured (using in-process queue)")

    # --- Section 7: Telemetry ---
    print()
    print(_color("  Telemetry", _C.BOLD))
    print(_color("  ---------", _C.BOLD))

    if cfg and cfg.telemetry_enabled and cfg.sentry_dsn:
        try:
            importlib.import_module("sentry_sdk")
            _pass("Sentry DSN configured")
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

    # --- Section 9: Workflow Doctor (if workflow file specified) ---
    workflow_file = getattr(args, "workflow", None)
    if workflow_file:
        print()
        print(_color("  Workflow Doctor", _C.BOLD))
        print(_color("  ---------------", _C.BOLD))

        wf_path = Path(workflow_file)
        if not wf_path.exists():
            _fail(f"Workflow file not found: {workflow_file}")
        else:
            try:
                from sandcastle.engine.doctor import diagnose_yaml

                yaml_content = wf_path.read_text()
                report = diagnose_yaml(yaml_content)

                risk_colors = {
                    "LOW": _C.GREEN, "MEDIUM": _C.YELLOW,
                    "HIGH": _C.RED, "CRITICAL": _C.RED,
                }
                risk_color = risk_colors.get(report.risk, _C.RESET)
                print(f"  Workflow: {report.workflow_name}")
                print(f"  Risk: {_color(report.risk, risk_color)}")
                print()

                if report.blocking:
                    print(_color("  Blocking:", _C.RED))
                    for f in report.blocking:
                        step_label = f" [{f.step_id}]" if f.step_id else ""
                        print(f"    - {f.message}{step_label}")
                        if f.suggested_fix:
                            print(f"      Fix: {f.suggested_fix}")

                if report.warnings:
                    print(_color("  Warnings:", _C.YELLOW))
                    for f in report.warnings:
                        step_label = f" [{f.step_id}]" if f.step_id else ""
                        print(f"    - {f.message}{step_label}")
                        if f.suggested_fix:
                            print(f"      Fix: {f.suggested_fix}")

                if report.info:
                    print(_color("  Info:", _C.BLUE if hasattr(_C, "BLUE") else _C.RESET))
                    for f in report.info:
                        step_label = f" [{f.step_id}]" if f.step_id else ""
                        print(f"    - {f.message}{step_label}")

                if not report.findings:
                    _pass("No issues found")
                elif report.ok:
                    _pass(f"Ready ({len(report.warnings)} warning(s))")
                else:
                    _fail(f"Blocked ({len(report.blocking)} blocking issue(s))")
            except Exception as e:
                _fail(f"Could not diagnose workflow: {e}")

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
            print(f"Health check failed: {status}")
            sys.exit(1)
    except Exception as exc:
        print(_format_cli_error(exc), file=sys.stderr)
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

        out_path = Path(output_file)
        # Validate output file has a sane extension
        if out_path.suffix not in (".yaml", ".yml", ""):
            print(
                f"Warning: output file '{output_file}' does not have a .yaml/.yml extension.",
                file=sys.stderr,
            )
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(result.yaml_content)
        except OSError as exc:
            print(f"Error: failed to write output file: {exc}", file=sys.stderr)
            sys.exit(1)
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
    if concurrency < 1:
        print("Error: --concurrency must be at least 1.", file=sys.stderr)
        sys.exit(1)
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
        print(_format_cli_error(exc), file=sys.stderr)
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

    _validate_run_id(args.run_id)

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
        print(_format_cli_error(exc), file=sys.stderr)
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

    _validate_run_id(args.run_id)

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
        print(_format_cli_error(exc), file=sys.stderr)
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

    _validate_run_id(args.run_id)

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
        print(_format_cli_error(exc), file=sys.stderr)
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

    _validate_run_id(args.run_id)

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
        print(_format_cli_error(exc), file=sys.stderr)
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

    limit = getattr(args, "limit", 20)
    if limit < 1:
        print("Error: --limit must be at least 1.", file=sys.stderr)
        sys.exit(1)
    params: dict[str, Any] = {"limit": limit}
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
        print(_format_cli_error(exc), file=sys.stderr)
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
    """Find the pending approval_id for a given run_id by querying the API.

    Returns the approval_id if found, None if no pending approval exists.
    Exits with error on network/API failures so callers can distinguish
    "no pending approvals" from "could not reach the API".
    """
    import httpx

    try:
        resp = httpx.get(
            f"{base}/api/approvals",
            params={"status": "pending"},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(
            f"Error: failed to query approvals API: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

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

        # Show detailed error messages for failed steps
        failed_steps = [
            _to_dict(s) for s in steps if _to_dict(s).get("status") == "failed" and _to_dict(s).get("error")
        ]
        if failed_steps:
            print()
            print(_color("  Step Errors:", _C.RED))
            for fs in failed_steps:
                step_name = fs.get("step_id", "?")
                step_error = fs.get("error", "")
                attempt_num = fs.get("attempt", 1)
                print(f"    {_color('X', _C.RED)} {step_name} (attempt {attempt_num}): {step_error}")

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
_HUB_REGISTRY_MAX_SIZE = 5 * 1024 * 1024  # 5MB - prevent DoS via oversized registry
_HUB_MAX_DOWNLOAD_SIZE = 512 * 1024  # 512KB per template download


def _fetch_hub_registry() -> dict:
    """Fetch the community hub registry with size limit protection."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(_HUB_REGISTRY_URL, timeout=10) as resp:
            raw = resp.read(_HUB_REGISTRY_MAX_SIZE + 1)
            if len(raw) > _HUB_REGISTRY_MAX_SIZE:
                print(
                    f"{_color('Error', _C.RED)}: Hub registry exceeds size limit"
                    f" ({_HUB_REGISTRY_MAX_SIZE} bytes)",
                    file=sys.stderr,
                )
                sys.exit(1)
            data = json.loads(raw)
            if not isinstance(data, dict):
                print(
                    f"{_color('Error', _C.RED)}: Hub registry has invalid structure",
                    file=sys.stderr,
                )
                sys.exit(1)
            return data
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

    # Fetch the YAML content with size limit and redirect validation
    try:
        req = urllib.request.Request(download_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Validate final URL after any redirects
            final_url = resp.geturl()
            if final_url != download_url and not _validate_hub_download_url(final_url):
                print(
                    f"{_color('Error', _C.RED)}: Download redirected to untrusted URL: {final_url}",
                    file=sys.stderr,
                )
                sys.exit(1)
            raw = resp.read(_HUB_MAX_DOWNLOAD_SIZE + 1)
            if len(raw) > _HUB_MAX_DOWNLOAD_SIZE:
                print(
                    f"{_color('Error', _C.RED)}: Download exceeds size limit"
                    f" ({_HUB_MAX_DOWNLOAD_SIZE} bytes)",
                    file=sys.stderr,
                )
                sys.exit(1)
            yaml_content = raw.decode("utf-8")
    except SystemExit:
        raise
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
        try:
            answer = input("Continue anyway? [y/N] ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return
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

    # Overwrite protection - warn if file already exists
    if target_path.exists() and not force:
        if sys.stdin.isatty():
            try:
                answer = input(
                    f"  File '{target_path}' already exists. Overwrite? [y/N] "
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\nAborted.")
                return
            if answer not in ("y", "yes"):
                print("Installation cancelled.")
                return
        else:
            print(
                f"{_color('Error', _C.RED)}: File '{target_path}' already exists. "
                "Use --force to overwrite.",
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
    if limit < 1:
        print("Error: --limit must be at least 1.", file=sys.stderr)
        sys.exit(1)
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
                # Validate final URL after redirects
                final_url = resp.geturl()
                if final_url != dl_url and not _validate_hub_download_url(final_url):
                    print(f"  {_color('Skip', _C.YELLOW)}: {slug} (redirected to untrusted URL)")
                    continue
                raw = resp.read(_HUB_MAX_DOWNLOAD_SIZE + 1)
                if len(raw) > _HUB_MAX_DOWNLOAD_SIZE:
                    print(f"  {_color('Skip', _C.YELLOW)}: {slug} (exceeds size limit)")
                    continue
                yaml_content = raw.decode("utf-8")
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
# Verified template bundles (.sctpl) - pack / verify / install / search
# ---------------------------------------------------------------------------

_TEMPLATE_INDEX_MAX_SIZE = 5 * 1024 * 1024  # 5MB - prevent DoS via oversized index


def _sanitize_bundle_stem(name: str) -> str:
    """Sanitize a bundle/template name to a safe filename stem (no traversal)."""
    import re as _re

    base = name.split("/")[-1]
    safe = _re.sub(r"[^a-zA-Z0-9_\-]", "_", base).lstrip(".")
    return safe or "template"


def _cmd_pack(args: argparse.Namespace) -> None:
    """Pack a workflow + recorded cassettes into a distributable .sctpl bundle."""
    from sandcastle.engine.bundle import BundleError, create_bundle

    # Same resolution describe/lint use: file path, workflows dir, built-in template.
    wf_path = Path(_resolve_workflow_file(args.workflow))

    for cp in args.cassettes:
        if not Path(cp).exists():
            print(f"{_color('Error', _C.RED)}: cassette not found: {cp}", file=sys.stderr)
            sys.exit(1)

    example_inputs: dict[str, Any] = {}
    if args.input_file:
        example_inputs = _load_input_file(args.input_file)
    example_inputs.update(_parse_input_pairs(args.input))

    try:
        bundle_path = create_bundle(
            wf_path,
            list(args.cassettes),
            args.output,  # None -> <name>-<version>.sctpl in the working directory
            name=args.name,
            version=args.bundle_version,
            description=args.description,
            author=args.author,
            license_id=args.license_id,
            example_inputs=example_inputs,
            created_at=args.created_at,
        )
    except BundleError as exc:
        print(f"{_color('Error', _C.RED)}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"{_color('Packed', _C.GREEN)}: {bundle_path}")

    if getattr(args, "no_verify", False):
        return

    # Prove the bundle before it ships: a bundle that does not verify is not
    # a verified template, so pack fails loudly instead of producing one.
    print(_color("Verifying the freshly packed bundle...", _C.CYAN))
    if not _print_verify_report(str(bundle_path)):
        print(
            f"{_color('Error', _C.RED)}: the packed bundle does not verify - "
            "record the cassette with the same inputs you pass to pack "
            "(--input/--input-file), or use --no-verify to skip.",
            file=sys.stderr,
        )
        sys.exit(1)


def _print_verify_report(bundle: str, as_json: bool = False) -> bool:
    """Verify *bundle* and print a PASS/FAIL report. Returns overall pass."""
    from sandcastle.engine.bundle import verify_bundle

    result = verify_bundle(bundle)

    if as_json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "errors": result.errors,
                    "manifest": result.manifest,
                    "cassettes": [
                        {
                            "file": c.file,
                            "passed": c.passed,
                            "detail": c.detail,
                            "replay_hits": c.replay_hits,
                            "replay_misses": c.replay_misses,
                        }
                        for c in result.cassette_results
                    ],
                },
                indent=2,
                default=str,
            )
        )
        return result.ok

    manifest = result.manifest
    if manifest:
        print(
            f"  {manifest.get('name', '?')} v{manifest.get('version', '?')}"
            f"  by {manifest.get('author') or 'unknown'}"
            f"  (sandcastle {manifest.get('sandcastle_version', '?')})"
        )
    for err in result.errors:
        print(f"  {_color('FAIL', _C.RED)}  {err}")
    for c in result.cassette_results:
        mark = _color("PASS", _C.GREEN) if c.passed else _color("FAIL", _C.RED)
        print(f"  {mark}  {c.file}  {c.detail}")
    if result.ok:
        print(_color("Verified: the bundled cassette(s) replay the workflow at $0.", _C.GREEN))
    else:
        print(_color("Verification FAILED.", _C.RED))
    return result.ok


def _cmd_template_verify(args: argparse.Namespace) -> None:
    """Verify a bundle's proof-of-execution: checksums + strict offline replay."""
    ok = _print_verify_report(args.bundle, as_json=getattr(args, "json", False))
    sys.exit(0 if ok else 1)


def _download_bundle(url: str, dest: Path) -> None:
    """Download a bundle over https with timeout and size cap."""
    import urllib.request
    from urllib.parse import urlparse

    from sandcastle.engine.bundle import MAX_BUNDLE_SIZE

    if urlparse(url).scheme != "https":
        print(f"{_color('Error', _C.RED)}: bundle URLs must use https://", file=sys.stderr)
        sys.exit(1)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            raw = resp.read(MAX_BUNDLE_SIZE + 1)
        if len(raw) > MAX_BUNDLE_SIZE:
            print(
                f"{_color('Error', _C.RED)}: download exceeds size limit"
                f" ({MAX_BUNDLE_SIZE} bytes)",
                file=sys.stderr,
            )
            sys.exit(1)
        dest.write_bytes(raw)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"{_color('Error', _C.RED)}: failed to download bundle: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_template_install(args: argparse.Namespace) -> None:
    """Verify a bundle, then install its workflow where the wizard can find it."""
    import hashlib
    import tempfile

    from sandcastle.engine.bundle import read_bundle

    source = args.source
    force = getattr(args, "force", False)

    with tempfile.TemporaryDirectory(prefix="sctpl-install-") as tmp:
        if source.startswith(("http://", "https://")):
            bundle_path = Path(tmp) / "download.sctpl"
            _download_bundle(source, bundle_path)
        else:
            bundle_path = Path(source)
            if not bundle_path.exists():
                print(f"{_color('Error', _C.RED)}: bundle not found: {source}", file=sys.stderr)
                sys.exit(1)

        # Pin to the index checksum when given (sandcastle template search prints it).
        expected_sha = getattr(args, "sha256", None)
        if expected_sha:
            actual = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            if actual != expected_sha.lower().strip():
                print(
                    f"{_color('Error', _C.RED)}: bundle checksum mismatch\n"
                    f"  Expected: {expected_sha}\n  Got:      {actual}",
                    file=sys.stderr,
                )
                sys.exit(1)

        # Verify first - install refuses a failing bundle unless --force.
        if not _print_verify_report(str(bundle_path)):
            if not force:
                print(
                    f"{_color('Error', _C.RED)}: refusing to install an unverified bundle "
                    "(use --force to override).",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(_color("Installing anyway (--force).", _C.YELLOW))

        try:
            manifest, workflow_yaml, cassettes = read_bundle(bundle_path)
        except Exception as exc:
            print(f"{_color('Error', _C.RED)}: {exc}", file=sys.stderr)
            sys.exit(1)

        # Install into the community templates dir - the same place dashboard hub
        # installs land, so the Lite wizard and dashboard surface it immediately.
        if getattr(args, "dir", None):
            target_dir = Path(args.dir)
        else:
            from sandcastle.templates import _TEMPLATES_DIR

            target_dir = _TEMPLATES_DIR / "community"
        target_dir.mkdir(parents=True, exist_ok=True)

        stem = _sanitize_bundle_stem(str(manifest["name"]))
        target_path = (target_dir / f"{stem}.yaml").resolve()
        if not str(target_path).startswith(str(target_dir.resolve())):
            print(f"{_color('Error', _C.RED)}: unsafe template name in manifest", file=sys.stderr)
            sys.exit(1)
        if target_path.exists() and not force:
            print(
                f"{_color('Error', _C.RED)}: '{target_path}' already exists. "
                "Use --force to overwrite.",
                file=sys.stderr,
            )
            sys.exit(1)

        target_path.write_text(workflow_yaml)
        # Keep the proof next to the workflow so anyone can re-run the replay.
        for arcname, blob in cassettes.items():
            cassette_name = _sanitize_bundle_stem(Path(arcname).stem) + ".cassette.json"
            (target_dir / f"{stem}.{cassette_name}").write_bytes(blob)

    print(f"{_color('Installed', _C.GREEN)}: {manifest['name']} v{manifest['version']}"
          f" -> {target_path}")
    print(f"  Proof cassette(s): {len(cassettes)} (kept alongside for offline replay)")
    print(f"  Run it: sandcastle run --local {target_path}")


def _fetch_template_index() -> dict:
    """Fetch the verified template index with size limit protection."""
    import json
    import urllib.request

    from sandcastle.config import settings

    url = settings.template_index_url
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read(_TEMPLATE_INDEX_MAX_SIZE + 1)
            if len(raw) > _TEMPLATE_INDEX_MAX_SIZE:
                print(
                    f"{_color('Error', _C.RED)}: template index exceeds size limit"
                    f" ({_TEMPLATE_INDEX_MAX_SIZE} bytes)",
                    file=sys.stderr,
                )
                sys.exit(1)
            data = json.loads(raw)
            if not isinstance(data, dict):
                print(
                    f"{_color('Error', _C.RED)}: template index has invalid structure",
                    file=sys.stderr,
                )
                sys.exit(1)
            return data
    except SystemExit:
        raise
    except Exception as exc:
        print(
            f"{_color('Error', _C.RED)}: could not reach the template index at {url}\n"
            f"  ({exc})\n"
            "  Check your connection, or point TEMPLATE_INDEX_URL at your own index.json.",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_template_search(args: argparse.Namespace) -> None:
    """Search the verified template index (a static index.json of .sctpl bundles)."""
    index = _fetch_template_index()
    templates = index.get("templates", [])
    query = getattr(args, "query", "").strip().lower()

    if not query:
        print("Error: search query cannot be empty", file=sys.stderr)
        sys.exit(1)

    results = []
    for t in templates:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name", "")).lower()
        desc = str(t.get("description", "")).lower()
        tags = [str(tag).lower() for tag in t.get("tags", [])]
        if query in name or query in desc or any(query in tag for tag in tags):
            results.append(t)

    if getattr(args, "json", False):
        print(json.dumps(results, indent=2))
        return

    if not results:
        print(f"No verified templates found for query '{query}'.")
        return

    headers = ["NAME", "VERSION", "AUTHOR", "DESCRIPTION"]
    rows = [
        [
            str(t.get("name", "")),
            str(t.get("version", "")),
            str(t.get("author", "")),
            str(t.get("description", "")),
        ]
        for t in results
    ]
    print(_table(headers, rows))
    print(f"\n{_color(str(len(results)), _C.CYAN)} result(s).")
    print("Install one with: sandcastle template install <download_url> --sha256 <sha256>")
    print("(use --json to see download_url and sha256)")


def _cmd_template(args: argparse.Namespace) -> None:
    """Route template sub-commands."""
    action = getattr(args, "template_action", None)
    template_dispatch: dict[str, Any] = {
        "verify": _cmd_template_verify,
        "install": _cmd_template_install,
        "search": _cmd_template_search,
    }
    handler = template_dispatch.get(action)
    if handler:
        handler(args)
    else:
        print("Usage: sandcastle template {verify,install,search}", file=sys.stderr)
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
        print(_format_cli_error(exc), file=sys.stderr)
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
        print(_format_cli_error(exc), file=sys.stderr)
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
        print(_format_cli_error(exc), file=sys.stderr)
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
        print(_format_cli_error(exc), file=sys.stderr)
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
    """Rotate an API key using the dedicated rotate endpoint.

    Creates a new key atomically and starts the grace period for the old one.
    """
    body = _api_post(args, f"/api/api-keys/{args.key_id}/rotate")
    data = body.get("data", {})

    if getattr(args, "json", False):
        _json_out(body)
        return

    new_id = data.get("new_key_id") or data.get("id", "?")
    new_key = data.get("key", "")
    print(f"Key rotated: {args.key_id} -> {new_id}")
    if data.get("grace_expires_at"):
        print(f"  Old key grace period expires: {data['grace_expires_at']}")
    print()
    print(_color("  New key (shown ONCE - save it now!):", _C.YELLOW))
    print(f"  {_color(new_key, _C.GREEN)}")
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
            if sev in ("critical", "high"):
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
# providers - List configured AI advisor providers
# ---------------------------------------------------------------------------


def _cmd_providers(args: argparse.Namespace) -> None:  # noqa: ARG001
    """List configured AI providers and their status."""
    from sandcastle.engine.generator import _PROVIDER_CONFIGS

    _load_dot_env()

    provider_env = os.environ.get("SANDCASTLE_ADVISOR_PROVIDER", "anthropic").lower()
    if provider_env not in _PROVIDER_CONFIGS:
        provider_env = "anthropic"

    model_override = os.environ.get("SANDCASTLE_ADVISOR_MODEL", "")
    residency = os.environ.get("DATA_RESIDENCY", "")

    print(_color("AI Providers:", _C.BOLD))
    print()

    region_flag = {"us": "\U0001f1fa\U0001f1f8", "eu": "\U0001f1ea\U0001f1fa", "local": "\U0001f3e0"}

    for provider_id, cfg in _PROVIDER_CONFIGS.items():
        key_env = cfg.get("api_key_env", "")
        region = cfg.get("region", "us")
        model = model_override if (provider_id == provider_env and model_override) else cfg.get("model", "")
        flag = region_flag.get(region, "")

        # Determine configured status
        if not key_env:
            # Ollama - check if running (best-effort socket probe)
            import socket
            try:
                s = socket.create_connection(("localhost", 11434), timeout=1)
                s.close()
                configured = True
                status_label = "Running"
            except OSError:
                configured = False
                status_label = "Not running"
        else:
            api_key = os.environ.get(key_env, "")
            configured = bool(api_key)
            status_label = "Ready" if configured else "Not configured"

        is_active = provider_id == provider_env
        tick = _color("✓", _C.GREEN) if configured else _color("✗", _C.RED)
        active_marker = _color("  Active", _C.CYAN) if is_active else ""

        name_part = f"{provider_id:<12}"
        region_part = f"{region.upper()} {flag}"
        status_part = f"{status_label:<18}"
        model_part = model

        line = f"  {tick} {name_part}  {region_part:<8}  {status_part}  {model_part}{active_marker}"
        print(line)

    print()
    if residency:
        print(f"Data Residency: {_color(residency, _C.CYAN)}")
    else:
        print("Data Residency: disabled")


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
# Update / rollback helpers
# ---------------------------------------------------------------------------

_SANDCASTLE_DIR = os.path.join(os.path.expanduser("~"), ".sandcastle")
_PYPI_PACKAGE = "sandcastle-ai"
_PYPI_JSON_URL = f"https://pypi.org/pypi/{_PYPI_PACKAGE}/json"


def _ensure_sandcastle_dir() -> str:
    """Ensure ~/.sandcastle exists and return its path."""
    os.makedirs(_SANDCASTLE_DIR, exist_ok=True)
    return _SANDCASTLE_DIR


def _fetch_pypi_versions(timeout: int = 10) -> dict:
    """Fetch package metadata from PyPI. Returns the full JSON response."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        _PYPI_JSON_URL,
        headers={"Accept": "application/json", "User-Agent": "sandcastle-cli"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to fetch PyPI metadata: {exc}") from exc


def _is_prerelease(version_str: str) -> bool:
    """Return True if version looks like a pre-release (contains a/b/rc/dev)."""
    import re
    return bool(re.search(r"(a|b|rc|dev)\d*$", version_str))


def _latest_version_for_channel(pypi_data: dict, channel: str) -> str:
    """Pick the latest version from PyPI data respecting the channel.

    - "stable": skip pre-release versions (alpha/beta/rc/dev)
    - "beta": include everything
    """
    from packaging.version import InvalidVersion, Version

    all_versions: list[str] = list(pypi_data.get("releases", {}).keys())
    parsed: list[tuple[Version, str]] = []
    for v in all_versions:
        try:
            pv = Version(v)
        except InvalidVersion:
            continue
        # Skip yanked releases
        release_files = pypi_data["releases"].get(v, [])
        if release_files and all(f.get("yanked", False) for f in release_files):
            continue
        # Skip empty releases (no files uploaded)
        if not release_files:
            continue
        if channel == "stable" and pv.is_prerelease:
            continue
        parsed.append((pv, v))

    if not parsed:
        raise RuntimeError("No suitable version found on PyPI for channel: " + channel)

    parsed.sort(key=lambda x: x[0])
    return parsed[-1][1]


def _save_previous_version(version: str) -> None:
    """Persist the current version before upgrading so rollback works."""
    import datetime
    d = _ensure_sandcastle_dir()
    path = os.path.join(d, "previous_version.json")
    data = {
        "version": version,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _load_previous_version() -> dict | None:
    """Load previous version info, or None if not available."""
    path = os.path.join(_SANDCASTLE_DIR, "previous_version.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _backup_file(src: str, suffix: str) -> bool:
    """Copy src to src + suffix. Returns True if backup was created."""
    import shutil
    if os.path.isfile(src):
        dest = src + suffix
        shutil.copy2(src, dest)
        return True
    return False


def _restore_file(src_with_suffix: str, dest: str) -> bool:
    """Restore a backup file. Returns True if restored."""
    import shutil
    if os.path.isfile(src_with_suffix):
        shutil.copy2(src_with_suffix, dest)
        return True
    return False


def _pip_install(package_spec: str) -> bool:
    """Run pip install and return True on success."""
    import subprocess
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", package_spec]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _verify_installed_version() -> str | None:
    """Import sandcastle in a subprocess and return its __version__."""
    import subprocess
    cmd = [
        sys.executable, "-c",
        "from sandcastle import __version__; print(__version__)",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _find_db_path() -> str | None:
    """Return the path to the SQLite database if it exists."""
    candidates = [
        os.path.join(_SANDCASTLE_DIR, "data", "sandcastle.db"),
        os.path.join(".", "sandcastle.db"),
        os.path.join(".", "data", "sandcastle.db"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _find_env_path() -> str | None:
    """Return the path to .env if it exists."""
    if os.path.isfile(".env"):
        return ".env"
    return None


def _cmd_update(args: argparse.Namespace) -> None:
    """Check for updates and install the latest (or specified) version."""
    from sandcastle import __version__

    current = __version__
    channel = getattr(args, "channel", None) or os.getenv("UPDATE_CHANNEL", "stable")
    target_version = getattr(args, "target_version", None)
    dry_run = getattr(args, "dry_run", False)
    auto_yes = getattr(args, "yes", False)

    print(f"\n  {_color('Checking for updates...', _C.CYAN)}\n")

    # Fetch PyPI metadata
    try:
        pypi_data = _fetch_pypi_versions()
    except RuntimeError as exc:
        print(f"  {_color(str(exc), _C.RED)}")
        sys.exit(1)

    # Determine target version
    if target_version:
        # Validate that the requested version exists
        if target_version not in pypi_data.get("releases", {}):
            print(f"  {_color('Version ' + target_version + ' not found on PyPI.', _C.RED)}")
            sys.exit(1)
        latest = target_version
    else:
        try:
            latest = _latest_version_for_channel(pypi_data, channel)
        except RuntimeError as exc:
            print(f"  {_color(str(exc), _C.RED)}")
            sys.exit(1)

    # Display version info
    print(f"  Current:  {_color('v' + current, _C.YELLOW)}")
    print(f"  Latest:   {_color('v' + latest, _C.GREEN)}")
    print(f"  Channel:  {_color(channel, _C.CYAN)}")

    # Check if already up to date
    from packaging.version import Version
    try:
        if Version(latest) <= Version(current) and not target_version:
            print(f"\n  {_color('Already up to date!', _C.GREEN)}")
            return
    except Exception:
        pass  # Fall through if version comparison fails

    # Show changelog (from PyPI description if available)
    release_info = pypi_data.get("info", {})
    summary = release_info.get("summary", "")
    if summary:
        label = "What's new:"
        print(f"\n  {_color(label, _C.BOLD)}")
        print(f"    - {summary}")
    print()

    # Dry run stops here
    if dry_run:
        print(f"  {_color('[dry-run] No changes made.', _C.YELLOW)}")
        return

    # Confirmation prompt
    if not auto_yes:
        if not sys.stdin.isatty():
            print(
                f"  {_color('Non-interactive mode. Use --yes to skip confirmation.', _C.RED)}",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            answer = input("  Proceed with update? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return
        if answer not in ("", "y", "yes"):
            print("  Aborted.")
            return

    # Backup phase
    print(f"  {_color('Backing up current installation...', _C.CYAN)}")
    _save_previous_version(current)

    db_path = _find_db_path()
    if db_path:
        _backup_file(db_path, ".pre-update")
        print(f"    Database: backed up to {os.path.basename(db_path)}.pre-update")

    env_path = _find_env_path()
    if env_path:
        _backup_file(env_path, ".pre-update")
        print("    Config: backed up to .env.pre-update")

    print()

    # Install
    package_spec = f"{_PYPI_PACKAGE}=={latest}"
    print(f"  Installing {_color(package_spec, _C.CYAN)}...")
    if not _pip_install(package_spec):
        print(f"  {_color('Installation failed!', _C.RED)}")
        print(f"  Run manually: pip install {package_spec}")
        sys.exit(1)

    print(f"  {_color('Successfully installed ' + package_spec, _C.GREEN)}")

    # Verify
    print(f"\n  {_color('Verifying...', _C.CYAN)}")
    installed = _verify_installed_version()
    if installed:
        print(f"    Version: {installed} {_color('OK', _C.GREEN)}")
    else:
        print(f"    {_color('Could not verify installed version.', _C.YELLOW)}")

    # Restart instructions
    print(f"\n  {_color('Update complete!', _C.GREEN)} Restart Sandcastle to apply:")
    print("    sandcastle serve\n")


def _cmd_rollback(args: argparse.Namespace) -> None:
    """Roll back to the previously installed version."""
    auto_yes = getattr(args, "yes", False)

    prev = _load_previous_version()
    if not prev:
        print(
            f"\n  {_color('No previous version to roll back to.', _C.RED)}\n"
            f"  (Run 'sandcastle update' first to create a rollback point.)\n"
        )
        sys.exit(1)

    prev_ver = prev["version"]
    prev_ts = prev.get("timestamp", "unknown")

    # Format relative time
    age_str = ""
    try:
        import datetime
        ts = datetime.datetime.fromisoformat(prev_ts)
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = now - ts
        hours = int(delta.total_seconds() // 3600)
        if hours < 1:
            minutes = int(delta.total_seconds() // 60)
            age_str = f" (installed {minutes}m ago)"
        elif hours < 24:
            age_str = f" (installed {hours}h ago)"
        else:
            days = hours // 24
            age_str = f" (installed {days}d ago)"
    except Exception:
        pass

    print(f"\n  Previous version: {_color('v' + prev_ver, _C.YELLOW)}{age_str}")

    # Confirmation
    if not auto_yes:
        if not sys.stdin.isatty():
            print(
                f"  {_color('Non-interactive mode. Use --yes to skip confirmation.', _C.RED)}",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            answer = input(f"\n  Roll back to v{prev_ver}? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return
        if answer not in ("", "y", "yes"):
            print("  Aborted.")
            return

    # Install previous version
    package_spec = f"{_PYPI_PACKAGE}=={prev_ver}"
    print(f"\n  Installing {_color(package_spec, _C.CYAN)}...")
    if not _pip_install(package_spec):
        print(f"  {_color('Installation failed!', _C.RED)}")
        print(f"  Run manually: pip install {package_spec}")
        sys.exit(1)

    print(f"  {_color('Successfully installed ' + package_spec, _C.GREEN)}")

    # Restore DB backup if available
    db_path = _find_db_path()
    if db_path:
        backup_path = db_path + ".pre-update"
        if _restore_file(backup_path, db_path):
            print(f"  Restoring database backup... {_color('OK', _C.GREEN)}")

    # Verify
    installed = _verify_installed_version()
    if installed:
        print(f"  Version: {installed} {_color('OK', _C.GREEN)}")

    print(f"\n  {_color('Rollback complete!', _C.GREEN)} Restart to apply:")
    print("    sandcastle serve\n")


def _check_for_updates_on_startup() -> None:
    """Non-blocking update check for 'sandcastle serve' startup.

    Checks PyPI at most once every 24 hours. Never blocks startup for
    more than 3 seconds.
    """
    import datetime

    state_dir = _ensure_sandcastle_dir()
    check_file = os.path.join(state_dir, "update_check.json")

    # Read last check timestamp
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(check_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        last_check = datetime.datetime.fromisoformat(state.get("last_check", ""))
        if (now - last_check).total_seconds() < 86400:  # 24 hours
            return
    except (OSError, json.JSONDecodeError, ValueError, KeyError):
        pass  # First run or corrupted file - proceed with check

    # Fetch with a short timeout so we never block startup
    try:
        pypi_data = _fetch_pypi_versions(timeout=3)
    except RuntimeError:
        return  # Network error - silently skip

    # Determine channel from env (config module may not be loaded yet)
    channel = os.getenv("UPDATE_CHANNEL", "stable")

    try:
        latest = _latest_version_for_channel(pypi_data, channel)
    except RuntimeError:
        return

    from sandcastle import __version__
    current = __version__

    # Persist check timestamp
    try:
        with open(check_file, "w", encoding="utf-8") as f:
            json.dump({
                "last_check": now.isoformat(),
                "latest_version": latest,
                "current_version": current,
            }, f, indent=2)
    except OSError:
        pass

    # Compare versions
    try:
        from packaging.version import Version
        if Version(latest) > Version(current):
            msg = (
                f"Sandcastle v{latest} available (current: v{current}). "
                f"Run 'sandcastle update' to upgrade."
            )
            print(f"\n  {_color('Warning: ' + msg, _C.YELLOW)}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Self-describing workflow commands (describe, lint, owners)
# ---------------------------------------------------------------------------


def _resolve_workflow_file(name: str) -> str:
    """Resolve a workflow name or path to an absolute YAML file path.

    Accepts a direct file path (*.yaml / *.yml) or a bare workflow name
    which is looked up in the configured workflows directory and the
    built-in templates directory.
    """
    from pathlib import Path

    p = Path(name)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return str(p.resolve())

    # Try configured workflows directory
    try:
        from sandcastle.config import settings
        wf_dir = Path(settings.workflows_dir).resolve()
        for ext in (".yaml", ".yml"):
            candidate = wf_dir / f"{name}{ext}"
            if candidate.exists():
                return str(candidate)
    except Exception:
        pass

    # Try current working directory
    for ext in (".yaml", ".yml"):
        candidate = Path(name + ext)
        if candidate.exists():
            return str(candidate.resolve())

    # Try built-in templates
    templates_dir = Path(__file__).resolve().parent / "templates"
    for ext in (".yaml", ".yml"):
        candidate = templates_dir / f"{name}{ext}"
        if candidate.exists():
            return str(candidate)

    print(f"Error: workflow '{name}' not found.", file=sys.stderr)
    sys.exit(1)


def _cmd_describe(args: argparse.Namespace) -> None:
    """Print workflow summary with responsibilities and source hints."""
    from sandcastle.engine.dag import parse

    path = _resolve_workflow_file(args.workflow)
    wf = parse(path)
    print(wf.summary())


def _cmd_lint(args: argparse.Namespace) -> None:
    """Check workflow metadata completeness."""
    from sandcastle.engine.dag import parse

    path = _resolve_workflow_file(args.workflow)
    wf = parse(path)
    h = wf.health()

    print(f"Linting {wf.name}...")
    print()

    if h["issues"]:
        print("Warnings:")
        for issue in h["issues"]:
            print(f"  - {issue}")
        print()

    total = h["total_fields"]
    filled = h["filled_fields"]
    pct = int(filled / total * 100) if total > 0 else 0
    warnings = len(h["issues"])
    print(f"{warnings} warnings, 0 errors")
    print(f"Metadata completeness: {pct}% ({filled}/{total} fields filled)")


def _cmd_owners(args: argparse.Namespace) -> None:
    """Show who owns which steps in a workflow."""
    from sandcastle.engine.dag import parse

    path = _resolve_workflow_file(args.workflow)
    wf = parse(path)

    print(f"Owners for {wf.name}:")
    print()

    # Group steps by owner
    owner_map: dict[str, list[str]] = {}
    for step in wf.steps:
        key = step.owner if step.owner else "unassigned"
        owner_map.setdefault(key, []).append(step.id)

    for owner in sorted(owner_map, key=lambda o: (o == "unassigned", o)):
        steps_str = ", ".join(owner_map[owner])
        print(f"  {owner}: {steps_str}")


def _cmd_audit_verify(args: argparse.Namespace) -> None:
    """Verify a run cassette's tamper-evident hash chain and keyed signature.

    The target is either a path to a ``.cassette.json`` file or a run ID,
    which is resolved to the auto-recorded cassette under the data dir
    (black box compliance mode). Exits 1 on FAIL.
    """
    from pathlib import Path

    from sandcastle.engine.cassette import default_cassette_path, verify_cassette

    path = Path(args.target)
    if not path.exists():
        path = default_cassette_path(args.target)
    if not path.exists():
        print(
            _color(f"No cassette found for '{args.target}' (looked at {path})", _C.RED),
            file=sys.stderr,
        )
        sys.exit(1)

    v = verify_cassette(path, key=args.key)

    print(f"Cassette:   {path}")
    print(f"Records:    {v.chain_length}")
    print(f"Chain head: {v.chain_head}")
    if v.signature_ok is None:
        print("Signature:  not checked (unsigned cassette or no audit key configured)")
    elif v.signature_ok:
        print(f"Signature:  {_color('valid', _C.GREEN)}")
    else:
        print(f"Signature:  {_color('INVALID', _C.RED)}")
    print()

    if v.valid:
        print(_color("PASS", _C.GREEN) + " - hash chain intact, every record verifies")
        return
    print(_color("FAIL", _C.RED) + " - the audit trail does not verify")
    if v.first_broken_index is not None:
        print(f"  First broken record: chain index {v.first_broken_index}")
    if v.reason:
        print(f"  Reason: {v.reason}")
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

    # --- node (Sandcastle Mesh) ---
    p_node = subparsers.add_parser("node", help="Sandcastle Mesh node commands")
    node_sub = p_node.add_subparsers(dest="node_action")
    p_node_join = node_sub.add_parser(
        "join", help="Join a Sandcastle Mesh as a worker node"
    )
    p_node_join.add_argument(
        "coordinator", help="Coordinator base URL, e.g. http://spark.local:8080"
    )
    p_node_join.add_argument(
        "--token", default="", help="Shared mesh token (or MESH_TOKEN env var)"
    )
    p_node_join.add_argument(
        "--capabilities", default="",
        help="Comma-separated capability override (default: auto-detect)",
    )
    p_node_join.add_argument("--name", default="", help="Node name (default: hostname)")
    p_node_join.add_argument(
        "--base-url", default="",
        help="URL the coordinator uses to reach this node (default: http://<hostname>:<port>)",
    )
    p_node_join.add_argument("--host", default="0.0.0.0", help="Bind host for the node API")
    p_node_join.add_argument(
        "--port", type=int, default=8080, help="Bind port for the node API (default: 8080)"
    )
    p_node_join.add_argument(
        "--heartbeat", type=int, default=0,
        help="Heartbeat interval in seconds (default: MESH_HEARTBEAT_SECONDS)",
    )
    p_node_join.add_argument(
        "--no-serve", action="store_true", default=False,
        help="Do not start the node API server (one is already running)",
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
        "--stream",
        "-s",
        action="store_true",
        help="Stream step-by-step progress after queuing the run",
    )
    p_run.add_argument(
        "--max-cost", type=float, default=None, metavar="USD", help="Maximum cost limit in USD"
    )
    p_run.add_argument(
        "--local",
        action="store_true",
        help="Run the workflow in-process without a server (no `sandcastle serve` needed)",
    )
    p_run.add_argument(
        "--record",
        metavar="FILE",
        help="Record every step output to a deterministic cassette file (implies --local)",
    )
    p_run.add_argument(
        "--replay",
        metavar="FILE",
        help="Replay a recorded cassette offline at zero cost, no providers called (implies --local)",
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

    # --- share ---
    p_share = subparsers.add_parser(
        "share", help="Mint a public, scrubbed permalink for a run (or --revoke it)"
    )
    p_share.add_argument("run_id", help="Run ID to share")
    p_share.add_argument(
        "--revoke", action="store_true", help="Revoke the run's public share link"
    )
    _add_connection_args(p_share)

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

    # --- publish-mcp ---
    p_publish_mcp = subparsers.add_parser(
        "publish-mcp",
        help="List MCP-publishable workflows or emit a client config snippet",
    )
    p_publish_mcp.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="Workflow name (omit to list all publishable workflows as JSON)",
    )

    # --- publish-skills ---
    p_publish_skills = subparsers.add_parser(
        "publish-skills",
        help="Publish workflows as Anthropic Skills (dry-run by default)",
    )
    p_publish_skills.add_argument(
        "--upload",
        action="store_true",
        default=False,
        help="Actually POST each skill via AnthropicSkillsClient (default: dry-run)",
    )
    p_publish_skills.add_argument(
        "--dir",
        default=None,
        help="Override the workflows directory (defaults to settings.workflows_dir)",
    )

    # --- doctor ---
    p_doctor = subparsers.add_parser("doctor", help="Run local diagnostics")
    p_doctor.add_argument("workflow", nargs="?", default=None, help="Workflow YAML file to diagnose (optional)")

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

    # --- timemachine ---
    p_tm = subparsers.add_parser(
        "timemachine",
        help="Replay recorded workload against a different model (cost/quality/latency delta)",
    )
    p_tm.add_argument("--model", "-m", required=True, help="Target model (e.g. nim/llama-3.1-70b)")
    p_tm.add_argument("--workflow", "-w", default=None, help="Only replay this workflow")
    p_tm.add_argument("--since", default="30d", help="Window like 30d / 12h or ISO date (default: 30d)")
    p_tm.add_argument(
        "--max-cassettes", type=int, default=20, help="Max recorded runs to replay (default: 20)"
    )
    p_tm.add_argument(
        "--live", action="store_true",
        help="Re-execute steps with real API calls and judge quality (default: dry-run estimate)",
    )
    p_tm.add_argument(
        "--budget", type=float, default=None,
        help="Hard cost cap in USD for --live (required with --live)",
    )
    p_tm.add_argument("--judge", default=None, help="Judge model for quality scoring (default: haiku)")
    p_tm.add_argument("--json", action="store_true", help="Print the full report as JSON")

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

    # --- audit (black box flight recorder) ---
    p_audit = subparsers.add_parser("audit", help="Signed audit trail (black box)")
    audit_sub = p_audit.add_subparsers(dest="audit_action")

    p_audit_verify = audit_sub.add_parser(
        "verify", help="Verify a run's signed cassette: hash chain + signature"
    )
    p_audit_verify.add_argument(
        "target", help="Run ID (resolved under the data dir) or path to a .cassette.json file"
    )
    p_audit_verify.add_argument(
        "--key",
        default=None,
        help="Audit key to verify the signature with (default: AUDIT_KEY from env/.env)",
    )

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

    # --- providers ---
    subparsers.add_parser("providers", help="List configured AI advisor providers")

    # --- update ---
    p_update = subparsers.add_parser("update", help="Check for and install updates")
    p_update.add_argument(
        "--yes", "-y", action="store_true", default=False, help="Skip confirmation prompt"
    )
    p_update.add_argument(
        "--version", dest="target_version", default=None, metavar="X.Y.Z",
        help="Install a specific version instead of latest",
    )
    p_update.add_argument(
        "--channel", default=None, choices=["stable", "beta"],
        help="Override update channel (default: from config or 'stable')",
    )
    p_update.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Show what would happen without installing",
    )

    # --- rollback ---
    p_rollback = subparsers.add_parser("rollback", help="Roll back to previous version")
    p_rollback.add_argument(
        "--yes", "-y", action="store_true", default=False, help="Skip confirmation prompt"
    )

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

    # --- pack ---
    p_pack = subparsers.add_parser(
        "pack", help="Pack a workflow + recorded cassettes into a .sctpl bundle"
    )
    p_pack.add_argument("workflow", help="Workflow .yaml path or built-in template name")
    p_pack.add_argument(
        "--cassette", "-c", action="append", required=True, dest="cassettes",
        metavar="FILE", help="Recorded cassette file - proof-of-execution (repeatable)",
    )
    p_pack.add_argument(
        "--output", "-o", default=None, help="Output path (default: <name>-<version>.sctpl)"
    )
    p_pack.add_argument("--name", default=None, help="Bundle name (default: workflow name)")
    p_pack.add_argument(
        "--bundle-version", default="1.0.0", help="Bundle version (default: 1.0.0)"
    )
    p_pack.add_argument(
        "--description", default=None, help="Description (default: workflow description)"
    )
    p_pack.add_argument("--author", default="", help="Author handle (e.g. gizmax)")
    p_pack.add_argument(
        "--license", default="MIT", dest="license_id", help="SPDX license id (default: MIT)"
    )
    p_pack.add_argument(
        "--created-at", default=None,
        help="ISO 8601 timestamp (default: the workflow's last git commit date)",
    )
    p_pack.add_argument(
        "--input", "-i", action="append",
        help="Example input key=value - must match the recording (repeatable)",
    )
    p_pack.add_argument(
        "--input-file", "-f", help="JSON file with the example inputs used when recording"
    )
    p_pack.add_argument(
        "--no-verify", action="store_true", help="Skip the post-pack verification replay"
    )

    # --- template (verified bundles) ---
    p_template = subparsers.add_parser(
        "template", help="Verified template bundles (.sctpl): verify, install, search"
    )
    template_sub = p_template.add_subparsers(dest="template_action")

    t_verify = template_sub.add_parser(
        "verify", help="Replay a bundle's cassettes against its workflow - offline, $0"
    )
    t_verify.add_argument("bundle", help="Path to a .sctpl bundle")
    t_verify.add_argument("--json", action="store_true", help="JSON output")

    t_install = template_sub.add_parser(
        "install", help="Verify a bundle, then install its workflow"
    )
    t_install.add_argument("source", help="Path or https:// URL of a .sctpl bundle")
    t_install.add_argument(
        "--dir", "-d", default=None,
        help="Target directory (default: the community templates dir the wizard reads)",
    )
    t_install.add_argument(
        "--force", action="store_true",
        help="Install even if verification fails; overwrite an existing template",
    )
    t_install.add_argument(
        "--sha256", default=None, help="Expected SHA-256 of the bundle file (from the index)"
    )

    t_search = template_sub.add_parser("search", help="Search the verified template index")
    t_search.add_argument("query", help="Search query")
    t_search.add_argument("--json", action="store_true", help="JSON output")

    # --- describe ---
    p_describe = subparsers.add_parser(
        "describe", help="Print workflow summary with responsibilities"
    )
    p_describe.add_argument("workflow", help="Workflow name or path to .yaml file")

    # --- lint ---
    p_lint = subparsers.add_parser(
        "lint", help="Check workflow metadata completeness"
    )
    p_lint.add_argument("workflow", help="Workflow name or path to .yaml file")

    # --- owners ---
    p_owners = subparsers.add_parser(
        "owners", help="Show step ownership for a workflow"
    )
    p_owners.add_argument("workflow", help="Workflow name or path to .yaml file")

    return parser


def _cmd_timemachine(args: argparse.Namespace) -> None:
    """Replay recorded workload against a different model and print the delta report."""
    import asyncio

    from sandcastle.engine import timemachine as tm
    from sandcastle.engine.providers import is_known_model

    model = args.model
    if not is_known_model(model):
        print(f"Error: unknown model '{model}'.", file=sys.stderr)
        sys.exit(1)
    if args.live and args.budget is None:
        print(
            "Error: --live makes real API calls - an explicit --budget cap is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    since = None
    if args.since:
        try:
            since = tm.parse_since(args.since)
        except ValueError as exc:
            print(f"Error: invalid --since value: {exc}", file=sys.stderr)
            sys.exit(1)

    mode = "live replay" if args.live else "dry run (pricing only, no API calls)"
    print()
    print(_color("  Model Time Machine", _C.BOLD))
    print(_color("  Target model: ", _C.BOLD) + model)
    print(_color("  Mode:         ", _C.BOLD) + mode)
    print()

    async def _go() -> dict:
        from sandcastle.models.db import init_db

        await init_db()
        return await tm.run_time_machine(
            target_model=model,
            workflow=args.workflow,
            since=since,
            max_cassettes=args.max_cassettes,
            live=args.live,
            budget_usd=args.budget,
            judge_model=args.judge,
        )

    try:
        report = asyncio.run(_go())
    except tm.TimeMachineError as exc:
        print(_color(f"  Refused: {exc}", _C.RED), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(_color(f"  Time Machine failed: {exc}", _C.RED), file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    sel = report["selection"]
    if sel["runs"] == 0:
        print(_color("  No recorded runs matched the selection.", _C.YELLOW))
        print("  Run some workflows first, then come back - the Time Machine replays")
        print("  your real recorded workload, not synthetic benchmarks.")
        return

    cost = report["cost"]
    print(
        f"  Selection: {sel['runs']} runs / {sel['steps']} LLM steps "
        f"across {len(sel['workflows'])} workflow(s), "
        f"original spend ${cost['original_usd']:.4f}"
    )
    print()

    # Per-workflow table
    header = (
        f"  {'WORKFLOW':<28} {'COST':>10} {'->':^4} {'NEW':>10} "
        f"{'QUALITY':>9} {'LATENCY':>9}"
    )
    print(_color(header, _C.BOLD))
    for wf in report["per_workflow"]:
        q = wf["quality_delta_pct"]
        lat = wf["latency_delta_pct"]
        q_str = f"{q:+.1f}%" if q is not None else "-"
        l_str = f"{lat:+.1f}%" if lat is not None else "-"
        print(
            f"  {wf['workflow'][:28]:<28} ${wf['original_cost_usd']:>9.4f} {'->':^4} "
            f"${wf['new_cost_usd']:>9.4f} {q_str:>9} {l_str:>9}"
        )
    print()

    delta_pct = cost["delta_pct"]
    delta_str = f" ({delta_pct:+.1f}%)" if delta_pct is not None else ""
    color = _C.GREEN if cost["delta_usd"] <= 0 else _C.RED
    print(_color(f"  Cost delta: ${cost['delta_usd']:+.4f}{delta_str}", color))
    if report.get("live"):
        live = report["live"]
        print(
            f"  Live replay: {live['steps_replayed']} steps replayed, "
            f"{live['steps_failed']} failed, measured ${live['measured_cost_usd']:.4f} "
            f"of ${live['budget_usd']:.2f} budget"
            + (" (truncated at budget)" if live["truncated"] else "")
        )
    if report.get("quality"):
        q = report["quality"]
        if q["old_avg"] is not None:
            print(f"  Quality (judge {report['judge_model']}): "
                  f"{q['old_avg']:.1f} -> {q['new_avg']:.1f} ({q['delta_pct']:+.1f}%)")
    print()
    print(_color(f"  {report['verdict']}", _C.BOLD))


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

    # Load .env file for commands that read os.getenv() directly.
    # Commands like 'init' and 'doctor' don't need this but it's harmless.
    _load_dot_env()

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

    # --- audit ---
    if args.command == "audit":
        if getattr(args, "audit_action", None) == "verify":
            _cmd_audit_verify(args)
        else:
            print("Usage: sandcastle audit verify <run-id-or-cassette-path>", file=sys.stderr)
            sys.exit(1)
        return

    # --- node (Sandcastle Mesh) ---
    if args.command == "node":
        _cmd_node(args)
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
        "share": _cmd_share,
        "logs": _cmd_logs,
        "ls": _cmd_ls,
        "worker": _cmd_worker,
        "health": _cmd_health,
        "mcp": _cmd_mcp,
        "publish-mcp": _cmd_publish_mcp,
        "publish-skills": _cmd_publish_skills,
        "doctor": _cmd_doctor,
        "generate": _cmd_generate,
        "eval": _cmd_eval,
        "timemachine": _cmd_timemachine,
        "templates": _cmd_templates,
        "replay": _cmd_replay,
        "fork": _cmd_fork,
        "approve": _cmd_approve,
        "reject": _cmd_reject,
        "hub": _cmd_hub,
        "pack": _cmd_pack,
        "template": _cmd_template,
        "keys": _cmd_keys,
        "dlq": _cmd_dlq,
        "violations": _cmd_violations,
        "tools": _cmd_tools,
        "autopilot": _cmd_autopilot,
        "providers": _cmd_providers,
        "update": _cmd_update,
        "rollback": _cmd_rollback,
        "describe": _cmd_describe,
        "lint": _cmd_lint,
        "owners": _cmd_owners,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
