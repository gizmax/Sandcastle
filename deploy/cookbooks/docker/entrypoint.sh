#!/bin/sh

# Docker named volumes are created as root. Prepare the writable mounts, then
# execute ant as the unprivileged sandbox user.
set -eu

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "ANTHROPIC_API_KEY must not be set on a self-hosted sandbox worker; use ANTHROPIC_ENVIRONMENT_KEY." >&2
    exit 64
fi

for writable_dir in /workspace /mnt/session/outputs; do
    mkdir -p "$writable_dir"
    chown 10001:10001 "$writable_dir"
done

# The Docker socket's numeric GID comes from the host and is not reliably
# representable in /etc/group. Preserve only the worker group and that socket
# group before dropping root; all sandbox commands then run as uid 10001.
worker_groups="10001"
if [ -S /var/run/docker.sock ]; then
    socket_gid="$(stat -c '%g' /var/run/docker.sock)"
    if [ "$socket_gid" != "10001" ]; then
        worker_groups="${worker_groups},${socket_gid}"
    fi
fi

exec setpriv --reuid=10001 --regid=10001 --groups "$worker_groups" "$@"
