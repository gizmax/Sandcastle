# Self-Hosted Sandbox - Cloudflare Containers Cookbook

Run Anthropic Managed Agents on a per-session Cloudflare Container,
dispatched from a Worker that consumes Anthropic's
`session.status_run_started` webhook. Mirrors Anthropic's
[cf cookbook](https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/self_hosted_sandboxes/cf)
and pairs with `deploy/cookbooks/docker/` (same Dockerfile contract).

The Worker is the wake-up + dispatch layer. The Container is the actual
sandbox: it runs `ant beta:worker run` once, heartbeats the work item lease
itself, then exits.

## When to pick this over the pure-Worker variant

| Need                                 | This cookbook (Containers) | `cf-worker/` (DO isolate) |
|--------------------------------------|----------------------------|---------------------------|
| Real subprocess + filesystem         | yes                        | no (RAM-only fake FS)     |
| Cold start                           | ~1 to 3 s                  | ~5 ms                     |
| Disk persistence between tool calls  | yes (per Container)        | no                        |
| Bash / Playwright / language servers | yes                        | no                        |
| RAM budget per session               | up to Container limit      | ~128 MB DO                |

## 1. wrangler login

```sh
npm install
npx wrangler login
```

Pick the Cloudflare account that owns the Worker. `wrangler whoami` should
return the same account id you see in Anthropic Console -> Environments.

## 2. Create the work-queue webhook tunnel

In `wrangler.toml`, set `[vars].ANTHROPIC_ENVIRONMENT_ID` to the environment
created in Anthropic Console. Then store the two secrets:

```sh
npx wrangler secret put ANTHROPIC_ENVIRONMENT_KEY
npx wrangler secret put ANTHROPIC_WEBHOOK_SECRET
```

Both are zero-trust bindings: scoped to this Worker, never exposed to the
Container image, never written to disk. The Worker forwards
`ANTHROPIC_ENVIRONMENT_KEY` to each Container via the `dispatch()` RPC.

## 3. wrangler deploy

```sh
npx wrangler deploy
```

`wrangler` builds `./Dockerfile` for the Containers runtime, uploads the
image, and binds the `SANDBOX_CONTAINER` Durable Object class. First deploy
takes ~2 minutes; subsequent layer-cached deploys are seconds.

## 4. Confirm in the Anthropic Console

In Console -> Environments -> your env -> Webhooks, paste the Worker URL
(printed at the end of `wrangler deploy`). Click "Send test event"; the
Worker should respond `{"status": "ignored", "event_type": "ping"}` and the
function log shows `[webhook] polled work=...` for any pending session.

## 5. Trigger work from a managed-agent step

```yaml
- id: heavy-research
  type: managed-agent
  runtime: "self-hosted-sandbox"
  managed_agent_config:
    agent_template: researcher
    self_hosted_sandbox:
      environment_id: env_xxxxxxxxxxxx
      environment_key_env: ANTHROPIC_ENVIRONMENT_KEY
      provider: cloudflare-containers
```

Sandcastle dispatches the session-create call with the `environment`
block, Anthropic enqueues a work item, the webhook fires, the Worker
drains the queue, and a fresh Container handles the session.
