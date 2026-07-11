# syntax=docker/dockerfile:1.7
#
# Sandcastle self-hosted sandbox worker image for Cloudflare Containers.
#
# Mirrors deploy/cookbooks/docker/Dockerfile (same ant CLI + Python deps),
# but trimmed for the Cloudflare Containers runtime:
#
#   - no docker-compose / no spawn.sh
#   - entrypoint is `ant beta:worker run` (one-shot, exits when session done)
#   - non-root (UID 10001) + /workspace as the work dir
#   - environment key is injected per session by the parent Worker via env
#
# Build is handled implicitly by `wrangler deploy` once `[[containers]]`
# references this Dockerfile.

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22
ARG ANT_VERSION=v1.17.0

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG NODE_VERSION
ARG ANT_VERSION
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
    && curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

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

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    ANT_LOG_FORMAT=json

# Match the docker/ cookbook: minimal runtime deps only.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        tar \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Carry over Node + ant CLI from the builder stage.
COPY --from=builder /usr/local/bin/ant /usr/local/bin/ant
COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/bin/npm /usr/bin/npm
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
RUN ant --version

# Non-root user. UID 10001 matches the docker/ cookbook so the Sandcastle
# worker volume permissions line up across runtimes.
RUN groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash sandbox

WORKDIR /workspace
RUN chown -R 10001:10001 /workspace

USER 10001

# Cloudflare Containers receives ANTHROPIC_ENVIRONMENT_KEY +
# ANTHROPIC_ENVIRONMENT_ID + SESSION_ID via the Container env that the parent
# Worker passes through `containerStub.start({ env: ... })`.
ENTRYPOINT ["ant", "beta:worker", "run"]
CMD ["--log-format=json", "--idle-timeout=60"]
