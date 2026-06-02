"""Shareable run permalinks: redact a finished run and render it as a public page.

A run owner mints a token; the public page at /api/r/{token} shows the run's workflow,
per-step provider/cost/duration (the cross-provider transparency that is the selling
point), and each step's output - with secrets and PII scrubbed first. Redaction is
redact-by-default: every string in every step output passes through the secret scrubber,
a supplementary high-confidence secret filter, and the PII router before it is rendered.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sandcastle.engine.dag import ReportConfig
from sandcastle.engine.generator import _scrub_secrets
from sandcastle.engine.privacy import PII_PATTERNS, PrivacyConfig, PrivacyRouter
from sandcastle.engine.report import render_report_html

# PII router covering every built-in entity (email/phone/ssn/cc/ip/iban/dob).
_PUB_PRIVACY = PrivacyRouter(
    PrivacyConfig(enabled=True, entities=list(PII_PATTERNS.keys()))
)

# Supplementary high-confidence secret shapes the generic scrubber may miss. These are
# deliberately conservative (clear token shapes) to avoid over-redacting real content.
_SECRET_PATTERNS = [
    re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|glpat-|xox[baprs]-|AKIA|ASIA|AIza|sk-|sk_live_|sk_test_|pk_live_|rk_live_)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}"),  # JWT
    re.compile(r"(?i)\b(?:bearer|authorization|api[_-]?key|access[_-]?token|secret|password|passwd|client[_-]?secret)\b\s*[:=]\s*[^\s\"']{6,}"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:@/]+:[^\s:@/]+@"),  # creds in a URL
]

_MAX_OUTPUT_CHARS = 3000


def _redact_str(text: str) -> str:
    out = _scrub_secrets(text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    out, _ = _PUB_PRIVACY.scrub(out)
    return out


def redact_public(value: Any) -> Any:
    """Recursively redact secrets + PII from any JSON-able value for public display."""
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, dict):
        return {k: redact_public(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_public(v) for v in value]
    return value


def _enum_str(value: Any) -> str:
    return getattr(value, "value", None) or getattr(value, "name", None) or str(value)


def _fmt_output(output: Any) -> str:
    if output is None or output == "" or output == {} or output == []:
        return "_(no output)_"
    redacted = redact_public(output)
    text = redacted if isinstance(redacted, str) else json.dumps(redacted, indent=2, default=str)
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"
    return f"```\n{text}\n```"


def run_to_markdown(run: Any) -> str:
    """Compose a public markdown page from a finished Run and its steps (redacted)."""
    steps = list(getattr(run, "steps", []) or [])
    steps.sort(key=lambda s: (getattr(s, "started_at", None) or 0, str(getattr(s, "step_id", ""))))

    status = _enum_str(run.status)
    cost = float(getattr(run, "total_cost_usd", 0.0) or 0.0)
    started = getattr(run, "started_at", "") or ""
    completed = getattr(run, "completed_at", "") or ""
    providers = sorted({str(s.model) for s in steps if getattr(s, "model", None)})

    lines: list[str] = []
    lines.append(f"# {run.workflow_name}")
    lines.append("")
    lines.append(
        f"**Status:** {status}  |  **Cost:** ${cost:.4f}  |  "
        f"**Started:** {started}  |  **Finished:** {completed}"
    )
    if providers:
        plural = "providers" if len(providers) > 1 else "provider"
        lines.append("")
        lines.append(f"Ran across {len(providers)} {plural}: " + ", ".join(f"`{p}`" for p in providers))
    if getattr(run, "error", None):
        lines.append("")
        lines.append(f"**Error:** {_redact_str(str(run.error))}")

    lines.append("")
    lines.append(f"## Steps ({len(steps)})")
    lines.append("")
    lines.append("| # | Step | Provider | Status | Cost | Duration |")
    lines.append("|---|------|----------|--------|------|----------|")
    for i, s in enumerate(steps, 1):
        lines.append(
            f"| {i} | {getattr(s, 'step_id', '')} | `{getattr(s, 'model', '') or '-'}` | "
            f"{_enum_str(getattr(s, 'status', ''))} | "
            f"${float(getattr(s, 'cost_usd', 0.0) or 0.0):.4f} | "
            f"{float(getattr(s, 'duration_seconds', 0.0) or 0.0):.2f}s |"
        )

    lines.append("")
    lines.append("## Step outputs")
    for s in steps:
        lines.append("")
        lines.append(f"### {getattr(s, 'step_id', '')}  -  model `{getattr(s, 'model', '') or '-'}`")
        if getattr(s, "error", None):
            lines.append(f"**Error:** {_redact_str(str(s.error))}")
        lines.append(_fmt_output(getattr(s, "output_data", None)))

    lines.append("")
    lines.append("## Run this yourself")
    lines.append("")
    lines.append("```")
    lines.append("pip install sandcastle-ai")
    lines.append(f"sandcastle run --local {run.workflow_name}")
    lines.append("```")
    lines.append("")
    lines.append("_Reproducible and provider-neutral. Shared from Sandcastle._")
    return "\n".join(lines)


def render_run_page(run: Any) -> str:
    """Render a finished run as a self-contained, scrubbed public HTML page."""
    markdown = run_to_markdown(run)
    config = ReportConfig(
        format="html",
        theme="professional",
        title=str(run.workflow_name),
        subtitle="Sandcastle run",
        author="Sandcastle",
        include_toc=False,
        include_page_numbers=False,
    )
    return render_report_html(markdown, config, {"title": str(run.workflow_name)})
