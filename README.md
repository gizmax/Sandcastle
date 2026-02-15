# 🏰 Sandcastle

**Production-ready AI agent workflows. Built on the shoulders of [Sandstorm](https://github.com/tomascupr/sandstorm).**

[![Built on Sandstorm](https://img.shields.io/badge/Built%20on-Sandstorm-orange?style=flat-square)](https://github.com/tomascupr/sandstorm)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

## Why Sandcastle?

[Sandstorm](https://github.com/tomascupr/sandstorm) by [@tomascupr](https://github.com/tomascupr) is a brilliant piece of engineering - one API call, a full AI agent, completely sandboxed. It nails the core problem: giving AI agents full system access without worrying about what they do with it. We love it. We use it. You should too.

But sometimes you need to **build something lasting from the storm.**

Sandstorm gives you isolated, one-shot agent runs - fire a prompt, get a result, sandbox destroyed. That's perfect for a lot of things. But when we started building real products on top of it, we kept hitting the same walls:

- **"I need this agent to remember what it found yesterday."** → No persistence between runs.
- **"Agent A should feed its results into Agent B."** → No workflow orchestration.
- **"Bill the customer per enrichment, track costs per run."** → No usage metering.
- **"Alert me if the agent fails, retry automatically."** → No production error handling.
- **"Run this every 6 hours and notify me on Slack."** → No scheduling, no webhooks.

Sandcastle takes Sandstorm's sandboxed agent execution and wraps it in everything you need to ship agent-powered products to real customers.

> **Sandstorm** = the engine.
> **Sandcastle** = the product you build with it.

---

## What Sandcastle Adds

| Capability | Sandstorm | Sandcastle |
|---|---|---|
| Isolated agent execution | ✅ | ✅ (via Sandstorm) |
| Structured output | ✅ | ✅ |
| Subagents | ✅ | ✅ |
| MCP servers | ✅ | ✅ |
| File uploads | ✅ | ✅ |
| **DAG workflow orchestration** | - | ✅ |
| **Persistent storage between runs** | - | ✅ |
| **Webhook callbacks** | - | ✅ |
| **Scheduled / cron agents** | - | ✅ |
| **Retry logic & dead letter queue** | - | ✅ |
| **Per-run cost tracking** | - | ✅ |
| **Multi-tenant API keys & billing** | - | ✅ |
| **Dashboard & run history** | - | ✅ |

---

## Quickstart

### Prerequisites

Everything Sandstorm needs, plus:
- Redis (for job queue & scheduling)
- PostgreSQL (for run history, billing, storage)
- S3-compatible storage (for persistent agent data)

### Setup

```bash
git clone https://github.com/gizmax/Sandcastle.git
cd Sandcastle

cp .env.example .env   # configure your keys

# Install dependencies
uv sync

# Start services
docker-compose up -d redis postgres minio

# Run migrations
uv run python -m sandcastle db migrate

# Start the server
uv run python -m sandcastle serve
```

### Your First Workflow

```bash
curl -N -X POST http://localhost:8000/workflows/run \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": "enrichment",
    "input": {
      "companies": [
        {"name": "Stripe", "website": "stripe.com"},
        {"name": "Vercel", "website": "vercel.com"}
      ]
    },
    "callback_url": "https://your-app.com/api/done"
  }'
```

---

## Workflow Engine

Define multi-step agent pipelines where each step can run in parallel, depend on previous steps, and pass data forward.

### sandcastle.yaml

```yaml
name: lead-enrichment
description: Enrich a list of companies with firmographic data

steps:
  - id: scrape
    prompt: |
      Scrape {company.website} and extract:
      company description, team size, tech stack, pricing model.
      Return structured JSON.
    parallel_over: input.companies
    model: sonnet
    max_turns: 10
    output_schema:
      type: object
      properties:
        description: { type: string }
        employee_estimate: { type: string }
        tech_stack: { type: array, items: { type: string } }
        pricing_model: { type: string }

  - id: enrich
    depends_on: [scrape]
    prompt: |
      Given this company data: {steps.scrape.output}
      Find additional info: funding rounds, key contacts (CEO, CTO),
      LinkedIn URLs, recent news. Return structured JSON.
    model: sonnet
    max_turns: 15
    output_schema:
      type: object
      properties:
        funding_total_usd: { type: number }
        last_round: { type: string }
        contacts: { type: array }
        recent_news: { type: array }

  - id: score
    depends_on: [scrape, enrich]
    prompt: |
      Score this lead from 1-100 based on:
      {steps.scrape.output} and {steps.enrich.output}
      Consider: company size, funding, tech stack fit.
    model: haiku
    max_turns: 3

on_complete:
  webhook: ${CALLBACK_URL}
  storage: s3://sandcastle-data/enrichments/{run_id}/

schedule: null  # or "0 */6 * * *" for every 6 hours
```

---

## Persistence

Agents can read and write to shared storage that survives between runs:

```yaml
steps:
  - id: compare
    prompt: |
      Load yesterday's prices from {storage.prices/latest.json}.
      Scrape current prices. Compare and report changes.
      Save current prices to {storage.prices/latest.json}.
    storage:
      read: [prices/latest.json]
      write: [prices/latest.json, reports/diff-{date}.json]
```

Storage backends: S3, R2 (Cloudflare), MinIO (local), or PostgreSQL for small data.

---

## Webhooks & Callbacks

Don't hold a connection open. Fire and forget:

```bash
curl -X POST http://localhost:8000/workflows/run \
  -d '{
    "workflow": "enrichment",
    "input": { ... },
    "callback_url": "https://your-app.com/api/enrichment-done",
    "callback_headers": { "Authorization": "Bearer your-token" }
  }'

# Response: { "run_id": "run_abc123", "status": "queued" }
# When done, Sandcastle POSTs results to your callback_url
```

---

## Scheduling

Run agents on a cron schedule:

```yaml
# sandcastle.yaml
schedule: "0 8 * * 1-5"  # Every weekday at 8am

# or via API:
curl -X POST http://localhost:8000/schedules \
  -d '{
    "workflow": "competitor-monitor",
    "cron": "0 */6 * * *",
    "notify": { "slack_webhook": "https://hooks.slack.com/..." }
  }'
```

---

## Cost Tracking & Billing

Every run tracks costs automatically:

```json
{
  "run_id": "run_abc123",
  "workflow": "enrichment",
  "costs": {
    "e2b_sandbox_seconds": 145,
    "e2b_cost_usd": 0.02,
    "anthropic_input_tokens": 12500,
    "anthropic_output_tokens": 3200,
    "anthropic_cost_usd": 0.08,
    "total_cost_usd": 0.10
  },
  "tenant_id": "customer_xyz",
  "billed": true
}
```

Use this to build usage-based billing for your customers.

---

## Error Handling

```yaml
# Per-step retry configuration
steps:
  - id: scrape
    prompt: "..."
    retry:
      max_attempts: 3
      backoff: exponential  # 1s, 2s, 4s
      on_failure: skip      # skip | abort | fallback

    fallback:
      prompt: "Web scraping failed. Use web search to find basic info about {company.name}."

# Global: failed runs go to dead letter queue
on_failure:
  dead_letter: true
  notify:
    slack_webhook: ${SLACK_WEBHOOK}
    email: ops@yourcompany.com
```

---

## Dashboard

```
http://localhost:8000/dashboard
```

- Run history with status, duration, cost
- Live streaming of active agents
- Error logs and retry history
- Cost breakdown per workflow, per tenant
- Schedule management

---

## Architecture

```
Your App ──POST /workflows/run──▶ Sandcastle API
                                      │
                              ┌───────┴────────┐
                              │  Workflow Engine │
                              │  (DAG executor) │
                              └───────┬────────┘
                                      │
                    ┌─────────┬───────┴───────┬──────────┐
                    ▼         ▼               ▼          ▼
               Sandstorm  Sandstorm      Sandstorm   Sandstorm
               Agent A    Agent B        Agent C     Agent D
               (scrape)   (scrape)       (enrich)    (report)
                    │         │               │          │
                    ▼         ▼               ▼          ▼
                 E2B VM    E2B VM          E2B VM     E2B VM
                    │         │               │          │
                    └─────────┴───────┬───────┴──────────┘
                                      │
                              ┌───────┴────────┐
                              │   PostgreSQL    │ ◀── run history, billing
                              │   Redis         │ ◀── job queue, scheduling
                              │   S3 / R2       │ ◀── persistent storage
                              └────────────────┘
                                      │
                              ┌───────┴────────┐
                              │  Webhook POST   │──▶ Your App
                              │  or Dashboard   │──▶ Browser
                              └────────────────┘
```

---

## Acknowledgements

Sandcastle would not exist without [**Sandstorm**](https://github.com/tomascupr/sandstorm) by [**@tomascupr**](https://github.com/tomascupr). Sandstorm is the core engine that powers every agent run in Sandcastle - we didn't reinvent it, we built on it. If you haven't already, go star the repo. It's one of the cleanest abstractions for sandboxed AI agent execution out there.

Created by [**Tomas Pflanzer**](https://github.com/gizmax) ([@gizmax](https://github.com/gizmax)).

Sandcastle uses Sandstorm as a dependency and extends it with orchestration, persistence, and production infrastructure. All original Sandstorm code remains under its [MIT license](https://github.com/tomascupr/sandstorm/blob/main/LICENSE).

---

## License

[MIT](LICENSE)

---

<p align="center">
  <strong>Sandstorm</strong> gives you the storm.<br>
  <strong>Sandcastle</strong> lets you build.
</p>
