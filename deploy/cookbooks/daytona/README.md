# Daytona cookbook for Anthropic Managed Agents (self-hosted sandboxes)

Snapshot-backed sandbox provider for Anthropic Managed Agents. Each session
runs inside a Daytona sandbox cloned from a pre-baked snapshot, then
auto-pauses to disk between turns so the next webhook resumes in under a
second with `/mnt/session` state intact.

Mirrors [`anthropics/claude-cookbooks/managed_agents/self_hosted_sandboxes/daytona`](https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/self_hosted_sandboxes/daytona).

## 5-step walkthrough

### Step 1: Build the snapshot

```bash
daytona snapshot create byoc-env-default \
    --dockerfile byoc_env_default.dockerfile \
    --context .
```

The snapshot ships `ant` CLI, Node 22, Python 3.12, the Anthropic SDK and
a `skills/` directory. Pre-baking these dependencies eliminates the
~15s cold-start hit you pay when running `pip install anthropic` in a
fresh sandbox.

### Step 2: Provision credentials

```bash
cp .env.example .env
# Fill in DAYTONA_API_KEY + ANTHROPIC_ENVIRONMENT_KEY + ANTHROPIC_ENVIRONMENT_ID
```

Use an **environment key** (`sk-ant-oat-...`), never an org-wide API key.
The environment key is scoped to a single Managed Agents environment and
can be revoked without affecting other workloads.

### Step 3: Start the webhook receiver

```bash
uv sync   # or: pip install -e .
uv run uvicorn sandbox_runner:app --host 0.0.0.0 --port 8080
```

### Step 4: Register the webhook with Anthropic

In Anthropic Console -> Settings -> Webhooks, add the public URL of your
receiver (e.g. via `cloudflared tunnel`) and subscribe to
`session.status_run_started`. Copy the signing secret into
`ANTHROPIC_WEBHOOK_SECRET`.

### Step 5: Trigger a session

```python
from anthropic import Anthropic

c = Anthropic(api_key="<env-key>")
session = c.beta.sessions.create(agent="agent_...", environment_id="env_...")
c.beta.sessions.events.send(session.id, events=[{"type": "user.message", ...}])
```

The control plane fires `session.status_run_started`, the receiver verifies
the signature, drains the work queue, and `daytona.create()` clones the
snapshot with `byoc.session_id` labels so resumed sandboxes route back to
the same session.

## How snapshot + auto-pause preserves state across worker restarts

| Phase                | Sandbox state           | Cost     |
| -------------------- | ----------------------- | -------- |
| First webhook        | Clone snapshot, run     | ~2s warm |
| Idle 600s            | Auto-pause to disk      | $0       |
| Next user turn       | Resume from disk        | <1s      |
| `/mnt/session` data  | Preserved across pauses | -        |

Daytona's `auto_stop_interval` (set via `DAYTONA_AUTO_STOP_INTERVAL` in
`.env`) suspends the sandbox after the configured idle window. Resume is
transparent: the next `session.status_run_started` webhook re-attaches to
the existing sandbox by label and `ant beta:worker run --once` picks up
exactly where it left off. This is the key advantage over ephemeral
sandboxes - long-running multi-turn sessions stay cheap.
