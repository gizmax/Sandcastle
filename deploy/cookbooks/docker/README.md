# Self-Hosted Sandbox - Docker Cookbook

Canonical reference for running Anthropic Managed Agents inside your own
Docker host using Sandcastle's `runtime: "self-hosted-sandbox"`. Mirrors
Anthropic's [self-hosted sandboxes docs](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes).

Two layers:

- **Poller** (`sandcastle-worker`) - long-running, claims work items from
  `/v1/environments/{id}/work`.
- **Per-session containers** - ephemeral, spawned by `spawn.sh` for every
  claimed work item, run `ant beta:worker run` once, then disappear.

## 1. Build the worker image

```sh
docker build -t sandcastle/worker:latest deploy/cookbooks/docker/
```

Override pinned versions via `--build-arg PYTHON_VERSION=3.12`,
`NODE_VERSION=22`, `ANT_VERSION=v1.17.0`. Browsers (Playwright, LightPanda)
are pre-baked so per-session containers do not pay first-run cost.

## 2. Create your env file

```sh
cat > .env <<'EOF'
ANTHROPIC_ENVIRONMENT_ID=env_xxxxxxxxxxxx
ANTHROPIC_ENVIRONMENT_KEY=sk-ant-oat01-xxxxxxxxxxxxxxxxxxxxxxxx
# Optional
ANTHROPIC_BASE_URL=https://api.anthropic.com
SANDCASTLE_SESSION_TIMEOUT=1800
EOF
```

> **Important:** Do **not** set `ANTHROPIC_API_KEY` on the worker host.
> Org-scoped keys are exposed to every tool call inside the sandbox. The
> entrypoint hard-errors if it sees one.

## 3. Start the poller

```sh
docker compose up -d
docker compose logs -f sandcastle-worker
curl -fsS http://localhost:8081/healthz
```

The compose file creates a `session-net` bridge that `spawn.sh` attaches
per-session containers to, and mounts the Docker socket so the poller
can launch siblings.

## 4. Trigger work from a managed-agent step

In any Sandcastle workflow:

```yaml
- id: heavy-research
  type: managed-agent
  runtime: "self-hosted-sandbox"
  managed_agent_config:
    agent_template: researcher
    self_hosted_sandbox:
      environment_id: env_xxxxxxxxxxxx
      environment_key_env: ANTHROPIC_ENVIRONMENT_KEY
      provider: docker
```

Sandcastle dispatches the session-create call with the `environment`
block; Anthropic enqueues a work item; your poller claims it and runs it
through `spawn.sh`. Outputs land in the per-session named volume and are
uploaded by the poller before the volume is removed.

## 5. Clean shutdown

```sh
docker compose down
docker volume ls --filter "name=sc-session-" -q | xargs -r docker volume rm
```

The `trap cleanup EXIT INT TERM` in `spawn.sh` removes each per-session
volume on its own, so manual cleanup is only needed if the poller was
killed mid-flight.
