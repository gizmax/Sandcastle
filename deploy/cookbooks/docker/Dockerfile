# syntax=docker/dockerfile:1.7
#
# Sandcastle self-hosted sandbox worker image. Two stages:
#
#   1. builder  - pulls Python deps + ant CLI + Node 22 into a fat image
#   2. runtime  - slim, non-root, pre-baked with Playwright + LightPanda
#
# This mirrors the layout Anthropic's docker/ cookbook ships at
# https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/self_hosted_sandboxes/docker
# and is what spawn.sh exec's via `ant beta:worker run` per session.
#
# Build:
#   docker build -t sandcastle/worker:latest deploy/cookbooks/docker/
#
# Override versions:
#   docker build --build-arg PYTHON_VERSION=3.12 \
#                --build-arg NODE_VERSION=22 \
#                --build-arg ANT_VERSION=latest \
#                -t sandcastle/worker:custom deploy/cookbooks/docker/

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22
ARG ANT_VERSION=latest
ARG PLAYWRIGHT_VERSION=1.49.0
ARG LIGHTPANDA_VERSION=nightly

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG NODE_VERSION
ARG ANT_VERSION
ARG PLAYWRIGHT_VERSION
ARG LIGHTPANDA_VERSION

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Documented runtime requirements for the ant CLI: bash, tar, unzip, curl,
# ca-certificates. Node is needed because ant ships as an npm package.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        gnupg \
        tar \
        unzip \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Node ${NODE_VERSION} from NodeSource (LTS line).
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install the Anthropic ant CLI globally.
RUN npm install -g "@anthropic-ai/ant@${ANT_VERSION}"

# Python deps. We do NOT vendor the Sandcastle source here - the worker
# only needs the ant CLI + a tiny Python shim for output upload.
COPY requirements.txt /tmp/requirements.txt 2>/dev/null || true
RUN if [ -f /tmp/requirements.txt ]; then \
        pip install --prefix=/install -r /tmp/requirements.txt; \
    else \
        pip install --prefix=/install httpx pydantic; \
    fi

# Pre-bake Playwright browsers so per-session containers do not download
# them on first run.
RUN pip install --prefix=/install "playwright==${PLAYWRIGHT_VERSION}" \
    && PYTHONPATH=/install/lib/python${PYTHON_VERSION%.*}/site-packages \
       /install/bin/playwright install --with-deps chromium || true

# LightPanda is pulled in as a static binary for headless browsing.
RUN mkdir -p /opt/lightpanda \
    && curl -fsSL -o /opt/lightpanda/lightpanda \
        "https://github.com/lightpanda-io/browser/releases/download/${LIGHTPANDA_VERSION}/lightpanda-x86_64-linux" \
    && chmod +x /opt/lightpanda/lightpanda \
    || echo "LightPanda fetch failed; runtime will skip browser tool"

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG PYTHON_VERSION

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:/usr/local/bin:/usr/bin:/bin" \
    PYTHONPATH="/install/lib/python${PYTHON_VERSION%.*}/site-packages"

# Minimum required system packages for ant + tool calls (bash is non-negotiable).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        tar \
        tini \
        unzip \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 sandcastle \
    && useradd --system --uid 10001 --gid 10001 \
        --home-dir /workspace --no-create-home \
        --shell /bin/bash sandcastle

# Copy pre-baked Python, Node, ant, browsers from the builder.
COPY --from=builder /install /install
COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/bin/npm /usr/bin/npm
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
COPY --from=builder /opt/lightpanda /opt/lightpanda
COPY --from=builder /root/.cache/ms-playwright /home/sandcastle/.cache/ms-playwright

RUN ln -sf /usr/lib/node_modules/@anthropic-ai/ant/bin/ant.js /usr/local/bin/ant \
    && chown -R 10001:10001 /home/sandcastle

WORKDIR /workspace
USER 10001

# Health-check shim hits the in-process ant status endpoint when the
# worker is run as the long-running poller.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8081/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["ant", "beta:worker", "poll"]
