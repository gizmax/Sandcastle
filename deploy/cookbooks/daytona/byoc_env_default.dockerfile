# Pre-baked Daytona snapshot for Anthropic Managed Agents BYOC sandboxes.
#
# Build & push to Daytona:
#   daytona snapshot create byoc-env-default --dockerfile byoc_env_default.dockerfile
#
# The resulting snapshot is referenced from sandbox_runner.py via
# CreateSandboxFromSnapshotParams(snapshot="byoc-env-default"). Cold-start
# drops from ~15s (apt + npm + pip install) to <2s when the disk is
# pre-warmed on Daytona's hot pool.

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22
ARG ANT_VERSION=v1.17.0

FROM python:${PYTHON_VERSION}-slim-bookworm

ARG NODE_VERSION
ARG ANT_VERSION
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ANT_HOME=/opt/ant

# ---- base OS deps (curl + git + tini for clean process supervision) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git tini openssh-client \
        build-essential ripgrep jq \
    && rm -rf /var/lib/apt/lists/*

# ---- Node 22 (available for JavaScript-based tool calls) ----
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ---- ant CLI: Anthropic's Managed Agents worker binary ----
# Pin to a known-good version. `ant beta:worker run --once` is the entrypoint
# invoked by sandbox_runner.py inside the sandbox.
RUN mkdir -p /tmp/ant \
    && ANT_VERSION_NO_V="${ANT_VERSION#v}" \
    && case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64|x86_64) ANT_ARCH="amd64" ;; \
        arm64|aarch64) ANT_ARCH="arm64" ;; \
        *) echo "Unsupported ant arch: ${TARGETARCH:-$(dpkg --print-architecture)}"; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/ant/ant.tar.gz \
        "https://github.com/anthropics/anthropic-cli/releases/download/${ANT_VERSION}/ant_${ANT_VERSION_NO_V}_linux_${ANT_ARCH}.tar.gz" \
    && tar -xzf /tmp/ant/ant.tar.gz -C /tmp/ant \
    && install -m 0755 /tmp/ant/ant /usr/local/bin/ant \
    && rm -rf /tmp/ant \
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

# Mount or copy additional skills into /opt/ant/skills for per-team customization.

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "-lc", "ant beta:worker run --once"]
