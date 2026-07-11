"""Shared Modal Image definition for the Anthropic Managed Agents sandbox.

Mirrors the canonical layer order from:
  https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/
  self_hosted_sandboxes/modal

Layers are ordered cheap -> expensive so a typo in the last `pip_install`
doesn't invalidate the Node + apt cache.
"""

from __future__ import annotations

import modal

ANT_VERSION = "v1.17.0"
ANT_RELEASE_VERSION = ANT_VERSION.removeprefix("v")

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
    # Node 22 (available for JavaScript-based tool calls).
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        "apt-get install -y --no-install-recommends nodejs",
        "node --version && npm --version",
    )
    # `ant` CLI - the Managed Agents worker binary that `sandbox_runner.py`
    # invokes once per work item.
    .run_commands(
        (
            "set -eux; "
            'case "$(uname -m)" in '
            'x86_64) ANT_ARCH="amd64" ;; '
            'aarch64|arm64) ANT_ARCH="arm64" ;; '
            '*) echo "Unsupported ant arch: $(uname -m)"; exit 1 ;; '
            "esac; "
            "mkdir -p /tmp/ant; "
            "curl -fsSL -o /tmp/ant/ant.tar.gz "
            f"https://github.com/anthropics/anthropic-cli/releases/download/{ANT_VERSION}/"
            f"ant_{ANT_RELEASE_VERSION}_linux_${{ANT_ARCH}}.tar.gz; "
            "tar -xzf /tmp/ant/ant.tar.gz -C /tmp/ant; "
            "install -m 0755 /tmp/ant/ant /usr/local/bin/ant; "
            "rm -rf /tmp/ant"
        ),
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
