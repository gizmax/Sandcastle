# Customer Case-Study Templates

Reference workflows that mirror the customer deployments Anthropic
called out in their 2026-05-19 Managed Agents blog post. They are
intended as fork-able starting points for EU enterprises evaluating
Sandcastle against the same problems Amplitude, Clay, and Rogo are
already solving in production on Anthropic's stack.

## Why this exists

Sandcastle ships dozens of step types, three sandbox runtimes, an
outcomes system, and an MCP-tunnel preview, but a flat feature list
does not tell prospects "this is the proven shape your peers run".
The templates in this directory each take one of the patterns
Anthropic explicitly cited as a reference deployment and encode it as
a runnable Sandcastle YAML. The marketplace page links to each
template; the landing page screenshots them.

## Templates

### amplitude-design-agent.yaml

- Who: Amplitude's design-systems team.
- What it exercises: `managed-agent` with the `designer` template, a
  `multiagent` coordinator dispatching to an `accessibility-review`
  specialist, a follow-up `computer-use` step driving headless
  Chromium for screenshot evidence, and an Anthropic MCP tunnel for
  brand-token persistence (Memory Stores are not compatible with
  self-hosted - see "Production gotchas" below).
- Deploy: pre-provision a Cloudflare-backed Anthropic environment
  named `env_amplitude_design`, set `AMPLITUDE_ENV_KEY` to the
  matching `sk-ant-oat01-...` environment key, point
  `AMPLITUDE_DESIGN_MEMORY_BEARER` at a Sandcastle Memory MCP
  reachable through your tunnel.

### clay-sculptor-gtm.yaml

- Who: Clay's GTM engineering team.
- What it exercises: A coordinator agent with three specialists
  (researcher, writer, qualifier), a Daytona-backed long-running
  sandbox, a Composio CRM write, an HTTP enrichment call, a Slack
  notify, a PDF rep-briefing report, and an outcome capturing the
  qualifier's confidence score. Includes a nightly cron schedule.
- Deploy: pre-provision a Daytona environment via Anthropic, set
  `CLAY_ENV_KEY`, wire `LINKEDIN_ENRICHMENT_TOKEN` for enrichment,
  `CLAY_BUYER_MEMORY_BEARER` for the optional buyer-history tunnel,
  and the Composio Salesforce (or HubSpot / Attio) connection.

### rogo-analyst-on-private-data.yaml

- Who: Rogo's financial-analyst product team.
- What it exercises: A read-only `financial_analyst` managed agent on
  Vercel, an MCP tunnel to the customer's private financial-data MCP
  server (`auth_mode: workload_identity_federation` so no static
  bearer ever lands on disk), an upstream approval gate
  (`risk_level: high` requires it under the EU AI Act validator), and
  an HTTP eval-gate post-step that blocks promotion below 0.8 on the
  golden-question dataset.
- Deploy: pre-provision a Vercel-backed environment, set `ROGO_ENV_KEY`,
  ensure your customer's MCP server is published behind your Anthropic
  tunnel, and wire `ROGO_FINANCIALS_BEARER` + `ROGO_EVAL_TOKEN`.

## Pre-requisites

Every template in this directory needs the following before it will run:

1. An Anthropic Managed Agents environment key
   (`sk-ant-oat01-...`) for the named provider, exported as the env
   variable referenced in each template's
   `managed_agent_config.self_hosted_sandbox.environment_key_env`.
   Org-scoped keys (`sk-ant-api03-...`) are refused at startup.
2. The MCP tunnels preview enabled (`SANDCASTLE_MCP_TUNNELS=1`) plus
   the tunnel ID provisioned by Anthropic and a cloudflared sidecar
   reachable on the worker host.
3. WIF: a Kubernetes projected service-account token for the worker
   pod, federated to Anthropic's tunnel-token issuer. The
   `mcp_tunnel_wif` helper drives the exchange; you only need to mount
   the token.
4. For each tunnel server: the matching `auth_token_env` populated.
   The runtime warns (does not crash) when a bearer is missing, but
   the upstream call will 401.
5. For high-risk workflows: a human approver wired into the approval
   queue (the validator refuses to load a high-risk workflow without
   an approval step).

## Customization checklist

Before you fork one of these templates, walk this list:

- Rename `agent_id` references in the multiagent roster to your own
  pre-published agent IDs (the `agent_amplitude_a11y_reviewer` etc.
  identifiers are placeholders).
- Swap `environment_id` to your real Anthropic environment.
- Update `metadata` so `work.list` filtering works for your fleet.
- Set the `target` on each `outcomes` entry to your acceptance bar.
- Audit `tools_enabled`; the templates ship a conservative default.
- For `risk_level: high` workflows, decide whether `network_access`
  should be off (Rogo template defaults to off because the analyst is
  read-only over private data).
- Replace the placeholder `*.anthropic.cloud` hostnames with your
  actual tunnel hostname.

## Production gotchas

- **Memory Stores are not compatible with self-hosted sandboxes.**
  Anthropic disclaims the combination; Sandcastle hard-errors at
  config-parse time (`MemoryStoresIncompatibleError`). All three of
  these templates use the Sandcastle Memory MCP behind an MCP tunnel
  instead - see `memory-mcp-via-tunnel.yaml` for the bare-bones shape.
- **AWS is unsupported as a self-hosted sandbox provider.** The
  supported set is exactly `cloudflare | daytona | modal | vercel |
  docker`. There is no `aws` enum value and the runtime refuses to
  fall back to a generic Docker host on EC2.
- **MCP tunnels are a gated preview.** Setting
  `SANDCASTLE_MCP_TUNNELS=1` is required and you must hold an
  Anthropic-provisioned tunnel ID. The runtime refuses to start the
  cloudflared sidecar otherwise.
- **High-risk workflows must include an approval step.** The DAG
  validator refuses to load `risk_level: high` workflows that have no
  `type: approval` step (EU AI Act Article 14).
- **Org-key leakage is fatal at startup.** Workers refuse to launch
  with `ANTHROPIC_API_KEY` set; only the environment-scoped key
  (`sk-ant-oat01-...`) is accepted, because the org key would be
  visible to every tool call inside the sandbox.
