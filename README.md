# Sandcastle

**Production-ready workflow orchestrator for AI agents. Built on [Sandstorm](https://github.com/tomascupr/sandstorm).**

[![Built on Sandstorm](https://img.shields.io/badge/Built%20on-Sandstorm-orange?style=flat-square)](https://github.com/tomascupr/sandstorm)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-51%20passing-brightgreen?style=flat-square)]()

<p align="center">
  <img src="docs/screenshots/overview.png" alt="Sandcastle Dashboard" width="720" />
</p>

---

## Why Sandcastle?

[Sandstorm](https://github.com/tomascupr/sandstorm) by [@tomascupr](https://github.com/tomascupr) is a brilliant piece of engineering - one API call, a full agent, completely sandboxed. It nails the core problem: giving agents full system access without worrying about what they do with it.

But sometimes you need to **build something lasting from the storm.**

Sandstorm gives you isolated, one-shot agent runs - fire a prompt, get a result, sandbox destroyed. That's perfect for a lot of things. But when you start building real products on top of it, you keep hitting the same walls:

- **"I need this agent to remember what it found yesterday."** - No persistence between runs.
- **"Agent A should feed its results into Agent B."** - No workflow orchestration.
- **"Bill the customer per enrichment, track costs per run."** - No usage metering.
- **"Alert me if the agent fails, retry automatically."** - No production error handling.
- **"Run this every 6 hours and notify me on Slack."** - No scheduling, no webhooks.

Sandcastle takes Sandstorm's sandboxed agent execution and wraps it in everything you need to ship agent-powered products to real customers.

> **Sandstorm** = the engine.
> **Sandcastle** = the product you build with it.

---

## Features

| Capability | Sandstorm | Sandcastle |
|---|---|---|
| Isolated agent execution | Yes | Yes (via Sandstorm) |
| Structured output & subagents | Yes | Yes |
| MCP servers & file uploads | Yes | Yes |
| **DAG workflow orchestration** | - | Yes |
| **Parallel step execution** | - | Yes |
| **Persistent storage (S3/MinIO)** | - | Yes |
| **Webhook callbacks (HMAC-signed)** | - | Yes |
| **Scheduled / cron agents** | - | Yes |
| **Retry logic with exponential backoff** | - | Yes |
| **Dead letter queue** | - | Yes |
| **Per-run cost tracking** | - | Yes |
| **SSE live streaming** | - | Yes |
| **Multi-tenant API keys** | - | Yes |
| **Dashboard with real-time monitoring** | - | Yes |

---

## Dashboard

Sandcastle ships with a full-featured dashboard built with React, TypeScript, and Tailwind CSS.

### Overview - KPI cards, run trends, cost breakdown per workflow

<p align="center">
  <img src="docs/screenshots/overview.png" alt="Overview" width="720" />
</p>

### Dark Mode

<p align="center">
  <img src="docs/screenshots/overview-dark.png" alt="Overview Dark" width="720" />
</p>

### Runs - Filterable run history with status, duration, and cost

<p align="center">
  <img src="docs/screenshots/runs.png" alt="Runs" width="720" />
</p>

### Run Detail - Step timeline with expandable output, live SSE stream for running agents

<p align="center">
  <img src="docs/screenshots/run-detail.png" alt="Run Detail" width="720" />
  <br/>
  <img src="docs/screenshots/run-detail-running.png" alt="Running Run with Parallel Steps" width="720" />
</p>

### Workflows - Manage workflows, preview DAG, trigger runs

<p align="center">
  <img src="docs/screenshots/workflows.png" alt="Workflows" width="720" />
</p>

### DAG Preview - Interactive visualization of step dependencies with model badges

<p align="center">
  <img src="docs/screenshots/dag-preview.png" alt="DAG Preview" width="720" />
</p>

### Schedules - Cron-based scheduling with enable/disable toggle

<p align="center">
  <img src="docs/screenshots/schedules.png" alt="Schedules" width="720" />
</p>

### Dead Letter Queue - Failed steps with retry and manual resolve actions

<p align="center">
  <img src="docs/screenshots/dead-letter.png" alt="Dead Letter Queue" width="720" />
</p>

---

## Quickstart

### Prerequisites

Everything Sandstorm needs, plus:
- **Redis** - job queue and scheduling
- **PostgreSQL** - run history, API keys, dead letter queue
- **S3-compatible storage** - persistent agent data (MinIO for local dev)

### Setup

```bash
git clone https://github.com/gizmax/Sandcastle.git
cd Sandcastle

cp .env.example .env   # configure your keys

# Install dependencies
uv sync

# Start infrastructure
docker-compose up -d redis postgres minio

# Run database migrations
uv run alembic upgrade head

# Start the API server
uv run python -m sandcastle serve

# Start the async worker (separate terminal)
uv run python -m sandcastle worker

# Start the dashboard (separate terminal)
cd dashboard && npm install && npm run dev
```

### Your First Workflow

```bash
# Run a workflow asynchronously
curl -X POST http://localhost:8000/workflows/run \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": "lead-enrichment",
    "input": {
      "target_url": "https://example.com",
      "max_depth": 3
    },
    "callback_url": "https://your-app.com/api/done"
  }'

# Response: { "run_id": "a1b2c3d4-...", "status": "queued" }
```

Or run synchronously and wait for the result:

```bash
curl -N -X POST http://localhost:8000/workflows/run/sync \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": "lead-enrichment",
    "input": { "target_url": "https://example.com" }
  }'
```

---

## Workflow Engine

Define multi-step agent pipelines as YAML. Each step can run in parallel, depend on previous steps, pass data forward, and use different models.

### Example: lead-enrichment.yaml

```yaml
name: "Lead Enrichment"
description: "Scrape, enrich, and score leads for sales outreach."
sandstorm_url: "${SANDSTORM_URL}"
default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: "scrape"
    prompt: |
      Visit {input.target_url} and extract:
      company name, employee count, main product, contact info.
      Return as structured JSON.
    output_schema:
      type: object
      properties:
        company_name: { type: string }
        employees: { type: integer }
        product: { type: string }
        contact_email: { type: string }

  - id: "enrich"
    depends_on: ["scrape"]
    prompt: |
      Given this company data: {steps.scrape.output}
      Research: revenue, industry, key decision makers, recent news.
    retry:
      max_attempts: 3
      backoff: exponential
      on_failure: abort

  - id: "score"
    depends_on: ["enrich"]
    prompt: |
      Score this lead 1-100 for B2B SaaS potential.
      Based on: {steps.enrich.output}
    model: haiku

on_complete:
  storage_path: "leads/{run_id}/result.json"
```

### Parallel Execution

Steps at the same DAG layer run concurrently. Use `parallel_over` to fan out over a list:

```yaml
steps:
  - id: "fetch-competitors"
    prompt: "Identify top 3 competitors for {input.company_url}."

  - id: "analyze"
    depends_on: ["fetch-competitors"]
    parallel_over: "steps.fetch-competitors.output.competitors"
    prompt: "Analyze {input._item} for pricing and feature changes."
    retry:
      max_attempts: 2
      backoff: exponential
      on_failure: skip

  - id: "summarize"
    depends_on: ["analyze"]
    prompt: "Create executive summary from: {steps.analyze.output}"
```

---

## API Reference

### Workflows

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/workflows` | List available workflows |
| `POST` | `/workflows` | Save new workflow YAML |
| `POST` | `/workflows/run` | Run workflow async (returns run_id) |
| `POST` | `/workflows/run/sync` | Run workflow sync (blocks until done) |

### Runs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/runs` | List runs (filterable by status, workflow, date, tenant) |
| `GET` | `/runs/{run_id}` | Get run detail with step statuses |
| `GET` | `/runs/{run_id}/stream` | SSE stream of live progress |

### Schedules

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/schedules` | Create cron schedule |
| `GET` | `/schedules` | List all schedules |
| `PATCH` | `/schedules/{id}` | Enable/disable schedule |
| `DELETE` | `/schedules/{id}` | Delete schedule |

### Dead Letter Queue

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/dead-letter` | List failed items |
| `POST` | `/dead-letter/{id}/retry` | Retry failed step |
| `POST` | `/dead-letter/{id}/resolve` | Mark as resolved |

### API Keys

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api-keys` | Create API key (returns plaintext once) |
| `GET` | `/api-keys` | List active keys |
| `DELETE` | `/api-keys/{id}` | Deactivate key |

### Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check (Sandstorm, DB, Redis) |
| `GET` | `/stats` | Aggregated stats and cost trends |

---

## Webhooks

Sandcastle signs all webhook payloads with HMAC-SHA256 so you can verify they're authentic:

```bash
curl -X POST http://localhost:8000/workflows/run \
  -d '{
    "workflow": "enrichment",
    "input": { "target_url": "https://example.com" },
    "callback_url": "https://your-app.com/api/done"
  }'
```

Your callback receives:

```json
{
  "run_id": "a1b2c3d4-...",
  "status": "completed",
  "outputs": { "lead_score": 87, "tier": "A" },
  "total_cost_usd": 0.12
}
```

With the header `X-Sandcastle-Signature` for HMAC verification.

---

## Scheduling

Run workflows on a cron schedule via API or dashboard:

```bash
curl -X POST http://localhost:8000/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_name": "competitor-monitor",
    "cron_expression": "0 */6 * * *",
    "input_data": { "company_url": "https://example.com" }
  }'
```

Powered by APScheduler with Redis-backed job storage.

---

## Cost Tracking

Every run automatically tracks Sandstorm execution costs per step:

```json
{
  "run_id": "a1b2c3d4-...",
  "workflow_name": "lead-enrichment",
  "total_cost_usd": 0.12,
  "steps": [
    { "step_id": "scrape", "cost_usd": 0.04, "duration_seconds": 12.3 },
    { "step_id": "enrich", "cost_usd": 0.05, "duration_seconds": 18.7 },
    { "step_id": "score", "cost_usd": 0.03, "duration_seconds": 8.2 }
  ]
}
```

The dashboard aggregates costs by workflow and shows daily trends.

---

## Error Handling

```yaml
steps:
  - id: scrape
    prompt: "..."
    retry:
      max_attempts: 3
      backoff: exponential    # 1s, 2s, 4s
      on_failure: skip        # skip | abort | fallback
```

Failed steps (after all retry attempts) land in the **Dead Letter Queue** where you can inspect the error, retry, or manually resolve them via the dashboard or API.

---

## Architecture

```
Your App ──POST /workflows/run──> Sandcastle API (FastAPI)
                                       |
                               ┌───────┴────────┐
                               │  Workflow Engine │
                               │  (DAG executor) │
                               └───────┬────────┘
                                       |
                     ┌─────────┬───────┴───────┬──────────┐
                     v         v               v          v
                Sandstorm  Sandstorm      Sandstorm   Sandstorm
                Agent A    Agent B        Agent C     Agent D
                (scrape)   (scrape)       (enrich)    (report)
                     |         |               |          |
                     v         v               v          v
                  E2B VM    E2B VM          E2B VM     E2B VM
                     |         |               |          |
                     └─────────┴───────┬───────┴──────────┘
                                       |
                     ┌─────────────────┼─────────────────┐
                     v                 v                  v
                PostgreSQL          Redis             S3 / MinIO
              (runs, keys,       (job queue,       (persistent
               dead letter)      scheduling)        storage)
                                       |
                               ┌───────┴────────┐
                               │  Webhook POST   │──> Your App
                               │  SSE Stream     │──> Dashboard
                               └────────────────┘
```

### Tech Stack

| Component | Technology |
|-----------|------------|
| API Server | Python 3.12, FastAPI, Uvicorn |
| Database | PostgreSQL 16 with SQLAlchemy async + Alembic |
| Job Queue | Redis 7 with arq |
| Scheduler | APScheduler with Redis store |
| Storage | S3 / MinIO |
| Agent Runtime | Sandstorm (E2B sandboxed) |
| Dashboard | React 18, TypeScript, Vite, Tailwind CSS v4 |
| DAG Visualization | @xyflow/react |
| Charts | Recharts |

---

## Configuration

All configuration via environment variables or `.env` file:

```bash
# Sandstorm connection
SANDSTORM_URL=http://localhost:8000
ANTHROPIC_API_KEY=sk-ant-...
E2B_API_KEY=e2b_...

# Database
DATABASE_URL=postgresql+asyncpg://sandcastle:sandcastle@localhost:5432/sandcastle

# Redis
REDIS_URL=redis://localhost:6379/0

# Storage
STORAGE_BACKEND=s3
STORAGE_BUCKET=sandcastle-data
STORAGE_ENDPOINT=http://localhost:9000
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin

# Security
WEBHOOK_SECRET=your-webhook-signing-secret
AUTH_REQUIRED=false

# Dashboard
DASHBOARD_ORIGIN=http://localhost:5173
WORKFLOWS_DIR=./workflows
LOG_LEVEL=info
```

---

## Development

```bash
# Run tests (51 passing)
uv run pytest

# Type check
cd dashboard && npx tsc --noEmit

# Dashboard dev server
cd dashboard && npm run dev
```

---

## Acknowledgements

Sandcastle would not exist without [**Sandstorm**](https://github.com/tomascupr/sandstorm) by [**@tomascupr**](https://github.com/tomascupr). Sandstorm is the core engine that powers every agent run in Sandcastle - we didn't reinvent it, we built on it. If you haven't already, go star the repo. It's one of the cleanest abstractions for sandboxed agent execution out there.

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
