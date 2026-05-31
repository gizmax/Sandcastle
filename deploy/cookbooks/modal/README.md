# Modal cookbook for Anthropic Managed Agents (self-hosted sandboxes)

GPU-attached sandbox provider for Anthropic Managed Agents. Each session
runs inside a `modal.Sandbox` with a persistent `modal.Volume` mounted at
`/workspace` and a configurable GPU (`l4` -> `b200`). Idle sandboxes are
reaped after 10 minutes; the volume keeps weights and artefacts available
for the next turn.

Mirrors [`anthropics/claude-cookbooks/managed_agents/self_hosted_sandboxes/modal`](https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/self_hosted_sandboxes/modal).

## 5-step walkthrough

### Step 1: Install the Modal SDK & authenticate

```bash
pip install modal
modal token new   # opens the browser, writes to ~/.modal.toml
```

### Step 2: Create the Modal secret

```bash
cp .env.example .env
modal secret create cma-self-hosted-sandboxes-secrets \
    ANTHROPIC_ENVIRONMENT_ID="$ANTHROPIC_ENVIRONMENT_ID" \
    ANTHROPIC_ENVIRONMENT_KEY="$ANTHROPIC_ENVIRONMENT_KEY" \
    ANTHROPIC_WEBHOOK_SECRET="placeholder"
```

The webhook secret stays as `placeholder` for now - we update it in
Step 4 once Anthropic Console mints the real signing key.

### Step 3: Deploy the webhook

```bash
modal deploy sandbox_runner.py
# -> https://<workspace>--cma-self-hosted-sandboxes-webhook.modal.run
```

The image build pre-bakes `ant` CLI + Node 22 + Anthropic SDK so per-
session cold-start drops to ~3s on a warm GPU pool.

### Step 4: Register the webhook with Anthropic

In Anthropic Console -> Settings -> Webhooks, paste the Modal endpoint
URL and subscribe to `session.status_run_started`. Copy the signing
secret and replace the placeholder:

```bash
modal secret create cma-self-hosted-sandboxes-secrets \
    ANTHROPIC_WEBHOOK_SECRET="whsec_..." --force
```

### Step 5: Trigger a session

```python
from anthropic import Anthropic
c = Anthropic(api_key="<env-key>")
session = c.beta.sessions.create(agent="agent_...", environment_id="env_...")
c.beta.sessions.events.send(session.id, events=[{"type": "user.message", ...}])
```

The webhook spins up a GPU sandbox, mounts `/workspace`, and runs
`ant beta:worker run --once`.

## When to use L4 vs B200

| GPU  | VRAM    | $/hr  | Use case                                                |
| ---- | ------- | ----- | ------------------------------------------------------- |
| L4   | 24 GB   | $0.80 | Default. Agent tool calls + small (<7B) inference.      |
| A10G | 24 GB   | $1.10 | fp16 throughput-sensitive workloads.                    |
| A100 | 40/80GB | $3.40 | 13B-30B inference, mid-tier fine-tuning.                |
| H100 | 80 GB   | $4.00 | Long-context (>32k) inference, fast training loops.     |
| B200 | 192 GB  | $5.00 | 70B+ inference, frontier training. Use only when L4 OOMs. |

Set via `MODAL_GPU=b200` in `.env` (sandbox-runner reads `os.environ`).
Match GPU class to your `agent` template's actual VRAM footprint. The
idle timeout (600s) means an L4 burning 10 min/day costs ~$4/mo per
session, while a B200 burns ~$25/mo. Prefer the smallest GPU that fits.
