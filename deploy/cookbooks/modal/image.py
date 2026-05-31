"""Shared Modal Image definition for the Anthropic Managed Agents sandbox.

Mirrors the canonical layer order from:
  https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/
  self_hosted_sandboxes/modal

Layers are ordered cheap -> expensive so a typo in the last `pip_install`
doesn't invalidate the Node + apt cache.
"""

from __future__ import annotations

import modal

# Single canonical image consumed by both the webhook function and the
# per-session `modal.Sandbox.create(image=IMAGE, ...)` call.
IMAGE = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "curl",
        "ca-certificates",
        "git",
        "tini",
        "ripgrep",
        "jq",
        "build-essential",
    )
    # Node 22 (required by `ant` CLI tool dispatcher).
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        "apt-get install -y --no-install-recommends nodejs",
        "node --version && npm --version",
    )
    # `ant` CLI - the Managed Agents worker binary that `sandbox_runner.py`
    # invokes once per work item.
    .run_commands(
        "npm install -g @anthropic-ai/ant@latest",
        "ant --version",
    )
    # Python SDK + standardwebhooks for signature verification.
    .pip_install(
        "anthropic>=0.45",
        "anthropic[beta]",
        "standardwebhooks>=1.0",
        "modal>=0.66",
    )
    .env(
        {
            "PYTHONUNBUFFERED": "1",
            "ANT_HOME": "/opt/ant",
        }
    )
    # tini as PID 1 inside the sandbox -> clean signal forwarding so
    # `idle_timeout` can SIGTERM the worker gracefully without orphaning
    # child processes.
    .entrypoint(["/usr/bin/tini", "--"])
)
