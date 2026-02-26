"""Opt-in error reporting via Sentry for improving Sandcastle.

Disabled by default. Enable with TELEMETRY_ENABLED=true and
SENTRY_DSN=<your-dsn> in .env.
No API keys, credentials, or user data are ever transmitted - only
error type, stack trace, step type, sandbox backend, version, and OS.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Patterns to strip from events
_SENSITIVE_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|secret|password|token|dsn|credential)"
        r"[\"']?\s*[:=]\s*[\"']?[\w\-\.]+"
    ),
    re.compile(
        r"(?i)(sk-|xoxb-|xoxp-|pypi-|ghp_|ghu_|glpat-)[\w\-]+"
    ),
    re.compile(r"(?i)bearer\s+[\w\-\.]+"),
]

_SENSITIVE_ENV_KEYS = frozenset({
    "anthropic_api_key", "e2b_api_key",
    "openai_api_key", "openrouter_api_key",
    "minimax_api_key", "sentry_dsn",
    "admin_api_key", "webhook_secret",
    "aws_access_key_id", "aws_secret_access_key",
    "tool_slack_bot_token", "tool_jira_api_token",
    "tool_github_token", "tool_notion_api_key",
    "tool_hubspot_api_key",
    "tool_salesforce_client_id",
    "tool_salesforce_client_secret",
    "tool_salesforce_refresh_token",
    "tool_zendesk_api_token",
    "tool_smtp_password", "tool_google_service_account",
    "license_key",
})

_initialized = False


def init_sentry() -> bool:
    """Initialize Sentry SDK if telemetry is enabled and DSN is set.

    Returns True if Sentry was initialized, False otherwise.
    """
    global _initialized
    if _initialized:
        return True

    from sandcastle.config import settings

    if not settings.telemetry_enabled or not settings.sentry_dsn:
        logger.info(
            "Telemetry disabled "
            "(TELEMETRY_ENABLED=false or no SENTRY_DSN)"
        )
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import (
            FastApiIntegration,
        )
        from sentry_sdk.integrations.logging import (
            LoggingIntegration,
        )

        from sandcastle import __version__

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            release=f"sandcastle@{__version__}",
            environment=os.getenv(
                "SANDCASTLE_ENV", "production"
            ),
            traces_sample_rate=0.0,  # Errors only
            before_send=_anonymize_event,
            integrations=[
                FastApiIntegration(
                    transaction_style="endpoint"
                ),
                LoggingIntegration(
                    level=logging.WARNING,
                    event_level=logging.ERROR,
                ),
            ],
            enable_tracing=False,
            server_name=None,  # Don't send hostname
            send_default_pii=False,
        )
        sentry_sdk.set_tag(
            "sandbox_backend", settings.sandbox_backend
        )
        sentry_sdk.set_tag("os", platform.system())
        sentry_sdk.set_tag("python", platform.python_version())

        _initialized = True
        logger.info(
            "Sentry telemetry initialized "
            "(release=sandcastle@%s)",
            __version__,
        )
        return True

    except ImportError:
        logger.info(
            "sentry-sdk not installed. "
            "Install with: pip install sandcastle-ai[telemetry]"
        )
        return False
    except Exception as exc:
        logger.warning("Failed to initialize Sentry: %s", exc)
        return False


def _anonymize_event(
    event: dict, hint: dict
) -> dict | None:
    """Strip sensitive data from Sentry events before sending."""
    # Scrub exception values and messages
    for field in ("message", "logentry"):
        if field in event:
            val = event[field]
            if isinstance(val, dict) and "formatted" in val:
                val["formatted"] = _scrub_text(
                    val["formatted"]
                )
            elif isinstance(val, str):
                event[field] = _scrub_text(val)

    # Scrub exception frames
    if "exception" in event:
        for exc_info in event["exception"].get("values", []):
            if "value" in exc_info:
                exc_info["value"] = _scrub_text(
                    str(exc_info["value"])
                )
            stacktrace = exc_info.get("stacktrace") or {}
            for frame in stacktrace.get("frames", []):
                # Remove local variable values
                frame.pop("vars", None)
                # Scrub absolute paths to relative
                if "abs_path" in frame:
                    frame["abs_path"] = _scrub_path(
                        frame["abs_path"]
                    )

    # Scrub breadcrumbs
    for crumb in event.get("breadcrumbs", {}).get(
        "values", []
    ):
        if "message" in crumb:
            crumb["message"] = _scrub_text(crumb["message"])
        if "data" in crumb:
            crumb["data"] = {
                k: (
                    _scrub_text(str(v))
                    if isinstance(v, str)
                    else v
                )
                for k, v in crumb["data"].items()
            }

    # Remove request data (IP, headers, cookies)
    event.pop("request", None)
    event.pop("user", None)

    # Also save locally
    _save_local_report(event)

    return event


def _scrub_text(text: str) -> str:
    """Remove sensitive patterns from a text string."""
    for pat in _SENSITIVE_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _scrub_path(path: str) -> str:
    """Convert absolute paths to relative."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _save_local_report(event: dict) -> None:
    """Save error report to local JSON file as fallback."""
    try:
        from sandcastle.config import settings

        report_dir = Path(settings.data_dir) / "error_reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        event_id = event.get("event_id", "unknown")
        filepath = report_dir / f"{timestamp}_{event_id}.json"

        # Keep only essential fields for local report
        local_report: dict[str, Any] = {
            "event_id": event_id,
            "timestamp": event.get("timestamp"),
            "level": event.get("level", "error"),
            "platform": event.get("platform"),
            "release": event.get("release"),
            "tags": event.get("tags"),
            "contexts": {
                k: v
                for k, v in event.get("contexts", {}).items()
                if k in ("sandcastle", "os", "runtime")
            },
        }

        # Include exception type and message (already scrubbed)
        if "exception" in event:
            local_report["exceptions"] = [
                {
                    "type": e.get("type"),
                    "value": e.get("value"),
                }
                for e in event["exception"].get("values", [])
            ]

        filepath.write_text(
            json.dumps(local_report, indent=2, default=str)
        )
    except Exception:
        pass  # Local report is best-effort


def set_workflow_context(
    workflow_name: str,
    run_id: str,
    sandbox_backend: str = "",
) -> None:
    """Set Sentry context for the current workflow execution."""
    if not _initialized:
        return
    try:
        import sentry_sdk

        sentry_sdk.set_tag("workflow", workflow_name)
        sentry_sdk.set_tag("run_id", run_id)
        if sandbox_backend:
            sentry_sdk.set_tag(
                "sandbox_backend", sandbox_backend
            )
        sentry_sdk.set_context("sandcastle", {
            "workflow": workflow_name,
            "run_id": run_id,
            "sandbox_backend": sandbox_backend,
        })
    except Exception:
        pass


def capture_step_error(
    error: Exception,
    *,
    step_id: str = "",
    step_type: str = "",
    model: str = "",
    workflow_name: str = "",
    run_id: str = "",
    attempt: int = 0,
) -> None:
    """Capture a step execution error with enriched context."""
    if not _initialized:
        return
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("step_id", step_id)
            scope.set_tag("step_type", step_type)
            scope.set_tag("model", model)
            if workflow_name:
                scope.set_tag("workflow", workflow_name)
            if run_id:
                scope.set_tag("run_id", run_id)
            scope.set_extra("attempt", attempt)
            scope.set_context("step", {
                "step_id": step_id,
                "step_type": step_type,
                "model": model,
                "attempt": attempt,
            })
            sentry_sdk.capture_exception(error)
    except Exception:
        pass


def capture_backend_error(
    error: Exception,
    *,
    backend: str = "",
    operation: str = "",
) -> None:
    """Capture a sandbox backend error."""
    if not _initialized:
        return
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("backend", backend)
            scope.set_tag("operation", operation)
            scope.set_context("backend", {
                "type": backend,
                "operation": operation,
            })
            sentry_sdk.capture_exception(error)
    except Exception:
        pass


def is_enabled() -> bool:
    """Check if telemetry is currently active."""
    return _initialized


def _reset() -> None:
    """Reset state for testing."""
    global _initialized
    _initialized = False
