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
#                --build-arg ANT_VERSION=v1.17.0 \
#                -t sandcastle/worker:custom deploy/cookbooks/docker/

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22
ARG ANT_VERSION=v1.17.0
ARG PLAYWRIGHT_VERSION=1.61.1
ARG LIGHTPANDA_VERSION=nightly

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG NODE_VERSION
ARG ANT_VERSION
ARG PLAYWRIGHT_VERSION
ARG LIGHTPANDA_VERSION
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Documented worker requirements: bash, tar, unzip, curl, ca-certificates.
# Node 22 stays available for JavaScript tool calls; ant is a release binary.
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

# Install the official Anthropic ant CLI release.
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
    && ant --version \
    && rm -rf /tmp/ant

# Python deps. We do NOT vendor the Sandcastle source here - the worker
# only needs the ant CLI + a tiny Python shim for output upload.
RUN pip install --prefix=/install httpx pydantic

# Pre-bake Playwright browsers so per-session containers do not download
# them on first run.
RUN pip install --prefix=/install "playwright==${PLAYWRIGHT_VERSION}" \
    && PYTHONPATH=/install/lib/python${PYTHON_VERSION%.*}/site-packages \
       /install/bin/playwright install --with-deps chromium || true

# LightPanda is pulled in as a static binary for headless browsing.
RUN mkdir -p /opt/lightpanda \
    && case "${TARGETARCH:-$(uname -m)}" in \
        amd64|x86_64) LIGHTPANDA_ARCH="x86_64" ;; \
        arm64|aarch64) LIGHTPANDA_ARCH="aarch64" ;; \
        *) echo "Unsupported LightPanda arch: ${TARGETARCH:-$(uname -m)}"; exit 1 ;; \
    esac \
    && curl -fsSL -o /opt/lightpanda/lightpanda \
        "https://github.com/lightpanda-io/browser/releases/download/${LIGHTPANDA_VERSION}/lightpanda-${LIGHTPANDA_ARCH}-linux" \
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
COPY --from=builder /usr/local/bin/ant /usr/local/bin/ant
COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/bin/npm /usr/bin/npm
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
COPY --from=builder /opt/lightpanda /opt/lightpanda
COPY --from=builder /root/.cache/ms-playwright /home/sandcastle/.cache/ms-playwright

RUN ant --version \
    && chown -R 10001:10001 /home/sandcastle

WORKDIR /workspace
USER 10001

# Health-check shim hits the in-process ant status endpoint when the
# worker is run as the long-running poller.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8081/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["ant", "beta:worker", "poll"]
