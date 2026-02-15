# Sandcastle

**AI agent workflow orchestrator with DAG pipelines, parallel execution, policy enforcement, SLO-based model routing, human-in-the-loop approvals, cost tracking, and a real-time dashboard. Built on [Sandstorm](https://github.com/tomascupr/sandstorm).**

[![Built on Sandstorm](https://img.shields.io/badge/Built%20on-Sandstorm-orange?style=flat-square)](https://github.com/tomascupr/sandstorm)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-164%20passing-brightgreen?style=flat-square)]()
[![Live Demo](https://img.shields.io/badge/Live%20Demo-Dashboard-F59E0B?style=flat-square)](https://gizmax.github.io/Sandcastle/)

<p align="center">
  <a href="https://gizmax.github.io/Sandcastle/">
    <img src="docs/screenshots/overview-dark.png" alt="Sandcastle Dashboard" width="720" />
  </a>
</p>

<p align="center">
  <a href="https://gizmax.github.io/Sandcastle/"><strong>Try the Live Demo (no backend needed)</strong></a>
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

Sandcastle takes Sandstorm's sandboxed agent execution and adds the orchestration, persistence, and guardrails needed for production.

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
| **Run Time Machine (replay/fork)** | - | Yes |
| **Budget guardrails** | - | Yes |
| **Run cancellation** | - | Yes |
| **Idempotent run requests** | - | Yes |
| **Persistent storage (S3/MinIO)** | - | Yes |
| **Webhook callbacks (HMAC-signed)** | - | Yes |
| **Scheduled / cron agents** | - | Yes |
| **Retry logic with exponential backoff** | - | Yes |
| **Dead letter queue with full replay** | - | Yes |
| **Per-run cost tracking** | - | Yes |
| **SSE live streaming** | - | Yes |
| **Multi-tenant API keys** | - | Yes |
| **Dashboard with real-time monitoring** | - | Yes |
| **Visual workflow builder** | - | Yes |
| **Human approval gates** | - | Yes |
| **Self-optimizing workflows (AutoPilot)** | - | Yes |
| **Hierarchical workflows (workflow-as-step)** | - | Yes |
| **Policy engine (PII redaction, secret guard)** | - | Yes |
| **Cost-latency optimizer (SLO-based routing)** | - | Yes |

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

## Human Approval Gates

Pause any workflow at a critical step and wait for human review before continuing. Define approval steps in YAML, set timeouts with auto-actions (skip or abort), and approve/reject/skip via API or dashboard. Reviewers can edit the request data before approving. Webhook notifications fire when approval is needed.

```yaml
steps:
  - id: "generate-report"
    prompt: "Generate quarterly report..."

  - id: "review"
    type: approval
    depends_on: ["generate-report"]
    approval_config:
      message: "Review the generated report before sending to client"
      timeout_hours: 24
      on_timeout: abort
      allow_edit: true

  - id: "send"
    depends_on: ["review"]
    prompt: "Send the approved report to {input.client_email}"
```

---

## Self-Optimizing Workflows (AutoPilot)

A/B test different models, prompts, and configurations for any step. Sandcastle automatically runs variants, evaluates quality (via LLM judge or schema completeness), tracks cost and latency, and picks the best-performing variant. Supports quality, cost, latency, and pareto optimization targets.

```yaml
steps:
  - id: "enrich"
    prompt: "Enrich this lead: {input.company}"
    autopilot:
      enabled: true
      optimize_for: quality
      min_samples: 20
      auto_deploy: true
      variants:
        - id: fast
          model: haiku
        - id: quality
          model: opus
          prompt: "Thoroughly research and enrich: {input.company}"
      evaluation:
        method: llm_judge
        criteria: "Rate completeness, accuracy, and depth 1-10"
```

---

## Hierarchical Workflows (Workflow-as-Step)

Call one workflow from another. Parent workflows can pass data to children via input mapping, collect results via output mapping, and fan out over lists with configurable concurrency. Depth limiting prevents runaway recursion.

```yaml
steps:
  - id: "find-leads"
    prompt: "Find 10 leads in {input.industry}"

  - id: "enrich-each"
    type: sub_workflow
    depends_on: ["find-leads"]
    sub_workflow:
      workflow: lead-enrichment
      input_mapping:
        company: steps.find-leads.output.company
      output_mapping:
        result: enriched_data
      max_concurrent: 5
      timeout: 600

  - id: "summarize"
    depends_on: ["enrich-each"]
    prompt: "Summarize enrichment results: {steps.enrich-each.output}"
```

---

## Policy Engine

Declarative rules evaluated against every step output. Detect PII, block secrets, inject dynamic approval gates, or alert on suspicious patterns - all defined in YAML. Policies can be global (apply to all steps) or scoped per step.

```yaml
policies:
  - id: pii-redact
    description: "Redact personal data from outputs"
    severity: high
    trigger:
      type: pattern
      patterns:
        - type: builtin
          name: email
        - type: builtin
          name: phone
        - type: builtin
          name: ssn
    action:
      type: redact

  - id: cost-guard
    description: "Block steps that are too expensive"
    severity: critical
    trigger:
      type: condition
      expression: "step_cost > 2.0"
    action:
      type: block
      message: "Step exceeded $2 cost limit"

steps:
  - id: "research"
    prompt: "Research {input.company}"
    policies: ["pii-redact", "cost-guard"]

  - id: "internal-only"
    prompt: "Prepare internal report..."
    policies: []  # skip all policies for this step
```

Built-in patterns for email, phone, SSN, and credit card numbers. Custom regex patterns supported. Condition triggers use safe expression evaluation - no arbitrary code execution.

---

## Cost-Latency Optimizer

SLO-based dynamic model routing. Define quality, cost, and latency constraints per step, and Sandcastle automatically selects the best model from a pool based on historical performance data. Budget pressure detection forces cheaper models when spending approaches limits.

```yaml
steps:
  - id: "enrich"
    prompt: "Enrich data for {input.company}"
    slo:
      quality_min: 0.7
      cost_max_usd: 0.15
      latency_max_seconds: 60
      optimize_for: cost
    model_pool:
      - id: fast-cheap
        model: haiku
        max_turns: 5
      - id: balanced
        model: sonnet
        max_turns: 10
      - id: thorough
        model: opus
        max_turns: 20

  - id: "classify"
    prompt: "Classify the enriched data"
    slo:
      quality_min: 0.8
      optimize_for: quality
    # No model_pool - auto-generates haiku/sonnet/opus pool
```

The optimizer scores each model option across multiple objectives, filters out options that violate SLO constraints, and tracks confidence based on sample count. Cold starts default to a balanced middle option until enough data is collected.

---

## Run Time Machine

Every completed step saves a checkpoint. When something goes wrong - or you just want to try a different approach - you don't have to start over.

**Replay** - Re-run from any step. Sandcastle loads the checkpoint from just before that step and continues execution. All prior steps are skipped, their outputs restored from the checkpoint. Costs only what's re-executed.

**Fork** - Same as replay, but you change something first. Swap the model from Haiku to Opus. Rewrite the prompt. Adjust max_turns. The new run branches off with your changes and Sandcastle tracks the full lineage.

```bash
# Replay from the "enrich" step
curl -X POST http://localhost:8080/runs/{run_id}/replay \
  -H "Content-Type: application/json" \
  -d '{ "from_step": "enrich" }'

# Fork with a different model
curl -X POST http://localhost:8080/runs/{run_id}/fork \
  -H "Content-Type: application/json" \
  -d '{
    "from_step": "score",
    "changes": { "model": "opus", "prompt": "Score more conservatively..." }
  }'
```

---

## Budget Guardrails

Set a spending limit per run, per tenant, or as a global default. Sandcastle checks the budget after every step:

- **80%** - Warning logged, execution continues
- **100%** - Hard stop, status = `budget_exceeded`

Budget resolution order: request `max_cost_usd` > tenant API key limit > `DEFAULT_MAX_COST_USD` env var.

```bash
curl -X POST http://localhost:8080/workflows/run \
  -d '{ "workflow": "enrichment", "input": {...}, "max_cost_usd": 0.50 }'
```

---

## Dashboard

Sandcastle ships with a full-featured dashboard built with React, TypeScript, and Tailwind CSS. Dark and light theme, real-time updates, and zero configuration - just `npm run dev`.

### Overview

KPI cards, 30-day run trends, cost breakdown per workflow, recent runs at a glance.

<p align="center">
  <img src="docs/screenshots/overview-dark.png" alt="Overview - Dark Mode" width="720" />
</p>

<details>
<summary>Light mode</summary>
<p align="center">
  <img src="docs/screenshots/overview-light.png" alt="Overview - Light Mode" width="720" />
</p>
</details>

### Runs

Filterable run history with status badges, duration, cost per run. Auto-refreshes every 5 seconds for active runs.

<p align="center">
  <img src="docs/screenshots/runs.png" alt="Runs" width="720" />
</p>

### Run Detail - Completed with Budget Bar

Step-by-step timeline with expandable outputs, per-step cost and duration. Budget bar shows how close a run got to its spending limit.

<p align="center">
  <img src="docs/screenshots/run-detail.png" alt="Run Detail with Budget Bar" width="720" />
</p>

### Run Detail - Failed with Replay & Fork

When a step fails, expand it to see the full error, retry count, and two powerful recovery options: **Replay from here** re-runs from that step with the same context. **Fork from here** lets you change the prompt, model, or parameters before re-running.

<p align="center">
  <img src="docs/screenshots/run-detail-failed.png" alt="Failed Run with Replay and Fork" width="720" />
</p>

### Run Detail - Running with Parallel Steps

Live view of a running workflow showing parallel step execution. Steps with a pulsing blue dot are currently executing inside Sandstorm sandboxes.

<p align="center">
  <img src="docs/screenshots/run-detail-running.png" alt="Running Workflow with Parallel Steps" width="720" />
</p>

### Run Lineage

When you replay or fork a run, Sandcastle tracks the full lineage. The run detail page shows the parent-child relationship so you can trace exactly how you got here.

<p align="center">
  <img src="docs/screenshots/run-detail-replay.png" alt="Run Lineage Tree" width="720" />
</p>

### Workflows

Grid of workflow cards with step count, descriptions, and quick-action buttons. Click "Run" to trigger a workflow with custom input and budget limits.

<p align="center">
  <img src="docs/screenshots/workflows.png" alt="Workflows" width="720" />
</p>

### Visual DAG Preview

Click "DAG" on any workflow card to expand an interactive graph of all steps, their dependencies, and assigned models. Powered by React Flow.

<p align="center">
  <img src="docs/screenshots/dag-preview.png" alt="DAG Preview" width="720" />
</p>

### Workflow Builder

Visual drag-and-drop editor for building workflows. Add steps, connect dependencies, configure models and timeouts, then preview the generated YAML. Editing an existing workflow loads its steps and edges into the canvas.

<p align="center">
  <img src="docs/screenshots/workflow-builder.png" alt="Workflow Builder" width="720" />
</p>

### Schedules

Cron-based scheduling with human-readable descriptions, enable/disable toggle, and links to the last triggered run.

<p align="center">
  <img src="docs/screenshots/schedules.png" alt="Schedules" width="720" />
</p>

### Dead Letter Queue

Failed steps that exhausted all retries land here. Retry triggers a full re-run. Resolve marks the issue as handled. Sidebar badge shows unresolved count.

<p align="center">
  <img src="docs/screenshots/dead-letter.png" alt="Dead Letter Queue" width="720" />
</p>

### Approval Gates

Any workflow step can pause execution and wait for human review before continuing. The approvals page shows all pending, approved, rejected, and skipped gates with filterable tabs. Each pending approval has Approve, Reject, and Skip buttons. Configurable timeouts auto-resolve approvals if nobody responds. Webhook notifications fire when approval is needed.

<p align="center">
  <img src="docs/screenshots/approvals.png" alt="Approval Gates" width="720" />
</p>

<details>
<summary>Expanded with request data</summary>

Click any approval to expand it and see the full request data the agent produced. If `allow_edit` is enabled, reviewers can modify the data before approving - giving humans final control over what the next step receives.

<p align="center">
  <img src="docs/screenshots/approvals-detail.png" alt="Approval Gate Detail" width="720" />
</p>
</details>

### AutoPilot - Self-Optimizing Workflows

A/B test different models, prompts, and configurations on any workflow step. Sandcastle automatically runs variants, evaluates quality (LLM judge or schema completeness), and tracks cost vs latency vs quality. Stats cards show active experiments, total samples collected, average quality improvement, and total cost savings. Once enough samples are collected, the best-performing variant is auto-deployed.

<p align="center">
  <img src="docs/screenshots/autopilot.png" alt="AutoPilot Experiments" width="720" />
</p>

<details>
<summary>Expanded with variant comparison</summary>

Expand an experiment to see the variant comparison table. Each variant shows sample count, average quality score (color-coded), average cost, and average duration. The "BEST" badge highlights the current leader. Deploy any variant manually, or let AutoPilot pick the winner automatically based on your optimization target (quality, cost, latency, or pareto).

<p align="center">
  <img src="docs/screenshots/autopilot-detail.png" alt="AutoPilot Variant Comparison" width="720" />
</p>
</details>

### Policy Violations

Every policy trigger is logged with severity, action taken, and full context. Stats cards show 30-day totals, critical and high counts, and the most-triggered policy. Filter by severity (Critical, High, Medium, Low). Color-coded badges show what action was taken - blocked, redacted, flagged, or logged. Green checkmark indicates the output was automatically modified.

<p align="center">
  <img src="docs/screenshots/violations.png" alt="Policy Violations" width="720" />
</p>

<details>
<summary>Expanded with trigger details</summary>

Click any violation to expand and see the full trigger details - what pattern matched, what was detected, and what action was taken. Includes links to the originating run and step for quick investigation.

<p align="center">
  <img src="docs/screenshots/violations-detail.png" alt="Violation Detail" width="720" />
</p>
</details>

### Cost-Latency Optimizer

Real-time view of the optimizer's model routing decisions. Stats cards show total decisions, average confidence, top model with distribution percentage, and estimated savings. Each decision shows the selected model as a color-coded badge, a confidence bar, and the reasoning. Budget pressure indicators pulse red when spending approaches limits.

<p align="center">
  <img src="docs/screenshots/optimizer.png" alt="Cost-Latency Optimizer" width="720" />
</p>

<details>
<summary>Expanded with alternatives and SLO config</summary>

Expand a decision to see the full alternatives table with scores, and the SLO configuration that drove the selection. The "SELECTED" badge highlights which model won.

<p align="center">
  <img src="docs/screenshots/optimizer-detail.png" alt="Optimizer Decision Detail" width="720" />
</p>
</details>

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
| `GET` | `/runs/{id}` | Get run detail with step statuses |
| `GET` | `/runs/{id}/stream` | SSE stream of live progress |
| `POST` | `/runs/{id}/cancel` | Cancel a running workflow |
| `POST` | `/runs/{id}/replay` | Replay from a specific step |
| `POST` | `/runs/{id}/fork` | Fork from a step with overrides |

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
| `POST` | `/dead-letter/{id}/retry` | Retry failed step (full replay) |
| `POST` | `/dead-letter/{id}/resolve` | Mark as resolved |

### Approval Gates

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/approvals` | List approvals (filterable by status) |
| `GET` | `/approvals/{id}` | Get approval detail with request data |
| `POST` | `/approvals/{id}/approve` | Approve (optional edit + comment) |
| `POST` | `/approvals/{id}/reject` | Reject and fail the run |
| `POST` | `/approvals/{id}/skip` | Skip step and continue workflow |

### AutoPilot

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/autopilot/experiments` | List experiments |
| `GET` | `/autopilot/experiments/{id}` | Experiment detail with samples + stats |
| `POST` | `/autopilot/experiments/{id}/deploy` | Manually deploy a winning variant |
| `POST` | `/autopilot/experiments/{id}/reset` | Reset experiment |
| `GET` | `/autopilot/stats` | Savings and quality overview |

### Policy Engine

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/violations` | List policy violations (filterable) |
| `GET` | `/violations/stats` | Violation stats by severity, policy, day |
| `GET` | `/runs/{id}/violations` | Violations for a specific run |

### Optimizer

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/optimizer/decisions` | List routing decisions |
| `GET` | `/optimizer/decisions/{run_id}` | Decisions for a specific run |
| `GET` | `/optimizer/stats` | Model distribution, confidence, savings |

### API Keys

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api-keys` | Create API key (returns plaintext once) |
| `GET` | `/api-keys` | List active keys (prefix only) |
| `DELETE` | `/api-keys/{id}` | Deactivate key |

### Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check (Sandstorm, DB, Redis) |
| `GET` | `/stats` | Aggregated stats and cost trends |

All responses follow the envelope format: `{ "data": ..., "error": null }` or `{ "data": null, "error": { "code": "...", "message": "..." } }`.

---

## Multi-Tenant Auth

Sandcastle supports strict multi-tenant isolation via API keys. Every API key maps to a `tenant_id`, and all queries are automatically scoped.

```bash
# Create an API key
curl -X POST http://localhost:8080/api-keys \
  -d '{ "tenant_id": "acme-corp", "name": "Production" }'
# Returns: { "data": { "key": "sc_abc123...", "key_prefix": "sc_abc12" } }

# Use it
curl http://localhost:8080/runs -H "X-API-Key: sc_abc123..."
# Only sees runs belonging to acme-corp
```

Toggle with `AUTH_REQUIRED=true|false` (default: false for local dev). When enabled, all endpoints except `/health` require a valid API key.

---

## Webhooks

Sandcastle signs all webhook payloads with HMAC-SHA256:

```json
{
  "run_id": "a1b2c3d4-...",
  "status": "completed",
  "outputs": { "lead_score": 87, "tier": "A" },
  "total_cost_usd": 0.12
}
```

Header: `X-Sandcastle-Signature` for verification against your `WEBHOOK_SECRET`.

---

## Architecture

```
Your App --POST /workflows/run--> Sandcastle API (FastAPI)
                                       |
                               +-------+--------+
                               |  Workflow Engine |
                               |  (DAG executor) |
                               +-------+--------+
                                       |
                            +----------+-----------+
                            |                      |
                     Standard Steps          Sub-Workflow Steps
                            |              (recursive execution)
                     +------+------+               |
                     v      v      v        +------+------+
                 Sandstorm (parallel)       | Child Engine |
                  Agent A  Agent B  ...     +------+------+
                     |      |                      |
                     v      v               Sandstorm (child)
                  E2B VMs                       |
                     |                       E2B VMs
                     |                          |
          +----[Approval Gate?]----+            |
          |                        |            |
        Pause               Continue            |
     (wait for              (auto)              |
      human)                   |                |
          |          [AutoPilot?]               |
          v          Pick variant               |
     Approve/          |                        |
     Reject/     Evaluate quality               |
     Skip              |                        |
                 [Policy Engine]                 |
                 PII redact /                    |
                 block / alert                   |
                       |                        |
                 [SLO Optimizer]                |
                 Route to best                  |
                 model by SLO                   |
                       +-----------+------------+
                                   |
                  +----------------+-----------------+
                  v                v                  v
             PostgreSQL          Redis            S3 / MinIO
           (runs, keys,       (job queue,       (persistent
            approvals,        cancel flags,      storage)
            experiments,      scheduling)
            violations,
            routing,
            checkpoints)
                                   |
                           +-------+--------+
                           |  Webhook POST   |--> Your App
                           |  SSE Stream     |--> Dashboard
                           +----------------+
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
curl -X POST http://localhost:8080/workflows/run \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": "lead-enrichment",
    "input": {
      "target_url": "https://example.com",
      "max_depth": 3
    },
    "callback_url": "https://your-app.com/api/done"
  }'

# Response: { "data": { "run_id": "a1b2c3d4-...", "status": "queued" } }
```

Or run synchronously and wait for the result:

```bash
curl -X POST http://localhost:8080/workflows/run/sync \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": "lead-enrichment",
    "input": { "target_url": "https://example.com" }
  }'
```

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
DEFAULT_MAX_COST_USD=0    # 0 = no global budget limit
MAX_WORKFLOW_DEPTH=5      # max recursion depth for hierarchical workflows

# Dashboard
DASHBOARD_ORIGIN=http://localhost:5173
WORKFLOWS_DIR=./workflows
LOG_LEVEL=info
```

---

## Development

```bash
# Run tests (164 passing)
uv run pytest

# Type check backend
uv run mypy src/

# Type check frontend
cd dashboard && npx tsc --noEmit

# Dashboard dev server (starts with demo data when backend is offline)
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
