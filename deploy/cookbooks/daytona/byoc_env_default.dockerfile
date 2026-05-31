# Pre-baked Daytona snapshot for Anthropic Managed Agents BYOC sandboxes.
#
# Build & push to Daytona:
#   daytona snapshot create byoc-env-default --dockerfile byoc_env_default.dockerfile
#
# The resulting snapshot is referenced from sandbox_runner.py via
# CreateSandboxFromSnapshotParams(snapshot="byoc-env-default"). Cold-start
# drops from ~15s (apt + npm + pip install) to <2s when the disk is
# pre-warmed on Daytona's hot pool.

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ANT_HOME=/opt/ant

# ---- base OS deps (curl + git + tini for clean process supervision) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git tini openssh-client \
        build-essential ripgrep jq \
    && rm -rf /var/lib/apt/lists/*

# ---- Node 22 (required by ant CLI tool dispatcher) ----
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ---- ant CLI: Anthropic's Managed Agents worker binary ----
# Pin to a known-good version. `ant beta:worker run --once` is the entrypoint
# invoked by sandbox_runner.py inside the sandbox.
RUN npm install -g @anthropic-ai/ant@latest \
    && ant --version

# ---- Python SDK (pre-baked so per-sandbox cold start skips pip install) ----
RUN pip install --no-cache-dir \
        "anthropic>=0.45" \
        "anthropic[beta]" \
        standardwebhooks

# ---- session workspace (persisted via Daytona snapshot disk) ----
RUN mkdir -p /mnt/session/outputs /mnt/session/skills /opt/ant/skills \
    && chmod -R 0777 /mnt/session

WORKDIR /mnt/session

# Skills directory shipped with the snapshot. Mount additional skills via
# Daytona volumes if you need per-team customization.
COPY skills/ /opt/ant/skills/

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "-lc", "ant beta:worker run --once"]
