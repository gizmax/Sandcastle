# Vercel cookbook for Anthropic Managed Agents (self-hosted sandboxes)

Vercel Functions + `@vercel/sandbox` microVMs as the sandbox provider for
Anthropic Managed Agents. The defining feature: **credential brokering**
keeps `ANTHROPIC_ENVIRONMENT_KEY` entirely inside the Vercel Function and
out of the sandbox VM. All Anthropic traffic from the sandbox is
authenticated at the network firewall using `networkPolicy.allow`.

Mirrors [`anthropics/claude-cookbooks/managed_agents/self_hosted_sandboxes/vercel`](https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/self_hosted_sandboxes/vercel).

## 5-step walkthrough

### Step 1: Install dependencies

```bash
cd deploy/cookbooks/vercel
npm install
```

Vercel Sandbox requires Node 22+ on the function (declared in
`package.json` -> `engines`).

### Step 2: Wire secrets in Vercel

```bash
vercel link
vercel env add ANTHROPIC_ENVIRONMENT_ID
vercel env add ANTHROPIC_ENVIRONMENT_KEY
vercel env add ANTHROPIC_WEBHOOK_SECRET   # placeholder for now
```

Use **environment keys** (`sk-ant-oat-...`), not org API keys.

### Step 3: Deploy

```bash
vercel deploy --prod
# -> https://<project>.vercel.app/api/runner
```

`vercel.json` pins `maxDuration: 60` for the webhook (short, acks within
60s) and `3600` for the spawn helper (sandbox lifecycle bookkeeping).

### Step 4: Register webhook + finalize secret

In Anthropic Console -> Settings -> Webhooks, paste the deployment URL
plus `/api/runner` and subscribe to `session.status_run_started`. Copy
the signing secret into Vercel:

```bash
vercel env rm ANTHROPIC_WEBHOOK_SECRET production
vercel env add ANTHROPIC_WEBHOOK_SECRET production   # paste whsec_...
vercel deploy --prod
```

### Step 5: Trigger a session

```javascript
import Anthropic from "@anthropic-ai/sdk";
const c = new Anthropic({ apiKey: process.env.ANTHROPIC_ENVIRONMENT_KEY });
const session = await c.beta.sessions.create({
  agent: "agent_...",
  environmentId: "env_...",
});
await c.beta.sessions.events.send(session.id, {
  events: [{ type: "user.message", content: "..." }],
});
```

The Vercel Function verifies the webhook signature, drains the work
queue, and spawns one microVM per work item with `ms('1h')` timeout.

## How credential brokering keeps the token out of sandbox memory

The standard mistake is forwarding `ANTHROPIC_ENVIRONMENT_KEY` into the
sandbox via `spawn({ env: { ... } })`. That puts a long-lived OAuth
token inside a VM that runs untrusted model-generated code. A single
`process.env` dump or `/proc/self/environ` read leaks the key.

This cookbook instead:

1. The **Function** holds the key (`process.env.ANTHROPIC_ENVIRONMENT_KEY`)
   and uses it to verify webhooks + drain the work queue.
2. `Sandbox.create({ networkPolicy: { allow: [{ host: "api.anthropic.com" }] } })`
   tells Vercel's firewall to permit outbound calls to Anthropic AND to
   inject the credential at the TLS termination layer.
3. `sandbox.spawn(['node', 'runner.mjs'], { env: { SESSION_ID, WORK_ITEM_ID, ANTHROPIC_ENVIRONMENT_ID } })`
   passes only non-secret routing IDs into the VM. **There is no
   `ANTHROPIC_ENVIRONMENT_KEY` key in the `env` object.**
4. Inside the sandbox, `new Anthropic({ apiKey: "vercel-firewall-managed" })`
   is a sentinel string. The real Authorization header is rewritten on
   the wire by Vercel.

Net effect: a sandbox memory disclosure cannot exfiltrate the key. The
worst case is a sandbox that can call `api.anthropic.com` for the life
of the microVM (max 1h) - and revoking the environment key in Console
immediately cuts off all in-flight sandboxes.
