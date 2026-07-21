#!/bin/sh
# spawn.sh - canonical per-session container spawn for the self-hosted
# sandbox runtime. The long-running poller (sandcastle-worker) calls this
# script once for every work item it claims from
# /v1/environments/{id}/work. Each invocation creates a single ephemeral
# container running `ant beta:worker run`, isolated on the
# `session-net` network and given a tmpfs /workspace.
#
# Requires: Docker CLI and GNU coreutils (for timeout). /bin/sh is sufficient.
#
# Inputs (env):
#   ANTHROPIC_SESSION_ID         (required) - session being routed here
#   ANTHROPIC_WORK_ID            (required) - work item ID to ack
#   ANTHROPIC_ENVIRONMENT_ID     (required) - env_xxx
#   ANTHROPIC_ENVIRONMENT_KEY    (required) - sk-ant-oat01-xxx
#   ANTHROPIC_BASE_URL           (optional) - default api.anthropic.com
#   SANDCASTLE_WORKER_IMAGE      (optional) - default sandcastle/worker:latest
#   SANDCASTLE_SESSION_TIMEOUT   (optional) - hard kill seconds (default 1800)
#
# Outputs:
#   exit 0   - session completed, work item acked
#   exit 64  - missing required env var (config error)
#   exit 65  - docker run failed
#   exit 124 - session timed out
#
# Cleanup: a trap on EXIT removes the per-session named volume even when
# docker run fails or the session times out, so we never leak disk.

set -eu

BASE_URL="${ANTHROPIC_BASE_URL:-https://api.anthropic.com}"
WORKER_IMAGE="${SANDCASTLE_WORKER_IMAGE:-sandcastle/worker:latest}"
TIMEOUT_S="${SANDCASTLE_SESSION_TIMEOUT:-1800}"

require_var() {
    name="$1"
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        echo "spawn.sh: missing required env var: $name" >&2
        exit 64
    fi
}

require_var ANTHROPIC_SESSION_ID
require_var ANTHROPIC_WORK_ID
require_var ANTHROPIC_ENVIRONMENT_ID
require_var ANTHROPIC_ENVIRONMENT_KEY

# Per-session named volume holds outputs that survive container teardown
# long enough for the poller to upload them to the artifact store.
VOLUME="sc-session-${ANTHROPIC_SESSION_ID}"
CONTAINER="sc-sess-${ANTHROPIC_SESSION_ID}"

cleanup() {
    rc=$?
    # Remove the named volume; ignore errors (volume may already be gone
    # if docker run never got far enough to create it).
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    exit "$rc"
}
trap cleanup EXIT INT TERM

docker volume create "$VOLUME" >/dev/null

# --rm so the container is auto-removed (we still rm -f in trap for the
# kill paths). --network=session-net pins traffic to the segregated
# bridge defined in docker-compose.yml. tmpfs /workspace gives the agent
# a private scratch space that disappears on container exit.
set +e
timeout --signal=TERM --kill-after=15 "$TIMEOUT_S" \
    docker run \
        --rm \
        --name "$CONTAINER" \
        --network=session-net \
        --tmpfs /workspace:rw,exec,uid=10001,gid=10001,mode=700,size=2g \
        --mount "type=volume,source=${VOLUME},target=/mnt/session/outputs" \
        --env "ANTHROPIC_SESSION_ID=${ANTHROPIC_SESSION_ID}" \
        --env "ANTHROPIC_WORK_ID=${ANTHROPIC_WORK_ID}" \
        --env "ANTHROPIC_ENVIRONMENT_ID=${ANTHROPIC_ENVIRONMENT_ID}" \
        --env "ANTHROPIC_ENVIRONMENT_KEY=${ANTHROPIC_ENVIRONMENT_KEY}" \
        --env "ANTHROPIC_BASE_URL=${BASE_URL}" \
        --read-only \
        --security-opt=no-new-privileges \
        --cap-drop=ALL \
        "$WORKER_IMAGE" \
        ant beta:worker run
RC=$?
set -e

if [ "$RC" -eq 124 ]; then
    echo "spawn.sh: session ${ANTHROPIC_SESSION_ID} timed out after ${TIMEOUT_S}s" >&2
    exit 124
fi

if [ "$RC" -ne 0 ]; then
    echo "spawn.sh: docker run failed for ${ANTHROPIC_SESSION_ID} (rc=${RC})" >&2
    exit 65
fi

exit 0
