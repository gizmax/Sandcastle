# Self-Hosted Sandbox - Cloudflare Worker (Durable Object isolate)

Run Anthropic Managed Agents entirely inside a Cloudflare Worker Durable
Object. No Container, no subprocess, no real filesystem: the tool surface
(`bash` / `read` / `write` / `edit` / `glob` / `grep`) is implemented
against a RAM-only Map in the V8 isolate, matching the toolset advertised
by `beta_agent_toolset_20260401`. Mirrors Anthropic's
[cf-worker cookbook](https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/self_hosted_sandboxes/cf-worker).

## When to pick this over the Containers variant

| Need                                 | This cookbook (isolate) | `cloudflare/` (Container) |
|--------------------------------------|-------------------------|---------------------------|
| Cold start                           | ~5 ms                   | ~1 to 3 s                 |
| Real subprocess + shell              | no                      | yes                       |
| Disk persistence between tool calls  | no (RAM-only)           | yes                       |
| RAM budget per session               | ~128 MB DO              | up to Container limit     |
| Best for                             | docs editing, structured | research, codegen, scrape |

Pick the isolate when the agent only needs to read, write, and grep
markdown / config / structured text. Pick the Container when it needs
bash, Playwright, or language servers.

## 1. wrangler login + deploy

```sh
npm install
npx wrangler login
npx wrangler deploy
```

Set `[vars].ANTHROPIC_ENVIRONMENT_ID` in `wrangler.toml` first.

## 2. Push the secrets

```sh
npx wrangler secret put ANTHROPIC_ENVIRONMENT_KEY
npx wrangler secret put ANTHROPIC_WEBHOOK_SECRET
```

Both are scoped to this Worker only.

## 3. Confirm in the Anthropic Console

Console -> Environments -> your env -> Webhooks. Paste the Worker URL,
send a test event, and verify the response is
`{"status": "ignored", "event_type": "ping"}`.

## 4. Trigger work from a managed-agent step

```yaml
- id: lite-summarize
  type: managed-agent
  runtime: "self-hosted-sandbox"
  managed_agent_config:
    agent_template: editor
    self_hosted_sandbox:
      environment_id: env_xxxxxxxxxxxx
      environment_key_env: ANTHROPIC_ENVIRONMENT_KEY
      provider: cloudflare-worker
```

Sandcastle dispatches the session-create call, Anthropic enqueues a work
item, the webhook fires, the Worker drains the queue, and a fresh
`SessionToolRunner` DO handles the session in-isolate.
