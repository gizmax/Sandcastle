# Self-hosted Sandboxes for Managed Agents

Anthropic's 2026-05-19 Managed Agents update added a **self-hosted sandbox**
option. Tool calls run inside containers you operate; the orchestration brain
(planning, tool routing, conversation state) stays at Anthropic. Sandcastle
wires this together end to end.

Anthropic spec: <https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes>

## Why self-hosted?

Three buyer personas this is aimed at:

- **Compliance-driven EU enterprise.** PII, financial data, or anything else
  the EU AI Act flags as high-risk must never leave the regulated substrate.
  Self-hosted sandboxes keep the tool exhaust (file reads, browser sessions,
  SQL queries) inside the boundary you already audit. See
  [site/eu-ai-act](../site/eu-ai-act/index.html) for the Article mapping.
- **GPU-intensive ML team.** Hosted sandboxes top out at standard CPU shapes.
  Running on Modal, Daytona, or your own Kubernetes lets agents call into
  H100/A100 fleets for OCR, embeddings, or fine-tune evaluation without
  shuffling artifacts through Anthropic's network.
- **Latency-sensitive edge.** Cloudflare Workers + Containers put the tool
  surface within ~10 ms of the user (Cloudflare measures 50 ms p50 globally).
  The brain still talks to the Claude API, but every tool call short-circuits
  to the regional edge. Reference: <https://blog.cloudflare.com/containers-ga/>.

## Decision tree: which sandbox partner?

| Provider   | Deployment unit             | Cold start | GPU     | Stateful disk | Credential brokering              | Cost model                   | Docs |
|------------|-----------------------------|------------|---------|---------------|------------------------------------|-------------------------------|------|
| Cloudflare | Worker + Container          | ~300 ms    | No      | R2 / D1 only  | Workers Secrets / Service bindings | Per-request + container-second | <https://developers.cloudflare.com/containers/> |
| Daytona    | Dev sandbox (Firecracker)   | ~1 s       | Yes (A10/L4) | Yes      | OAuth / vault-issued JWT          | Per-sandbox-minute             | <https://www.daytona.io/docs/sandboxes> |
| Modal      | Function-class container    | ~500 ms    | Yes (H100) | Volumes      | Modal Secrets                      | Per-second compute             | <https://modal.com/docs/guide/sandboxes> |
| Vercel     | Sandbox (Fluid Compute)     | ~150 ms    | No      | Ephemeral     | Vercel env vars                    | Per-invocation                 | <https://vercel.com/docs/functions/sandboxes> |
| Docker     | docker run / Compose / k8s  | depends    | Yes (driver) | Yes      | Your secret manager                | Whatever you already pay       | <https://docs.docker.com/> |

Pick on the dominant constraint, not the feature list. Regulated workloads
default to Docker on your own cluster. Spiky public-facing workloads default
to Cloudflare. GPU OCR or ML evaluation defaults to Modal or Daytona.

## The 4 hard limits

These come straight from Anthropic's
[self-hosted-sandboxes#limitations](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes#limitations)
page. Sandcastle enforces all four at config-parse time so the workflow
fails fast instead of paging an on-call at 02:00.

1. **Memory Stores are NOT supported with self-hosted sandboxes.**
   The Anthropic-side Memory tool refuses to bind to a sandbox session that
   carries a self-hosted environment block.
   *Sandcastle workaround:* run our Memory MCP server inside the boundary and
   expose it over an MCP tunnel. See
   [managed-agents-mcp-tunnels.md](./managed-agents-mcp-tunnels.md) and
   [memory-mcp-server.md](./memory-mcp-server.md). Enforced by
   `assert_memory_compatible` in `src/sandcastle/engine/self_hosted_sandbox.py`.
2. **Claude Platform on AWS is unsupported.** Self-hosted sandboxes require
   the standard Anthropic endpoint, not Bedrock or AWS Claude Platform.
   Sandcastle warns via `warn_on_aws_region` when `AWS_REGION` /
   `ANTHROPIC_REGION` looks AWS-bound.
3. **`/bin/bash` must exist at that exact path inside the container.** Spawn
   uses an absolute path; Alpine-based images need the `bash` package and a
   symlink. The reference Dockerfiles under `deploy/cookbooks/*/Dockerfile`
   already do this.
4. **`ANTHROPIC_API_KEY` (the org key) must NOT be on the worker host.**
   Workers use the environment-scoped key (`sk-ant-oat01-...`) issued by
   Anthropic per sandbox environment. `assert_org_key_not_set` refuses to
   start the worker if the org key is present.

## Sandcastle wiring

A self-hosted managed-agent step looks like this:

```yaml
- id: heavy-research
  type: managed-agent
  managed_agent_config:
    agent_template: researcher
    self_hosted_sandbox:
      environment_id: env_abc123
      environment_key_env: SANDCASTLE_ENV_KEY_PRIMARY
      provider: cloudflare
      metadata:
        team: research
        cost_center: rnd-7714
      max_concurrent_sessions: 16
```

The block maps directly to `SelfHostedSandboxConfig` in
`src/sandcastle/engine/self_hosted_sandbox.py`. `environment_key_env` names an
env var; Sandcastle never accepts the key inline so secrets stay out of YAML
and git history. `build_environment_block(config)` produces the JSON
fragment Anthropic's session-create call expects (see
<https://platform.claude.com/docs/en/api/managed-agents-sessions>).

## Choosing always-on poller vs webhook-triggered

The self-hosted worker fleet can claim work in two modes:

- **Always-on poller.** Each worker holds an open long-poll against
  `work/list`. Lowest end-to-end latency (~50 ms). Best for steady traffic
  and small fleets. Cost is one always-warm process per worker.
- **Webhook-triggered.** Anthropic POSTs to your webhook endpoint when a
  session has work available; the worker scales from zero on demand. Best
  for spiky or low-volume workloads, FaaS-style deployments, and edge
  platforms (Cloudflare Workers, Vercel) that bill per-invocation. Adds
  ~200 ms of wake-up latency but drops idle cost to zero.

Anthropic's recommendation (per the May 19 launch post,
<https://www.anthropic.com/news/managed-agents-self-hosted>): always-on
for production fleets serving >1 req/s sustained; webhook below that.

## Deployment recipe per provider

Each cookbook under `deploy/cookbooks/` contains a working Dockerfile, the
provider's deploy manifest, and a 5-minute happy-path script.

- **Cloudflare Workers + Containers.** Worker handles the webhook, spawns a
  Container per session. See `deploy/cookbooks/cloudflare/` (full Container
  pattern) and `deploy/cookbooks/cf-worker/` (Worker-only, no Container).
  Cite: <https://developers.cloudflare.com/workers/>
- **Daytona.** One Daytona sandbox per session, lifecycled via the Daytona
  SDK. See `deploy/cookbooks/daytona/`. GPU shapes available.
  Cite: <https://www.daytona.io/docs>
- **Modal.** `modal.Sandbox` per session, claimed by a Modal function that
  polls `work/list`. See `deploy/cookbooks/modal/`.
  Cite: <https://modal.com/docs/guide/sandboxes>
- **Vercel Sandbox.** Webhook on a Vercel Function spawns a Sandbox via the
  Vercel SDK. See `deploy/cookbooks/vercel/`.
  Cite: <https://vercel.com/docs/functions/sandboxes>
- **Docker / Kubernetes.** The reference local-dev path. See
  `deploy/cookbooks/docker/README.md`. Pair with HPA on the metric below.

## Monitoring + autoscaling

Sandcastle exposes Anthropic's `work/stats` endpoint at
`GET /admin/environments/{id}/work/stats`. The handler caches for 5 s and
streams the same payload as SSE on
`GET /admin/environments/{id}/work/stats/stream`. See
`src/sandcastle/api/environments_admin.py`. Anthropic spec:
<https://platform.claude.com/docs/en/api/managed-agents-work-stats>.

The dashboard's `WorkQueuePanel` (`dashboard/src/components/runs/WorkQueuePanel.tsx`)
renders depth, claim rate, and per-worker throughput.

Recommended HPA thresholds:

- Scale out when `queue_depth / worker_count > 4` for 30 s.
- Scale in when `queue_depth == 0` and `worker_utilisation < 0.2` for 5 min.
- Hard floor of 1 worker if you depend on the always-on poller pattern;
  hard floor of 0 if webhook-triggered.

## Migration from hosted to self-hosted

A working 6-step checklist when moving an existing managed-agent step.

1. **Stand up an environment.** `POST /v1/environments` returns
   `environment_id` and a one-time environment key. Store the key in your
   secret manager under the name you'll reference via `environment_key_env`.
2. **Pick a provider and ship a worker.** Copy the matching cookbook from
   `deploy/cookbooks/`. Confirm `/bin/bash` exists in the container.
3. **Strip incompatible config.** Remove `memory_stores` from the
   managed-agent step (move it to a Memory MCP server, see above). Remove
   any `aws_region` overrides.
4. **Rotate keys off the worker host.** Verify `ANTHROPIC_API_KEY` is not
   present in the worker process environment. Sandcastle's
   `assert_org_key_not_set` will refuse to start otherwise.
5. **Add the YAML block.** Append `self_hosted_sandbox:` to the
   managed-agent step as shown above.
6. **Smoke test, then cut over.** Run the workflow with `--dry-run` first to
   exercise validation. Then route 10% of production traffic via a feature
   flag. Watch `WorkQueuePanel` for queue depth divergence before going 100%.

A clean migration takes a working afternoon for a single workflow on
Docker; the long tail is provider-specific networking (egress allowlists,
Anthropic webhook signature verification).
